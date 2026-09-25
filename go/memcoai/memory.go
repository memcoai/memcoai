package memcoai

import (
	"context"
	"errors"
	"log/slog"

	"google.golang.org/grpc"

	"github.com/memcoai/memcoai/go/internal/admin"
	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/memory"
	"github.com/memcoai/memcoai/go/internal/transport"
	"github.com/memcoai/memcoai/go/internal/warn"
)

// NewMemory is the memory idx that opens a new memory when passed to an
// enrichment. It is case-sensitive: "New" is an ordinary handle.
const NewMemory = "new"

// MemoryOperations are the memory service's operations, reached as
// [Client.Memory]. Most calls are easier through a [Session], which supplies
// the session and the domain.
type MemoryOperations struct {
	client *Client
	rpc    memoryv1.MemoryServiceClient
	log    *slog.Logger
	known  *memory.Known
	// credential is what every call made here carries: the client's own, or
	// an impersonated session's key.
	credential *transport.Renewing
}

// SessionOption configures a session [MemoryOperations.StartSession] opens.
type SessionOption func(*sessionOptions)

type sessionOptions struct {
	externalID   string
	impersonated bool
}

// ExternalID opens the session acting as one of your own users, by your id
// for them, as created with [UserOperations.Create]. The client's credential
// mints a short-lived key acting as that user, and the session and every call
// through it carry that key, which renews itself before it expires: what the
// session finds and writes is that user's, bounded by the network they are
// in. The session first lists the domains under the key, learning the limits
// and any deprecation notice as that user. Close the session with
// [Session.Close] when done, which ends the key.
//
// Parameters:
//   - externalID: your id for the user to act as.
func ExternalID(externalID string) SessionOption {
	return func(o *sessionOptions) { o.externalID, o.impersonated = externalID, true }
}

// SearchParams scope and narrow a search.
type SearchParams struct {
	// Domain is the domain to search, when the search names no session.
	Domain string
	// SessionID records the search under an open session, which also names
	// the domain.
	SessionID string
	// Tags narrow or boost the results.
	Tags []Tag
}

// CreateMemoryParams describe a new memory.
type CreateMemoryParams struct {
	// Query is what someone would search to find the memory. Required.
	Query string
	// Title is a short title for the memory. Required.
	Title string
	// Content is the knowledge being saved. Required.
	Content string
	// Domain is the domain to save into, when the write names no session.
	Domain string
	// SessionID is the session the memory was learned during, which also
	// names the domain.
	SessionID string
	// Tags describe the memory.
	Tags []Tag
	// Source is who produced the content; unset is the agent.
	Source DataSource
}

// EnrichMemoryParams describe an addition to a memory.
type EnrichMemoryParams struct {
	// MemoryIdx is the memory to add to, or [NewMemory]. Required.
	MemoryIdx string
	// SessionID is the session whose search returned the memory. Required.
	SessionID string
	// Title is a short title for the addition. Required.
	Title string
	// Content is the knowledge being added. Required.
	Content string
	// Tags describe the addition.
	Tags []Tag
	// Sources are handles of the memories the addition draws on.
	Sources []string
	// Source is who produced the content; unset is the agent.
	Source DataSource
}

// ImportMemoriesParams scope an import.
type ImportMemoriesParams struct {
	// Domain is the domain to import into, when the import names no session.
	Domain string
	// SessionID records the import under an open session, which also names
	// the domain.
	SessionID string
}

// invoke runs one generated client method under this namespace's credential.
func invoke[Req, Resp any](ctx context.Context, m *MemoryOperations, rpc string, req Req,
	method func(context.Context, Req, ...grpc.CallOption) (Resp, error)) (Resp, error) {
	return unary(ctx, m.client, m.credential, rpc, req, method)
}

// ListDomains lists the memory domains the credential may name, with the
// limits the service enforces. The client remembers those limits and checks
// later calls against them before sending, and surfaces a deprecation notice
// the first time the service reports one. Reads are retried when the service
// is briefly unavailable.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//
// It returns the domains, what each holds and the tags it uses, the limits, and
// any deprecation notice.
//
// Errors: [*AuthenticationError] when the credential is rejected;
// [*SunsetError] when what the client uses has stopped working;
// [*UnavailableError] when the service cannot be reached; [*ConfigError] when the client is closed.
func (m *MemoryOperations) ListDomains(ctx context.Context) (*DomainList, error) {
	response, err := invoke(ctx, m, "ListDomains", &memoryv1.ListDomainsRequest{}, m.rpc.ListDomains)
	if err != nil {
		return nil, err
	}
	m.known.Update(response.GetLimits(), response.GetDomains())
	warn.Deprecation(m.log, response.GetDeprecationMessage(), memory.Day(response.GetSunsetDate()))
	return toDomainList(response), nil
}

// ListTools lists every method the service declares, and which the
// credential's role permits. [MemoryOperations.StartSession] fetches this for
// [Session.Tools] already.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//
// It returns one descriptor per method.
//
// Errors: [*AuthenticationError] when the credential is rejected;
// [*UnavailableError] when the service cannot be reached; [*ConfigError] when the client is closed.
func (m *MemoryOperations) ListTools(ctx context.Context) ([]ToolDescriptor, error) {
	response, err := invoke(ctx, m, "ListTools", &memoryv1.ListToolsRequest{}, m.rpc.ListTools)
	if err != nil {
		return nil, err
	}
	return toTools(response), nil
}

// StartSession opens a session in a domain. Every call made through the
// session is recorded under it, which is what relates the searches of one task
// and lets their results be rated.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent. The session and the tool catalog are fetched concurrently,
//     and neither call outlives this one.
//   - domain: the slug of a domain [MemoryOperations.ListDomains] listed.
//   - opts: [ExternalID] to open the session acting as one of your own users,
//     which mints a key for them first, lists the domains under it, and then
//     starts the session under it. Such a session must be closed with
//     [Session.Close].
//
// It returns the open session. When the tool catalog cannot be fetched, the
// session still opens and [Session.Tools] offers every tool; a warning is
// logged.
//
// Errors: [*InvalidRequestError] when domain or the external id is blank
// (nothing is sent); [*PermissionError] when the credential may not use the
// domain, or may not act for users; [*NotFoundError] when there is no such
// domain or user; [*ConfigError] when the client is closed. With
// [ExternalID], a failure after the key was minted ends the key. From a
// service that does not yet say how many seconds a key has left, a
// [*ConfigError] naming the clock says this machine's clock runs so far ahead
// of the service's that the key minted had already expired by it.
func (m *MemoryOperations) StartSession(ctx context.Context, domain string, opts ...SessionOption) (*Session, error) {
	var chosen sessionOptions
	for _, opt := range opts {
		if opt != nil {
			opt(&chosen)
		}
	}
	if !chosen.impersonated {
		return m.startSession(ctx, domain, nil)
	}
	// Checked before the mint, so a blank value costs no key.
	if err := memory.CheckStartSession(&memoryv1.StartSessionRequest{Domain: domain}); err != nil {
		return nil, public(err)
	}
	if err := admin.Check(&adminv1.ImpersonateExternalUserRequest{ExternalId: chosen.externalID}); err != nil {
		return nil, public(err)
	}
	key := m.client.impersonate(chosen.externalID)
	// Every call from here on carries the key, including those the session's
	// tools and the memories it returns make: they reach the service through
	// the operations the session was opened on.
	scoped := &MemoryOperations{client: m.client, rpc: m.rpc, log: m.log, known: m.known, credential: key}
	// The first lease mints the key, and the listing teaches the session its
	// limits and any deprecation as that user.
	_, err := scoped.ListDomains(ctx)
	var session *Session
	if err == nil {
		session, err = scoped.startSession(ctx, domain, key)
	}
	if err != nil {
		// Beside the caller, who is kept to their own deadline: the end has a
		// timeout of its own, and the registry holds the key for a close.
		go key.Close(context.WithoutCancel(ctx))
		return nil, err
	}
	return session, nil
}

// startSession opens a session under this namespace's credential; key is what
// the session holds, or nil.
func (m *MemoryOperations) startSession(ctx context.Context, domain string, key *transport.Renewing) (*Session, error) {
	request := &memoryv1.StartSessionRequest{Domain: domain}
	if err := memory.CheckStartSession(request); err != nil {
		return nil, public(err)
	}
	type fetched struct {
		tools []ToolDescriptor
		err   error
	}
	catalogCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	catalog := make(chan fetched, 1)
	go func() {
		tools, err := m.ListTools(catalogCtx)
		catalog <- fetched{tools, err}
	}()
	response, err := invoke(ctx, m, "StartSession", request, m.rpc.StartSession)
	if err != nil {
		cancel()
		<-catalog
		return nil, err
	}
	session := &Session{ID: response.GetSessionId(), Instructions: toInstructions(response.GetInstructions()), ops: m, key: key}
	got := <-catalog
	var api *APIError
	switch {
	case got.err == nil:
		session.catalog, session.catalogKnown = got.tools, true
	case errors.As(got.err, &api):
		// Handled: a session without its catalog offers every tool.
		m.log.Warn("listTools failed while opening a session; treating every tool as available", "error", got.err.Error())
	default:
		return nil, got.err
	}
	return session, nil
}

// Search retrieves memories for a task-based query. It is not retried: an
// unscoped search opens a session, and a replayed one would open another.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - query: what to find, such as a question or a task description.
//   - p: where to search and how to narrow it. Domain or SessionID is
//     required; tags beyond the domain's cap are dropped, as the service would.
//
// It returns the memories selected, and the session the search was recorded
// under: p.SessionID, or a new session when only p.Domain was given. Rate the
// results with [Memory.Feedback] or [MemoryOperations.ShareFeedback].
//
// Errors: [*InvalidRequestError] when query is blank or longer than the
// service allows, when p names no domain and no session, or when a tag is blank
// (nothing is sent); [*PermissionError]; [*ResourceExhaustedError] when a rate
// limit or quota is spent; [*ConfigError] when the client is closed.
func (m *MemoryOperations) Search(ctx context.Context, query string, p SearchParams) (*SearchResult, error) {
	request := &memoryv1.SearchRequest{Query: query, Domain: p.Domain, SessionId: p.SessionID, Tags: wireTags(p.Tags)}
	if err := memory.CheckSearch(request, m.known.Snapshot(p.Domain), m.log); err != nil {
		return nil, public(err)
	}
	response, err := invoke(ctx, m, "Search", request, m.rpc.Search)
	if err != nil {
		return nil, err
	}
	return toSearchResult(response, m), nil
}

// GetMemory fetches the memory behind a handle a search returned, including
// one returned only as a reference. A memory fetched this way belongs to no
// session, so [Memory.Feedback] refuses it; fetch through [Session.GetMemory]
// to rate it.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - idx: a memory's or an insight's handle, copied from a search; an
//     insight's handle fetches the memory holding it.
//
// It returns the memory.
//
// Errors: [*InvalidRequestError] when idx is blank or longer than the service
// allows (nothing is sent); [*NotFoundError] when idx resolves to nothing;
// [*ConfigError] when the client is closed.
func (m *MemoryOperations) GetMemory(ctx context.Context, idx string) (*Memory, error) {
	return m.getMemory(ctx, idx, "")
}

func (m *MemoryOperations) getMemory(ctx context.Context, idx, sessionID string) (*Memory, error) {
	request := &memoryv1.GetMemoryRequest{Idx: idx}
	if err := memory.CheckGetMemory(request, m.known.Snapshot("")); err != nil {
		return nil, public(err)
	}
	response, err := invoke(ctx, m, "GetMemory", request, m.rpc.GetMemory)
	if err != nil {
		return nil, err
	}
	result, err := memory.RequireMemory(response, idx)
	if err != nil {
		return nil, public(err)
	}
	found := toMemory(result, m, sessionID)
	return &found, nil
}

// CreateMemory saves new knowledge. The write is processed asynchronously, so
// the result names the operation rather than the memory it becomes, and the
// memory is not searchable straight away.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: what to save and where. Query, Title and Content are required, and
//     Domain or SessionID names the domain; a Source left unset is the agent.
//
// It returns the operation's id, which [MemoryOperations.RevertMemory] undoes,
// or an empty id when the service could not mint one.
//
// Errors: [*InvalidRequestError] when a required field is blank, the scope is
// missing, or the text is longer than the service allows (nothing is sent);
// [*PermissionError] when the credential may not write there;
// [*ResourceExhaustedError]; [*ConfigError] when the client is closed.
func (m *MemoryOperations) CreateMemory(ctx context.Context, p CreateMemoryParams) (*WriteResult, error) {
	request := &memoryv1.CreateMemoryRequest{
		Query:     p.Query,
		Title:     p.Title,
		Content:   p.Content,
		Domain:    p.Domain,
		SessionId: p.SessionID,
		Tags:      wireTags(p.Tags),
		Source:    wireSource(p.Source),
	}
	if err := memory.CheckCreate(request, m.known.Snapshot(p.Domain), m.log); err != nil {
		return nil, public(err)
	}
	response, err := invoke(ctx, m, "CreateMemory", request, m.rpc.CreateMemory)
	if err != nil {
		return nil, err
	}
	return toWriteResult(response.GetOperationId(), response.GetInstructions()), nil
}

// EnrichMemory adds to a memory a search returned, or opens a new one.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: what to add. MemoryIdx is a handle from a search in SessionID, or
//     [NewMemory]; SessionID, Title and Content are required. Sources beyond
//     the service's cap are dropped, as the service would.
//
// It returns the operation's id, as [MemoryOperations.CreateMemory] does.
//
// Errors: [*InvalidRequestError] when a required field or a source is blank,
// or a handle or the text is longer than the service allows (nothing is sent);
// [*NotFoundError] when the memory is not one the session returned;
// [*PermissionError]; [*ResourceExhaustedError]; [*ConfigError] when the client is closed.
func (m *MemoryOperations) EnrichMemory(ctx context.Context, p EnrichMemoryParams) (*WriteResult, error) {
	request := &memoryv1.EnrichMemoryRequest{
		MemoryIdx: p.MemoryIdx,
		SessionId: p.SessionID,
		Title:     p.Title,
		Content:   p.Content,
		Tags:      wireTags(p.Tags),
		Sources:   copied(p.Sources),
		Source:    wireSource(p.Source),
	}
	// An enrichment names a session, never a domain, so no domain's tag cap is
	// known here and the trim is left to the service.
	if err := memory.CheckEnrich(request, m.known.Snapshot(""), m.log); err != nil {
		return nil, public(err)
	}
	response, err := invoke(ctx, m, "EnrichMemory", request, m.rpc.EnrichMemory)
	if err != nil {
		return nil, err
	}
	return toWriteResult(response.GetOperationId(), response.GetInstructions()), nil
}

// ShareFeedback rates the results of a search. Ratings are what move the
// reliability signal on an insight.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - sessionID: the session the rated search was recorded under.
//   - feedback: one rating per result; at least one, with a handle copied from
//     the search.
//
// It returns the ratings the service recorded, with any advice they earned.
//
// Errors: [*InvalidRequestError] when sessionID or a handle is blank, feedback
// is empty, or it holds more ratings than the service allows (nothing is
// sent); [*NotFoundError] when a handle is not one the session returned;
// [*ConfigError] when the client is closed.
func (m *MemoryOperations) ShareFeedback(ctx context.Context, sessionID string, feedback []FeedbackRating) (*FeedbackResult, error) {
	request := &memoryv1.ShareFeedbackRequest{SessionId: sessionID, Feedback: wireRatings(feedback)}
	if err := memory.CheckShareFeedback(request, m.known.Snapshot("")); err != nil {
		return nil, public(err)
	}
	response, err := invoke(ctx, m, "ShareFeedback", request, m.rpc.ShareFeedback)
	if err != nil {
		return nil, err
	}
	return toFeedbackResult(response), nil
}

// RevertMemory undoes the caller's own write. A write not yet processed, too
// old, or under moderation is reported in the result's Outcome, not as an
// error.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - operationID: the id [MemoryOperations.CreateMemory] or
//     [MemoryOperations.EnrichMemory] returned.
//
// It returns what the revert removed.
//
// Errors: [*InvalidRequestError] when operationID is blank or longer than the
// service allows (nothing is sent); [*PermissionError]; [*ConfigError] when the client is closed.
func (m *MemoryOperations) RevertMemory(ctx context.Context, operationID string) (*RevertResult, error) {
	request := &memoryv1.RevertMemoryRequest{OpId: operationID}
	if err := memory.CheckRevert(request, m.known.Snapshot("")); err != nil {
		return nil, public(err)
	}
	response, err := invoke(ctx, m, "RevertMemory", request, m.rpc.RevertMemory)
	if err != nil {
		return nil, err
	}
	return toRevertResult(response), nil
}

// ImportMemories contributes many memories at once. Each is judged on its
// own, so a refused memory does not stop the others. A batch larger than the
// service's cap is sent as several calls, one after another, each group checked
// just before it is sent; an import has no operation id and cannot be undone.
//
// Parameters:
//   - ctx: bounds the import. With a deadline, it bounds every call; without
//     one, each call gets [Options.Timeout]. Once ctx ends, no further group
//     is sent.
//   - memories: the memories to import, each with at least one query and one
//     insight.
//   - p: Domain or SessionID names the domain every memory is imported into.
//
// It returns one outcome per memory, indexed by its position in memories.
//
// Errors: [*InvalidRequestError] when the scope is missing, memories is empty,
// or a memory is incomplete or larger than the service allows, naming the
// memory by position (groups before it have already been sent);
// [*PermissionError]; [*ResourceExhaustedError]; [*TimeoutError] or
// [*InternalError] when ctx ended between groups; [*ConfigError] when the client is closed.
func (m *MemoryOperations) ImportMemories(ctx context.Context, memories []ImportedMemory, p ImportMemoriesParams) (*ImportResult, error) {
	request := &memoryv1.ImportMemoriesRequest{Domain: p.Domain, SessionId: p.SessionID, Memories: wireMemories(memories)}
	var parts []importPart
	send := func(ctx context.Context, offset int, part *memoryv1.ImportMemoriesRequest) error {
		response, err := invoke(ctx, m, "ImportMemories", part, m.rpc.ImportMemories)
		if err != nil {
			return err
		}
		parts = append(parts, importPart{offset, response})
		return nil
	}
	if err := memory.Import(ctx, request, m.known.Snapshot(p.Domain), m.log, send); err != nil {
		return nil, public(err)
	}
	return toImportResult(parts), nil
}
