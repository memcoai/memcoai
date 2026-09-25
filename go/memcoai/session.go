package memcoai

import (
	"context"

	"github.com/memcoai/memcoai/go/internal/transport"
)

// Session is an open session in one domain. Every call made through it is
// recorded under it, and supplies its domain. Open one with
// [MemoryOperations.StartSession]. A session opened with [ExternalID] acts
// for one of your users under a key of its own, which [Session.Close] ends;
// any other session holds nothing, and stays usable. A Session is safe for
// concurrent use.
type Session struct {
	// ID is the session's handle.
	ID string
	// Instructions is what the service said when the session opened.
	Instructions Instructions

	ops          *MemoryOperations
	catalog      []ToolDescriptor
	catalogKnown bool
	// key is what an impersonated session holds, and Close ends; nil for any
	// other session.
	key *transport.Renewing
}

// ScopedSearchParams narrow a search made in a session.
type ScopedSearchParams struct {
	// Tags narrow or boost the results.
	Tags []Tag
}

// ScopedCreateMemoryParams describe a memory created in a session.
type ScopedCreateMemoryParams struct {
	// Query is what someone would search to find the memory. Required.
	Query string
	// Title is a short title for the memory. Required.
	Title string
	// Content is the knowledge being saved. Required.
	Content string
	// Tags describe the memory.
	Tags []Tag
	// Source is who produced the content; unset is the agent.
	Source DataSource
}

// ScopedEnrichMemoryParams describe an addition made in a session.
type ScopedEnrichMemoryParams struct {
	// MemoryIdx is the memory to add to, or [NewMemory]. Required.
	MemoryIdx string
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

// Search retrieves memories for a task-based query, recorded under this
// session.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - query: what to find, such as a question or a task description.
//   - p: tags that narrow or boost the results.
//
// It returns the memories selected. Their [Memory.Feedback] rates them under
// this session.
//
// Errors: as [MemoryOperations.Search].
func (s *Session) Search(ctx context.Context, query string, p ScopedSearchParams) (*SearchResult, error) {
	return s.ops.Search(ctx, query, SearchParams{SessionID: s.ID, Tags: p.Tags})
}

// GetMemory fetches the memory behind a handle a search returned, bound to
// this session so [Memory.Feedback] can rate it.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - idx: a memory's or an insight's handle, copied from a search.
//
// It returns the memory.
//
// Errors: as [MemoryOperations.GetMemory].
func (s *Session) GetMemory(ctx context.Context, idx string) (*Memory, error) {
	return s.ops.getMemory(ctx, idx, s.ID)
}

// CreateMemory saves new knowledge learned during this session, into its
// domain.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: what to save. Query, Title and Content are required; a Source left
//     unset is the agent.
//
// It returns the operation's id, which [Session.RevertMemory] undoes, or an
// empty id when the service could not mint one.
//
// Errors: as [MemoryOperations.CreateMemory].
func (s *Session) CreateMemory(ctx context.Context, p ScopedCreateMemoryParams) (*WriteResult, error) {
	return s.ops.CreateMemory(ctx, CreateMemoryParams{
		Query: p.Query, Title: p.Title, Content: p.Content, SessionID: s.ID, Tags: p.Tags, Source: p.Source,
	})
}

// EnrichMemory adds to a memory this session's searches returned, or opens
// a new one.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - p: what to add. MemoryIdx is a handle from this session's searches, or
//     [NewMemory]; Title and Content are required.
//
// It returns the operation's id.
//
// Errors: as [MemoryOperations.EnrichMemory].
func (s *Session) EnrichMemory(ctx context.Context, p ScopedEnrichMemoryParams) (*WriteResult, error) {
	return s.ops.EnrichMemory(ctx, EnrichMemoryParams{
		MemoryIdx: p.MemoryIdx, SessionID: s.ID, Title: p.Title, Content: p.Content,
		Tags: p.Tags, Sources: p.Sources, Source: p.Source,
	})
}

// ShareFeedback rates the results of this session's searches.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - feedback: one rating per result; at least one.
//
// It returns the ratings recorded, with any advice they earned.
//
// Errors: as [MemoryOperations.ShareFeedback].
func (s *Session) ShareFeedback(ctx context.Context, feedback []FeedbackRating) (*FeedbackResult, error) {
	return s.ops.ShareFeedback(ctx, s.ID, feedback)
}

// RevertMemory undoes the caller's own write. The session plays no part: an
// operation id names its write on its own.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline, [Options.Timeout] applies; if
//     ctx has already ended, nothing is sent.
//   - operationID: the id a write returned.
//
// It returns what the revert removed.
//
// Errors: as [MemoryOperations.RevertMemory].
func (s *Session) RevertMemory(ctx context.Context, operationID string) (*RevertResult, error) {
	return s.ops.RevertMemory(ctx, operationID)
}

// ImportMemories contributes many memories into this session's domain,
// recorded under the session.
//
// Parameters:
//   - ctx: bounds the import; see [MemoryOperations.ImportMemories].
//   - memories: the memories to import, each with at least one query and one
//     insight.
//
// It returns one outcome per memory, indexed by its position in memories.
//
// Errors: as [MemoryOperations.ImportMemories].
func (s *Session) ImportMemories(ctx context.Context, memories []ImportedMemory) (*ImportResult, error) {
	return s.ops.ImportMemories(ctx, memories, ImportMemoriesParams{SessionID: s.ID})
}

// Close closes the session, ending the key it holds if it acts for one of
// your users. Only a session opened with [ExternalID] holds anything: its
// key, which is revoked here rather than left live until it expires. Every
// later call through the session, its tools, or a memory it returned is then
// refused locally with a [*ConfigError]. A call still in flight keeps the key
// until it finishes, and the key is ended then.
//
// Ending the key is best effort, so Close returns nothing: if the service
// cannot end it, a warning naming the key's id is logged, closing the client
// tries again, and the key expires on its own regardless. After the client is
// closed, Close does nothing.
//
// A session opened with [ExternalID] and dropped without Close still has its
// key ended eventually: once the session, its tools and the memories it
// returned are all unreachable, the client's next call ends the key. Close
// ends it at once, which is what keeps the user under the service's cap on
// live keys.
//
// A session opened without [ExternalID] holds nothing, so closing it changes
// nothing and it stays usable. Closing more than once is safe.
//
// Parameters:
//   - ctx: carries values to the call that ends the key. Its cancellation
//     does not stop that call, which gets [Options.Timeout] instead: a key
//     left live counts against its user's cap.
func (s *Session) Close(ctx context.Context) {
	if s.key != nil {
		s.key.Close(ctx)
	}
}
