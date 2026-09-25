// Package testserver is an in-process Memco service for tests: a real gRPC
// server on a loopback port that records what it is sent and answers with
// defaults, staged responses or staged failures.
package testserver

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"sync"
	"testing"
	"time"

	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	authv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/auth/v1"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

// Methods are the memory service's RPC names, in contract order.
var Methods = []string{
	"ListDomains", "StartSession", "Search", "GetMemory", "CreateMemory",
	"EnrichMemory", "ShareFeedback", "RevertMemory", "ImportMemories", "ListTools",
}

// Failure is a status to answer with. Reason and Domain, when set, attach a
// google.rpc.ErrorInfo detail carrying Metadata.
type Failure struct {
	Code     codes.Code
	Details  string
	Reason   string
	Domain   string
	Metadata map[string]string
}

func (f Failure) err() error {
	st := status.New(f.Code, f.Details)
	if f.Reason == "" && f.Domain == "" {
		return st.Err()
	}
	detailed, err := st.WithDetails(&errdetails.ErrorInfo{Reason: f.Reason, Domain: f.Domain, Metadata: f.Metadata})
	if err != nil {
		panic(err)
	}
	return detailed.Err()
}

// holdLimit bounds a held call, so a test that fails before releasing it
// cannot keep the server open.
const holdLimit = 10 * time.Second

// Hold keeps every call to one method in flight until it is released.
type Hold struct {
	arrived, released chan struct{}
	arrive, release   sync.Once
}

// Arrived is closed once a held call has reached the server.
func (h *Hold) Arrived() <-chan struct{} { return h.arrived }

// Release lets the held calls answer.
func (h *Hold) Release() { h.release.Do(func() { close(h.released) }) }

// Call is one call a harness's services received.
type Call struct {
	Method string
	// Authorization holds every authorization entry the call carried.
	Authorization []string
	Request       proto.Message
}

// journal is every call a harness's services received, in arrival order.
type journal struct {
	mu    sync.Mutex
	calls []Call
}

func (j *journal) note(ctx context.Context, method string, request proto.Message) {
	md, _ := metadata.FromIncomingContext(ctx)
	j.mu.Lock()
	defer j.mu.Unlock()
	j.calls = append(j.calls, Call{Method: method, Authorization: md.Get("authorization"), Request: proto.Clone(request)})
}

// recorder records what one service was sent, and answers as staged.
type recorder struct {
	journal *journal

	mu        sync.Mutex
	calls     []string
	metadata  []metadata.MD
	received  []proto.Message
	requests  map[string]proto.Message
	responses map[string]proto.Message
	failure   *Failure
	transient map[string][]Failure
	delays    map[string]time.Duration
	holds     map[string]*Hold
}

func newRecorder(j *journal) recorder {
	return recorder{
		journal:   j,
		requests:  map[string]proto.Message{},
		responses: map[string]proto.Message{},
		transient: map[string][]Failure{},
		delays:    map[string]time.Duration{},
		holds:     map[string]*Hold{},
	}
}

// Calls returns the method names called so far, including calls that failed.
func (r *recorder) Calls() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.calls...)
}

// Metadata returns the metadata each call arrived with, in call order.
func (r *recorder) Metadata() []metadata.MD {
	r.mu.Lock()
	defer r.mu.Unlock()
	copied := make([]metadata.MD, len(r.metadata))
	for i, md := range r.metadata {
		copied[i] = md.Copy()
	}
	return copied
}

// Received returns every request, in call order, index-aligned with Calls
// and Metadata.
func (r *recorder) Received() []proto.Message {
	r.mu.Lock()
	defer r.mu.Unlock()
	copied := make([]proto.Message, len(r.received))
	for i, request := range r.received {
		copied[i] = proto.Clone(request)
	}
	return copied
}

// Request returns the last request the named method received, or nil.
func (r *recorder) Request(method string) proto.Message {
	r.mu.Lock()
	defer r.mu.Unlock()
	if request, ok := r.requests[method]; ok {
		return proto.Clone(request)
	}
	return nil
}

// Respond stages the response the named method answers with.
func (r *recorder) Respond(method string, response proto.Message) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.responses[method] = response
}

// Fail makes every method answer with f; nil restores normal answers.
func (r *recorder) Fail(f *Failure) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.failure = f
}

// FailNext queues failures the named method serves once each before answering
// normally.
func (r *recorder) FailNext(method string, failures ...Failure) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.transient[method] = append(r.transient[method], failures...)
}

// Delay makes the named method wait before answering, or until its call ends.
func (r *recorder) Delay(method string, d time.Duration) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.delays[method] = d
}

// Hold keeps the named method's calls in flight until the hold is released.
func (r *recorder) Hold(method string) *Hold {
	r.mu.Lock()
	defer r.mu.Unlock()
	hold := &Hold{arrived: make(chan struct{}), released: make(chan struct{})}
	r.holds[method] = hold
	return hold
}

// Forget drops everything recorded so far and keeps what was staged.
func (r *recorder) Forget() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls = nil
	r.metadata = nil
	r.received = nil
	r.requests = map[string]proto.Message{}
}

// record notes the call before anything can fail it, so tests can count the
// attempts a retry made.
func (r *recorder) record(ctx context.Context, method string, request proto.Message) (time.Duration, *Hold, error) {
	r.journal.note(ctx, method, request)
	r.mu.Lock()
	defer r.mu.Unlock()
	md, _ := metadata.FromIncomingContext(ctx)
	r.calls = append(r.calls, method)
	r.metadata = append(r.metadata, md.Copy())
	r.received = append(r.received, proto.Clone(request))
	r.requests[method] = proto.Clone(request)
	delay, hold := r.delays[method], r.holds[method]
	if queued := r.transient[method]; len(queued) > 0 {
		r.transient[method] = queued[1:]
		return delay, hold, queued[0].err()
	}
	if r.failure != nil {
		return delay, hold, r.failure.err()
	}
	return delay, hold, nil
}

func (r *recorder) staged(method string) proto.Message {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.responses[method]
}

// wait sleeps for d, or until the call ends.
func wait(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return status.FromContextError(ctx.Err()).Err()
	}
}

func answer[Req, Resp proto.Message](ctx context.Context, r *recorder, method string, request Req, fallback func(Req) (Resp, error)) (Resp, error) {
	var zero Resp
	delay, hold, failure := r.record(ctx, method, request)
	if hold != nil {
		hold.arrive.Do(func() { close(hold.arrived) })
		select {
		case <-hold.released:
		case <-ctx.Done():
			return zero, status.FromContextError(ctx.Err()).Err()
		case <-time.After(holdLimit):
		}
	}
	if delay > 0 {
		if err := wait(ctx, delay); err != nil {
			return zero, err
		}
	}
	if failure != nil {
		return zero, failure
	}
	if staged := r.staged(method); staged != nil {
		return proto.Clone(staged).(Resp), nil
	}
	return fallback(request)
}

// Memory is the fake memory service.
type Memory struct {
	memoryv1.UnimplementedMemoryServiceServer
	recorder
}

func (m *Memory) ListDomains(ctx context.Context, r *memoryv1.ListDomainsRequest) (*memoryv1.ListDomainsResponse, error) {
	return answer(ctx, &m.recorder, "ListDomains", r, func(*memoryv1.ListDomainsRequest) (*memoryv1.ListDomainsResponse, error) {
		return &memoryv1.ListDomainsResponse{}, nil
	})
}

func (m *Memory) StartSession(ctx context.Context, r *memoryv1.StartSessionRequest) (*memoryv1.StartSessionResponse, error) {
	return answer(ctx, &m.recorder, "StartSession", r, func(*memoryv1.StartSessionRequest) (*memoryv1.StartSessionResponse, error) {
		return &memoryv1.StartSessionResponse{SessionId: "session-a"}, nil
	})
}

func (m *Memory) Search(ctx context.Context, r *memoryv1.SearchRequest) (*memoryv1.SearchResponse, error) {
	return answer(ctx, &m.recorder, "Search", r, func(*memoryv1.SearchRequest) (*memoryv1.SearchResponse, error) {
		return &memoryv1.SearchResponse{SessionId: "session-a"}, nil
	})
}

func (m *Memory) GetMemory(ctx context.Context, r *memoryv1.GetMemoryRequest) (*memoryv1.GetMemoryResponse, error) {
	return answer(ctx, &m.recorder, "GetMemory", r, func(r *memoryv1.GetMemoryRequest) (*memoryv1.GetMemoryResponse, error) {
		return &memoryv1.GetMemoryResponse{Memory: &memoryv1.MemoryResult{Idx: r.GetIdx()}}, nil
	})
}

func (m *Memory) CreateMemory(ctx context.Context, r *memoryv1.CreateMemoryRequest) (*memoryv1.CreateMemoryResponse, error) {
	return answer(ctx, &m.recorder, "CreateMemory", r, func(*memoryv1.CreateMemoryRequest) (*memoryv1.CreateMemoryResponse, error) {
		return &memoryv1.CreateMemoryResponse{OperationId: "create-a"}, nil
	})
}

func (m *Memory) EnrichMemory(ctx context.Context, r *memoryv1.EnrichMemoryRequest) (*memoryv1.EnrichMemoryResponse, error) {
	return answer(ctx, &m.recorder, "EnrichMemory", r, func(*memoryv1.EnrichMemoryRequest) (*memoryv1.EnrichMemoryResponse, error) {
		return &memoryv1.EnrichMemoryResponse{OperationId: "enrich-a"}, nil
	})
}

func (m *Memory) ShareFeedback(ctx context.Context, r *memoryv1.ShareFeedbackRequest) (*memoryv1.ShareFeedbackResponse, error) {
	return answer(ctx, &m.recorder, "ShareFeedback", r, func(*memoryv1.ShareFeedbackRequest) (*memoryv1.ShareFeedbackResponse, error) {
		return &memoryv1.ShareFeedbackResponse{SessionId: "session-a"}, nil
	})
}

func (m *Memory) RevertMemory(ctx context.Context, r *memoryv1.RevertMemoryRequest) (*memoryv1.RevertMemoryResponse, error) {
	return answer(ctx, &m.recorder, "RevertMemory", r, func(r *memoryv1.RevertMemoryRequest) (*memoryv1.RevertMemoryResponse, error) {
		return &memoryv1.RevertMemoryResponse{
			OperationId: r.GetOpId(),
			Outcome:     memoryv1.RevertOutcome_REVERT_OUTCOME_MERGED,
		}, nil
	})
}

func (m *Memory) ImportMemories(ctx context.Context, r *memoryv1.ImportMemoriesRequest) (*memoryv1.ImportMemoriesResponse, error) {
	return answer(ctx, &m.recorder, "ImportMemories", r, func(r *memoryv1.ImportMemoriesRequest) (*memoryv1.ImportMemoriesResponse, error) {
		response := &memoryv1.ImportMemoriesResponse{}
		for i := range r.GetMemories() {
			response.Results = append(response.Results, &memoryv1.ImportOutcome{
				Index:  int32(i),
				Status: memoryv1.ImportStatus_IMPORT_STATUS_QUEUED,
			})
		}
		return response, nil
	})
}

func (m *Memory) ListTools(ctx context.Context, r *memoryv1.ListToolsRequest) (*memoryv1.ListToolsResponse, error) {
	return answer(ctx, &m.recorder, "ListTools", r, func(*memoryv1.ListToolsRequest) (*memoryv1.ListToolsResponse, error) {
		response := &memoryv1.ListToolsResponse{}
		for _, name := range []string{
			"list_domains", "start_session", "search", "get_memory", "create_memory",
			"enrich_memory", "share_feedback", "revert_memory", "import_memories", "list_tools",
		} {
			response.Tools = append(response.Tools, &memoryv1.ToolDescriptor{
				Name:        name,
				Description: name + " tool",
				Available:   true,
			})
		}
		return response, nil
	})
}

// Tokens is the fake token service. It issues client-token-1, then -2, to
// whoever asks, numbering only the tokens it actually issued.
type Tokens struct {
	authv1.UnimplementedTokenServiceServer
	recorder

	expiresIn int32
	issued    int
}

// SetExpiresIn sets the lifetime, in seconds, each token is issued with. It
// starts at the service's default of 3600.
func (s *Tokens) SetExpiresIn(seconds int32) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expiresIn = seconds
}

func (s *Tokens) IssueToken(ctx context.Context, r *authv1.IssueTokenRequest) (*authv1.IssueTokenResponse, error) {
	return answer(ctx, &s.recorder, "IssueToken", r, func(*authv1.IssueTokenRequest) (*authv1.IssueTokenResponse, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.issued++
		return &authv1.IssueTokenResponse{
			AccessToken: fmt.Sprintf("client-token-%d", s.issued),
			TokenType:   "Bearer",
			ExpiresIn:   s.expiresIn,
		}, nil
	})
}

// The canned administration values a default answer carries.
var (
	CannedNetwork = &adminv1.Network{
		Id: "network-a", Name: "Acme", ParentId: "network-root", Domain: "coding",
		Region: "global", Scope: "customer", Owner: "acme", Description: "Acme's support knowledge",
	}
	CannedMember = &adminv1.Member{UserId: "user-a", Email: "ada@example.com", Name: "Ada"}
	CannedGroup  = &adminv1.Group{Id: "group-a", Name: "Support", MemoryNetworkId: "network-a", MemberCount: 2}
	CannedUser   = &adminv1.ExternalUser{
		Id: "xuser-a", ExternalId: "customer-42", Name: "Ada", Email: "ada@example.com",
		Roles: []string{"reader"}, Active: true,
	}
	CannedKey = &adminv1.ExternalUserKey{
		Id: "apikey-a", Name: "ci", ValuePrefix: "mk_live_ab", Roles: []string{"reader"},
		Scopes: []string{"mcp:read"}, ValidUntil: 1767225600,
	}
)

func clone[M proto.Message](m M) M { return proto.Clone(m).(M) }

// Admin is the fake administration service. Lists answer one canned entry,
// and a method acting on something echoes the handles it was sent.
//
// Created networks and users are numbered (network-1, xuser-1), and a user is
// placed in one network at a time: placing one already elsewhere is refused
// with USER_ALREADY_ASSIGNED_NETWORK unless forced, which moves them.
//
// Impersonation keys are numbered in the order they are minted, shared
// across users: impersonation-<external_id>-1 with key id key-1, then -2. A
// key is live until it is ended or expires; ending one that is not answers
// NOT_FOUND, as the service does.
type Admin struct {
	adminv1.UnimplementedAdminServiceServer
	recorder

	keyLifetime time.Duration
	expiresIn   bool
	keyCap      int
	clock       func() time.Time
	keys        int
	minted      map[string]mintedKey // key id → what it was minted as
	networks    int
	users       int
	names       map[string]string // network id → name
	placed      map[string]string // user id → network id
}

type mintedKey struct {
	externalID string
	expires    time.Time
	ended      bool
}

// SendExpiresIn makes each impersonation key carry expires_in, the seconds
// it has left, beside expires_at. It starts off, as a service that predates
// the field would have it.
func (s *Admin) SendExpiresIn(on bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expiresIn = on
}

// SetKeyCap refuses a key for a user already holding n live ones, as the
// service does at 20; zero, the start, refuses none.
func (s *Admin) SetKeyCap(n int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.keyCap = n
}

// Unended returns the ids of the keys minted, never ended and not yet expired.
func (s *Admin) Unended() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var ids []string
	for id, key := range s.minted {
		if !key.ended && key.expires.After(s.clock()) {
			ids = append(ids, id)
		}
	}
	return ids
}

// SetKeyLifetime sets how long each impersonation key lives. It starts at the
// service's default of an hour.
func (s *Admin) SetKeyLifetime(d time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.keyLifetime = d
}

// SetClock sets the wall clock a key's expiry is counted from.
func (s *Admin) SetClock(clock func() time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.clock = clock
}

func (s *Admin) ListNetworks(ctx context.Context, r *adminv1.ListNetworksRequest) (*adminv1.ListNetworksResponse, error) {
	return answer(ctx, &s.recorder, "ListNetworks", r, func(*adminv1.ListNetworksRequest) (*adminv1.ListNetworksResponse, error) {
		return &adminv1.ListNetworksResponse{Networks: []*adminv1.Network{clone(CannedNetwork)}, TotalCount: 1}, nil
	})
}

func (s *Admin) CreateNetwork(ctx context.Context, r *adminv1.CreateNetworkRequest) (*adminv1.Network, error) {
	return answer(ctx, &s.recorder, "CreateNetwork", r, func(r *adminv1.CreateNetworkRequest) (*adminv1.Network, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.networks++
		network := &adminv1.Network{
			Id: fmt.Sprintf("network-%d", s.networks), Name: r.GetName(), ParentId: r.GetParentId(),
			Domain: r.GetDomain(), Region: r.GetRegion(), Scope: r.GetScope(), Owner: r.GetOwner(),
			Description: r.GetDescription(),
		}
		if network.Domain == "" {
			network.Domain = "coding"
		}
		if network.Region == "" {
			network.Region = "global"
		}
		s.names[network.Id] = network.Name
		return network, nil
	})
}

func (s *Admin) UpdateNetwork(ctx context.Context, r *adminv1.UpdateNetworkRequest) (*adminv1.Network, error) {
	return answer(ctx, &s.recorder, "UpdateNetwork", r, func(r *adminv1.UpdateNetworkRequest) (*adminv1.Network, error) {
		network := clone(CannedNetwork)
		network.Id = r.GetId()
		set := func(from, to *string) {
			if from != nil {
				*to = *from
			}
		}
		set(r.Name, &network.Name)
		set(r.ParentId, &network.ParentId)
		set(r.Scope, &network.Scope)
		set(r.Owner, &network.Owner)
		set(r.Description, &network.Description)
		return network, nil
	})
}

func (s *Admin) DeleteNetwork(ctx context.Context, r *adminv1.DeleteNetworkRequest) (*adminv1.DeleteNetworkResponse, error) {
	return answer(ctx, &s.recorder, "DeleteNetwork", r, func(r *adminv1.DeleteNetworkRequest) (*adminv1.DeleteNetworkResponse, error) {
		return &adminv1.DeleteNetworkResponse{Id: r.GetId(), Removed: map[string]int64{"network_members": 1, "memories": 3}}, nil
	})
}

func (s *Admin) ListNetworkMembers(ctx context.Context, r *adminv1.ListNetworkMembersRequest) (*adminv1.ListNetworkMembersResponse, error) {
	return answer(ctx, &s.recorder, "ListNetworkMembers", r, func(*adminv1.ListNetworkMembersRequest) (*adminv1.ListNetworkMembersResponse, error) {
		return &adminv1.ListNetworkMembersResponse{Members: []*adminv1.Member{clone(CannedMember)}, TotalCount: 1}, nil
	})
}

func (s *Admin) AddNetworkMember(ctx context.Context, r *adminv1.AddNetworkMemberRequest) (*adminv1.AddNetworkMemberResponse, error) {
	return answer(ctx, &s.recorder, "AddNetworkMember", r, func(r *adminv1.AddNetworkMemberRequest) (*adminv1.AddNetworkMemberResponse, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		placed := &adminv1.AddNetworkMemberResponse{Id: r.GetId(), UserId: r.GetUserId()}
		if current, ok := s.placed[r.GetUserId()]; ok && current != r.GetId() {
			if !r.GetForce() {
				return nil, Failure{
					Code:    codes.FailedPrecondition,
					Details: "network_move_required: the user is already in network " + current,
					Reason:  "USER_ALREADY_ASSIGNED_NETWORK",
					Domain:  "memco.ai",
					Metadata: map[string]string{
						"current_network_id": current, "current_network_name": s.names[current],
					},
				}.err()
			}
			placed.MovedFrom = current
		}
		s.placed[r.GetUserId()] = r.GetId()
		return placed, nil
	})
}

func (s *Admin) RemoveNetworkMember(ctx context.Context, r *adminv1.RemoveNetworkMemberRequest) (*adminv1.RemoveNetworkMemberResponse, error) {
	return answer(ctx, &s.recorder, "RemoveNetworkMember", r, func(r *adminv1.RemoveNetworkMemberRequest) (*adminv1.RemoveNetworkMemberResponse, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		delete(s.placed, r.GetUserId())
		return &adminv1.RemoveNetworkMemberResponse{Id: r.GetId(), UserId: r.GetUserId()}, nil
	})
}

func (s *Admin) ListGroups(ctx context.Context, r *adminv1.ListGroupsRequest) (*adminv1.ListGroupsResponse, error) {
	return answer(ctx, &s.recorder, "ListGroups", r, func(*adminv1.ListGroupsRequest) (*adminv1.ListGroupsResponse, error) {
		return &adminv1.ListGroupsResponse{Groups: []*adminv1.Group{clone(CannedGroup)}, TotalCount: 1}, nil
	})
}

func (s *Admin) ListGroupMembers(ctx context.Context, r *adminv1.ListGroupMembersRequest) (*adminv1.ListGroupMembersResponse, error) {
	return answer(ctx, &s.recorder, "ListGroupMembers", r, func(*adminv1.ListGroupMembersRequest) (*adminv1.ListGroupMembersResponse, error) {
		return &adminv1.ListGroupMembersResponse{Members: []*adminv1.Member{clone(CannedMember)}}, nil
	})
}

func (s *Admin) AddNetworkGroup(ctx context.Context, r *adminv1.AddNetworkGroupRequest) (*adminv1.AddNetworkGroupResponse, error) {
	return answer(ctx, &s.recorder, "AddNetworkGroup", r, func(r *adminv1.AddNetworkGroupRequest) (*adminv1.AddNetworkGroupResponse, error) {
		return &adminv1.AddNetworkGroupResponse{Id: r.GetId(), GroupId: r.GetGroupId()}, nil
	})
}

func (s *Admin) RemoveNetworkGroup(ctx context.Context, r *adminv1.RemoveNetworkGroupRequest) (*adminv1.RemoveNetworkGroupResponse, error) {
	return answer(ctx, &s.recorder, "RemoveNetworkGroup", r, func(r *adminv1.RemoveNetworkGroupRequest) (*adminv1.RemoveNetworkGroupResponse, error) {
		return &adminv1.RemoveNetworkGroupResponse{Id: r.GetId(), GroupId: r.GetGroupId()}, nil
	})
}

func (s *Admin) ListExternalUsers(ctx context.Context, r *adminv1.ListExternalUsersRequest) (*adminv1.ListExternalUsersResponse, error) {
	return answer(ctx, &s.recorder, "ListExternalUsers", r, func(*adminv1.ListExternalUsersRequest) (*adminv1.ListExternalUsersResponse, error) {
		return &adminv1.ListExternalUsersResponse{ExternalUsers: []*adminv1.ExternalUser{clone(CannedUser)}, TotalCount: 1}, nil
	})
}

func (s *Admin) GetExternalUser(ctx context.Context, r *adminv1.GetExternalUserRequest) (*adminv1.ExternalUser, error) {
	return answer(ctx, &s.recorder, "GetExternalUser", r, func(r *adminv1.GetExternalUserRequest) (*adminv1.ExternalUser, error) {
		user := clone(CannedUser)
		user.ExternalId = r.GetExternalId()
		return user, nil
	})
}

func (s *Admin) CreateExternalUser(ctx context.Context, r *adminv1.CreateExternalUserRequest) (*adminv1.ExternalUser, error) {
	return answer(ctx, &s.recorder, "CreateExternalUser", r, func(r *adminv1.CreateExternalUserRequest) (*adminv1.ExternalUser, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.users++
		return &adminv1.ExternalUser{
			Id: fmt.Sprintf("xuser-%d", s.users), ExternalId: r.GetExternalId(), Name: r.GetName(),
			Email: r.GetEmail(), Roles: r.GetRoles(), Active: true,
		}, nil
	})
}

func (s *Admin) UpdateExternalUser(ctx context.Context, r *adminv1.UpdateExternalUserRequest) (*adminv1.ExternalUser, error) {
	return answer(ctx, &s.recorder, "UpdateExternalUser", r, func(r *adminv1.UpdateExternalUserRequest) (*adminv1.ExternalUser, error) {
		user := clone(CannedUser)
		user.ExternalId = r.GetExternalId()
		if r.Name != nil {
			user.Name = r.GetName()
		}
		if r.Email != nil {
			user.Email = r.GetEmail()
		}
		if len(r.GetRoles()) > 0 {
			user.Roles = r.GetRoles()
		}
		return user, nil
	})
}

func (s *Admin) DeleteExternalUser(ctx context.Context, r *adminv1.DeleteExternalUserRequest) (*adminv1.DeleteExternalUserResponse, error) {
	return answer(ctx, &s.recorder, "DeleteExternalUser", r, func(r *adminv1.DeleteExternalUserRequest) (*adminv1.DeleteExternalUserResponse, error) {
		return &adminv1.DeleteExternalUserResponse{ExternalId: r.GetExternalId()}, nil
	})
}

func (s *Admin) ListExternalUserKeys(ctx context.Context, r *adminv1.ListExternalUserKeysRequest) (*adminv1.ListExternalUserKeysResponse, error) {
	return answer(ctx, &s.recorder, "ListExternalUserKeys", r, func(*adminv1.ListExternalUserKeysRequest) (*adminv1.ListExternalUserKeysResponse, error) {
		return &adminv1.ListExternalUserKeysResponse{Keys: []*adminv1.ExternalUserKey{clone(CannedKey)}}, nil
	})
}

func (s *Admin) CreateExternalUserKey(ctx context.Context, r *adminv1.CreateExternalUserKeyRequest) (*adminv1.CreateExternalUserKeyResponse, error) {
	return answer(ctx, &s.recorder, "CreateExternalUserKey", r, func(r *adminv1.CreateExternalUserKeyRequest) (*adminv1.CreateExternalUserKeyResponse, error) {
		key := clone(CannedKey)
		key.Id, key.Name, key.ValuePrefix = "apikey-new", r.GetName(), "mk_live_ne"
		if r.GetValidUntil() != 0 {
			key.ValidUntil = r.GetValidUntil()
		}
		return &adminv1.CreateExternalUserKeyResponse{Key: key, Value: "mk_live_new-key-value"}, nil
	})
}

func (s *Admin) DeleteExternalUserKey(ctx context.Context, r *adminv1.DeleteExternalUserKeyRequest) (*adminv1.DeleteExternalUserKeyResponse, error) {
	return answer(ctx, &s.recorder, "DeleteExternalUserKey", r, func(r *adminv1.DeleteExternalUserKeyRequest) (*adminv1.DeleteExternalUserKeyResponse, error) {
		return &adminv1.DeleteExternalUserKeyResponse{ExternalId: r.GetExternalId(), KeyId: r.GetKeyId()}, nil
	})
}

func (s *Admin) ImpersonateExternalUser(ctx context.Context, r *adminv1.ImpersonateExternalUserRequest) (*adminv1.ImpersonationKey, error) {
	return answer(ctx, &s.recorder, "ImpersonateExternalUser", r, func(r *adminv1.ImpersonateExternalUserRequest) (*adminv1.ImpersonationKey, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		now, live := s.clock(), 0
		for _, key := range s.minted {
			if key.externalID == r.GetExternalId() && !key.ended && key.expires.After(now) {
				live++
			}
		}
		if s.keyCap > 0 && live >= s.keyCap {
			return nil, status.Errorf(codes.ResourceExhausted, "%s holds %d live impersonation keys", r.GetExternalId(), live)
		}
		s.keys++
		key := &adminv1.ImpersonationKey{
			Value:     fmt.Sprintf("impersonation-%s-%d", r.GetExternalId(), s.keys),
			ExpiresAt: now.Add(s.keyLifetime).Unix(),
			Roles:     []string{"reader", "creator"},
			Scopes:    []string{"memory"},
			KeyId:     fmt.Sprintf("key-%d", s.keys),
		}
		if s.expiresIn {
			key.ExpiresIn = int64(s.keyLifetime / time.Second)
		}
		s.minted[key.KeyId] = mintedKey{externalID: r.GetExternalId(), expires: time.Unix(key.ExpiresAt, 0)}
		return key, nil
	})
}

func (s *Admin) EndImpersonation(ctx context.Context, r *adminv1.EndImpersonationRequest) (*adminv1.EndImpersonationResponse, error) {
	return answer(ctx, &s.recorder, "EndImpersonation", r, func(r *adminv1.EndImpersonationRequest) (*adminv1.EndImpersonationResponse, error) {
		s.mu.Lock()
		defer s.mu.Unlock()
		key, ok := s.minted[r.GetKeyId()]
		if !ok || key.ended || key.externalID != r.GetExternalId() {
			return nil, status.Errorf(codes.NotFound, "%s holds no live key %s", r.GetExternalId(), r.GetKeyId())
		}
		key.ended = true
		s.minted[r.GetKeyId()] = key
		return &adminv1.EndImpersonationResponse{ExternalId: r.GetExternalId(), KeyId: r.GetKeyId()}, nil
	})
}

// Health is the fake health service.
type Health struct {
	grpc_health_v1.UnimplementedHealthServer

	mu        sync.Mutex
	status    grpc_health_v1.HealthCheckResponse_ServingStatus
	checked   []string
	metadata  []metadata.MD
	transient []Failure
	journal   *journal
}

// SetStatus sets what the probe reports.
func (h *Health) SetStatus(s grpc_health_v1.HealthCheckResponse_ServingStatus) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.status = s
}

// Checked returns the service names probed so far.
func (h *Health) Checked() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]string(nil), h.checked...)
}

// Metadata returns the metadata each probe arrived with.
func (h *Health) Metadata() []metadata.MD {
	h.mu.Lock()
	defer h.mu.Unlock()
	copied := make([]metadata.MD, len(h.metadata))
	for i, md := range h.metadata {
		copied[i] = md.Copy()
	}
	return copied
}

// FailNext queues failures the probe serves once each.
func (h *Health) FailNext(failures ...Failure) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.transient = append(h.transient, failures...)
}

// Forget drops everything recorded so far.
func (h *Health) Forget() {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.checked = nil
	h.metadata = nil
}

func (h *Health) Check(ctx context.Context, r *grpc_health_v1.HealthCheckRequest) (*grpc_health_v1.HealthCheckResponse, error) {
	h.journal.note(ctx, "Check", r)
	h.mu.Lock()
	defer h.mu.Unlock()
	md, _ := metadata.FromIncomingContext(ctx)
	h.checked = append(h.checked, r.GetService())
	h.metadata = append(h.metadata, md.Copy())
	if len(h.transient) > 0 {
		failure := h.transient[0]
		h.transient = h.transient[1:]
		return nil, failure.err()
	}
	return &grpc_health_v1.HealthCheckResponse{Status: h.status}, nil
}

// Harness is a running fake and the address to dial it on.
type Harness struct {
	Memory  *Memory
	Health  *Health
	Tokens  *Tokens
	Admin   *Admin
	Address string

	journal *journal
}

// Calls returns the method of every call any of the services received, in
// arrival order.
func (h *Harness) Calls() []string {
	var methods []string
	for _, call := range h.Log() {
		methods = append(methods, call.Method)
	}
	return methods
}

// Log returns every call any of the services received, in arrival order.
func (h *Harness) Log() []Call {
	h.journal.mu.Lock()
	defer h.journal.mu.Unlock()
	return append([]Call(nil), h.journal.calls...)
}

// Forget drops what every service recorded, such as a client's connect calls.
func (h *Harness) Forget() {
	h.Memory.Forget()
	h.Health.Forget()
	h.Tokens.Forget()
	h.Admin.Forget()
	h.journal.mu.Lock()
	defer h.journal.mu.Unlock()
	h.journal.calls = nil
}

// Start serves plaintext on a loopback port until the test ends.
func Start(t testing.TB) *Harness {
	t.Helper()
	return start(t, "127.0.0.1:0")
}

// StartOn serves plaintext on the given address until the test ends.
func StartOn(t testing.TB, address string) *Harness {
	t.Helper()
	return start(t, address)
}

// StartTLS serves TLS with the given certificate until the test ends.
func StartTLS(t testing.TB, certificate tls.Certificate) *Harness {
	t.Helper()
	return start(t, "127.0.0.1:0", grpc.Creds(credentials.NewServerTLSFromCert(&certificate)))
}

func start(t testing.TB, address string, options ...grpc.ServerOption) *Harness {
	t.Helper()
	listener, err := net.Listen("tcp", address)
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	server := grpc.NewServer(options...)
	j := &journal{}
	harness := &Harness{
		Memory: &Memory{recorder: newRecorder(j)},
		Health: &Health{status: grpc_health_v1.HealthCheckResponse_SERVING, journal: j},
		Tokens: &Tokens{recorder: newRecorder(j), expiresIn: 3600},
		Admin: &Admin{
			recorder: newRecorder(j), keyLifetime: time.Hour, clock: time.Now,
			minted: map[string]mintedKey{}, names: map[string]string{}, placed: map[string]string{},
		},
		Address: listener.Addr().String(),
		journal: j,
	}
	memoryv1.RegisterMemoryServiceServer(server, harness.Memory)
	grpc_health_v1.RegisterHealthServer(server, harness.Health)
	authv1.RegisterTokenServiceServer(server, harness.Tokens)
	adminv1.RegisterAdminServiceServer(server, harness.Admin)
	go func() { _ = server.Serve(listener) }()
	// Stop, not GracefulStop: a delayed handler must not hold the test open.
	t.Cleanup(server.Stop)
	return harness
}
