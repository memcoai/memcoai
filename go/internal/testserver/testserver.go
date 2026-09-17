// Package testserver is an in-process Memco service for tests: a real gRPC
// server on a loopback port that records what it is sent and answers with
// defaults, staged responses or staged failures.
package testserver

import (
	"context"
	"crypto/tls"
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

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

// Methods are the memory service's RPC names, in contract order.
var Methods = []string{
	"ListDomains", "StartSession", "Search", "GetMemory", "CreateMemory",
	"EnrichMemory", "ShareFeedback", "RevertMemory", "ImportMemories", "ListTools",
}

// Failure is a status to answer with. Reason and Domain, when set, attach a
// google.rpc.ErrorInfo detail.
type Failure struct {
	Code    codes.Code
	Details string
	Reason  string
	Domain  string
}

func (f Failure) err() error {
	st := status.New(f.Code, f.Details)
	if f.Reason == "" && f.Domain == "" {
		return st.Err()
	}
	detailed, err := st.WithDetails(&errdetails.ErrorInfo{Reason: f.Reason, Domain: f.Domain})
	if err != nil {
		panic(err)
	}
	return detailed.Err()
}

// Memory is the fake memory service.
type Memory struct {
	memoryv1.UnimplementedMemoryServiceServer

	mu        sync.Mutex
	calls     []string
	metadata  []metadata.MD
	requests  map[string]proto.Message
	responses map[string]proto.Message
	failure   *Failure
	transient map[string][]Failure
	delays    map[string]time.Duration
}

func newMemory() *Memory {
	return &Memory{
		requests:  map[string]proto.Message{},
		responses: map[string]proto.Message{},
		transient: map[string][]Failure{},
		delays:    map[string]time.Duration{},
	}
}

// Calls returns the method names called so far, including calls that failed.
func (m *Memory) Calls() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string(nil), m.calls...)
}

// Metadata returns the metadata each call arrived with, in call order.
func (m *Memory) Metadata() []metadata.MD {
	m.mu.Lock()
	defer m.mu.Unlock()
	copied := make([]metadata.MD, len(m.metadata))
	for i, md := range m.metadata {
		copied[i] = md.Copy()
	}
	return copied
}

// Request returns the last request the named method received, or nil.
func (m *Memory) Request(method string) proto.Message {
	m.mu.Lock()
	defer m.mu.Unlock()
	if request, ok := m.requests[method]; ok {
		return proto.Clone(request)
	}
	return nil
}

// Respond stages the response the named method answers with.
func (m *Memory) Respond(method string, response proto.Message) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.responses[method] = response
}

// Fail makes every method answer with f; nil restores normal answers.
func (m *Memory) Fail(f *Failure) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.failure = f
}

// FailNext queues failures the named method serves once each before answering
// normally.
func (m *Memory) FailNext(method string, failures ...Failure) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.transient[method] = append(m.transient[method], failures...)
}

// Delay makes the named method wait before answering, or until its call ends.
func (m *Memory) Delay(method string, d time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.delays[method] = d
}

// Forget drops everything recorded so far and keeps what was staged.
func (m *Memory) Forget() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.calls = nil
	m.metadata = nil
	m.requests = map[string]proto.Message{}
}

// record notes the call before anything can fail it, so tests can count the
// attempts a retry made.
func (m *Memory) record(ctx context.Context, method string, request proto.Message) (time.Duration, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	md, _ := metadata.FromIncomingContext(ctx)
	m.calls = append(m.calls, method)
	m.metadata = append(m.metadata, md.Copy())
	m.requests[method] = proto.Clone(request)
	if queued := m.transient[method]; len(queued) > 0 {
		m.transient[method] = queued[1:]
		return m.delays[method], queued[0].err()
	}
	if m.failure != nil {
		return m.delays[method], m.failure.err()
	}
	return m.delays[method], nil
}

func (m *Memory) staged(method string) proto.Message {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.responses[method]
}

func answer[Req, Resp proto.Message](ctx context.Context, m *Memory, method string, request Req, fallback func(Req) Resp) (Resp, error) {
	var zero Resp
	delay, failure := m.record(ctx, method, request)
	if delay > 0 {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-ctx.Done():
			return zero, status.FromContextError(ctx.Err()).Err()
		}
	}
	if failure != nil {
		return zero, failure
	}
	if staged := m.staged(method); staged != nil {
		return proto.Clone(staged).(Resp), nil
	}
	return fallback(request), nil
}

func (m *Memory) ListDomains(ctx context.Context, r *memoryv1.ListDomainsRequest) (*memoryv1.ListDomainsResponse, error) {
	return answer(ctx, m, "ListDomains", r, func(*memoryv1.ListDomainsRequest) *memoryv1.ListDomainsResponse {
		return &memoryv1.ListDomainsResponse{}
	})
}

func (m *Memory) StartSession(ctx context.Context, r *memoryv1.StartSessionRequest) (*memoryv1.StartSessionResponse, error) {
	return answer(ctx, m, "StartSession", r, func(*memoryv1.StartSessionRequest) *memoryv1.StartSessionResponse {
		return &memoryv1.StartSessionResponse{SessionId: "session-a"}
	})
}

func (m *Memory) Search(ctx context.Context, r *memoryv1.SearchRequest) (*memoryv1.SearchResponse, error) {
	return answer(ctx, m, "Search", r, func(*memoryv1.SearchRequest) *memoryv1.SearchResponse {
		return &memoryv1.SearchResponse{SessionId: "session-a"}
	})
}

func (m *Memory) GetMemory(ctx context.Context, r *memoryv1.GetMemoryRequest) (*memoryv1.GetMemoryResponse, error) {
	return answer(ctx, m, "GetMemory", r, func(r *memoryv1.GetMemoryRequest) *memoryv1.GetMemoryResponse {
		return &memoryv1.GetMemoryResponse{Memory: &memoryv1.MemoryResult{Idx: r.GetIdx()}}
	})
}

func (m *Memory) CreateMemory(ctx context.Context, r *memoryv1.CreateMemoryRequest) (*memoryv1.CreateMemoryResponse, error) {
	return answer(ctx, m, "CreateMemory", r, func(*memoryv1.CreateMemoryRequest) *memoryv1.CreateMemoryResponse {
		return &memoryv1.CreateMemoryResponse{OperationId: "create-a"}
	})
}

func (m *Memory) EnrichMemory(ctx context.Context, r *memoryv1.EnrichMemoryRequest) (*memoryv1.EnrichMemoryResponse, error) {
	return answer(ctx, m, "EnrichMemory", r, func(*memoryv1.EnrichMemoryRequest) *memoryv1.EnrichMemoryResponse {
		return &memoryv1.EnrichMemoryResponse{OperationId: "enrich-a"}
	})
}

func (m *Memory) ShareFeedback(ctx context.Context, r *memoryv1.ShareFeedbackRequest) (*memoryv1.ShareFeedbackResponse, error) {
	return answer(ctx, m, "ShareFeedback", r, func(*memoryv1.ShareFeedbackRequest) *memoryv1.ShareFeedbackResponse {
		return &memoryv1.ShareFeedbackResponse{SessionId: "session-a"}
	})
}

func (m *Memory) RevertMemory(ctx context.Context, r *memoryv1.RevertMemoryRequest) (*memoryv1.RevertMemoryResponse, error) {
	return answer(ctx, m, "RevertMemory", r, func(r *memoryv1.RevertMemoryRequest) *memoryv1.RevertMemoryResponse {
		return &memoryv1.RevertMemoryResponse{
			OperationId: r.GetOpId(),
			Outcome:     memoryv1.RevertOutcome_REVERT_OUTCOME_MERGED,
		}
	})
}

func (m *Memory) ImportMemories(ctx context.Context, r *memoryv1.ImportMemoriesRequest) (*memoryv1.ImportMemoriesResponse, error) {
	return answer(ctx, m, "ImportMemories", r, func(r *memoryv1.ImportMemoriesRequest) *memoryv1.ImportMemoriesResponse {
		response := &memoryv1.ImportMemoriesResponse{}
		for i := range r.GetMemories() {
			response.Results = append(response.Results, &memoryv1.ImportOutcome{
				Index:  int32(i),
				Status: memoryv1.ImportStatus_IMPORT_STATUS_QUEUED,
			})
		}
		return response
	})
}

func (m *Memory) ListTools(ctx context.Context, r *memoryv1.ListToolsRequest) (*memoryv1.ListToolsResponse, error) {
	return answer(ctx, m, "ListTools", r, func(*memoryv1.ListToolsRequest) *memoryv1.ListToolsResponse {
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
		return response
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
	Address string
}

// Forget drops what both services recorded, such as a client's connect calls.
func (h *Harness) Forget() {
	h.Memory.Forget()
	h.Health.Forget()
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
	harness := &Harness{
		Memory:  newMemory(),
		Health:  &Health{status: grpc_health_v1.HealthCheckResponse_SERVING},
		Address: listener.Addr().String(),
	}
	memoryv1.RegisterMemoryServiceServer(server, harness.Memory)
	grpc_health_v1.RegisterHealthServer(server, harness.Health)
	go func() { _ = server.Serve(listener) }()
	// Stop, not GracefulStop: a delayed handler must not hold the test open.
	t.Cleanup(server.Stop)
	return harness
}
