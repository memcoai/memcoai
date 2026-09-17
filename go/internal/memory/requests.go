package memory

import (
	"context"
	"fmt"
	"log/slog"

	"google.golang.org/protobuf/proto"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

// Each Check runs Node's checks in Node's order, trims what the service would
// trim, and last refuses text protobuf cannot encode.

// sendable catches what would otherwise fail inside the call as an internal
// marshalling error.
func sendable(message proto.Message) error {
	if _, err := proto.Marshal(message); err != nil {
		return fault.Invalid("a field value cannot be sent: " + err.Error())
	}
	return nil
}

func handle(value, field string, s Snapshot) error {
	return within(value, field, s.Limits.GetMaxIdxCharacters())
}

func CheckStartSession(req *memoryv1.StartSessionRequest) error {
	if err := present(req.GetDomain(), "domain"); err != nil {
		return err
	}
	return sendable(req)
}

func CheckSearch(req *memoryv1.SearchRequest, s Snapshot, log *slog.Logger) error {
	if err := present(req.GetQuery(), "query"); err != nil {
		return err
	}
	if err := checkScope(req.GetDomain(), req.GetSessionId()); err != nil {
		return err
	}
	if err := within(req.GetQuery(), "query", s.Limits.GetMaxQueryCharacters()); err != nil {
		return err
	}
	if err := checkTags(req.GetTags(), "tag"); err != nil {
		return err
	}
	req.Tags = trimmed(req.GetTags(), s.MaxTags, "tags", log)
	return sendable(req)
}

func CheckGetMemory(req *memoryv1.GetMemoryRequest, s Snapshot) error {
	if err := present(req.GetIdx(), "idx"); err != nil {
		return err
	}
	if err := handle(req.GetIdx(), "idx", s); err != nil {
		return err
	}
	return sendable(req)
}

func CheckCreate(req *memoryv1.CreateMemoryRequest, s Snapshot, log *slog.Logger) error {
	for _, check := range []error{
		present(req.GetQuery(), "query"),
		present(req.GetTitle(), "title"),
		present(req.GetContent(), "content"),
		checkScope(req.GetDomain(), req.GetSessionId()),
		within(req.GetQuery(), "query", s.Limits.GetMaxQueryCharacters()),
		together(req.GetTitle(), req.GetContent(), "title and content", s.Limits.GetMaxTextCharacters()),
		checkTags(req.GetTags(), "tag"),
	} {
		if check != nil {
			return check
		}
	}
	req.Tags = trimmed(req.GetTags(), s.MaxTags, "tags", log)
	return sendable(req)
}

func CheckEnrich(req *memoryv1.EnrichMemoryRequest, s Snapshot, log *slog.Logger) error {
	if req.GetMemoryIdx() != NewMemory {
		if err := present(req.GetMemoryIdx(), "memory_idx"); err != nil {
			return err
		}
	}
	checks := []error{
		present(req.GetSessionId(), "session_id"),
		present(req.GetTitle(), "title"),
		present(req.GetContent(), "content"),
	}
	for _, source := range req.GetSources() {
		checks = append(checks, present(source, "sources entry"))
	}
	checks = append(checks,
		together(req.GetTitle(), req.GetContent(), "title and content", s.Limits.GetMaxTextCharacters()),
		handle(req.GetMemoryIdx(), "memory_idx", s),
	)
	for _, source := range req.GetSources() {
		checks = append(checks, handle(source, "sources entry", s))
	}
	checks = append(checks, checkTags(req.GetTags(), "tag"))
	for _, check := range checks {
		if check != nil {
			return check
		}
	}
	req.Sources = trimmed(req.GetSources(), s.Limits.GetMaxSources(), "sources", log)
	// Tags are never trimmed here: an enrichment names no domain, so which
	// domain's cap applies is the service's to decide.
	return sendable(req)
}

func CheckShareFeedback(req *memoryv1.ShareFeedbackRequest, s Snapshot) error {
	if err := present(req.GetSessionId(), "session_id"); err != nil {
		return err
	}
	if len(req.GetFeedback()) == 0 {
		return fault.Invalid("feedback must contain at least one rating")
	}
	for _, rating := range req.GetFeedback() {
		if err := present(rating.GetIdx(), "feedback idx"); err != nil {
			return err
		}
	}
	if err := count(len(req.GetFeedback()), "feedback", s.Limits.GetMaxFeedbackEntries()); err != nil {
		return err
	}
	for _, rating := range req.GetFeedback() {
		if err := handle(rating.GetIdx(), "feedback idx", s); err != nil {
			return err
		}
	}
	return sendable(req)
}

func CheckRevert(req *memoryv1.RevertMemoryRequest, s Snapshot) error {
	if err := present(req.GetOpId(), "operation_id"); err != nil {
		return err
	}
	if err := handle(req.GetOpId(), "operation_id", s); err != nil {
		return err
	}
	return sendable(req)
}

// Import checks a batch and sends it in the groups the service's cap allows,
// one after another, checking each group just before it is sent.
func Import(ctx context.Context, req *memoryv1.ImportMemoriesRequest, s Snapshot, log *slog.Logger,
	send func(ctx context.Context, offset int, part *memoryv1.ImportMemoriesRequest) error) error {
	if err := checkScope(req.GetDomain(), req.GetSessionId()); err != nil {
		return err
	}
	memories := req.GetMemories()
	if len(memories) == 0 {
		return fault.Invalid("memories must contain at least one memory")
	}
	size := len(memories)
	if limit := s.Limits.GetMaxImportMemories(); limit > 0 {
		size = int(limit)
	}
	for offset := 0; offset < len(memories); offset += size {
		if ctx.Err() != nil {
			return fault.FromContext(ctx)
		}
		group := memories[offset:min(offset+size, len(memories))]
		for i, m := range group {
			if err := checkImported(m, offset+i, s, log); err != nil {
				return err
			}
		}
		part := &memoryv1.ImportMemoriesRequest{Domain: req.GetDomain(), SessionId: req.GetSessionId(), Memories: group}
		if err := sendable(part); err != nil {
			return err
		}
		if err := send(ctx, offset, part); err != nil {
			return err
		}
	}
	return nil
}

func checkImported(m *memoryv1.ImportedMemory, index int, s Snapshot, log *slog.Logger) error {
	where := fmt.Sprintf("memories[%d]", index)
	if len(m.GetQueries()) == 0 {
		return fault.Invalid(where + " must contain at least one query")
	}
	if len(m.GetInsights()) == 0 {
		return fault.Invalid(where + " must contain at least one insight")
	}
	for j, query := range m.GetQueries() {
		if err := present(query, fmt.Sprintf("%s queries[%d]", where, j)); err != nil {
			return err
		}
	}
	for j, insight := range m.GetInsights() {
		if err := present(insight.GetTitle(), fmt.Sprintf("%s insights[%d] title", where, j)); err != nil {
			return err
		}
		if err := present(insight.GetContent(), fmt.Sprintf("%s insights[%d] content", where, j)); err != nil {
			return err
		}
	}
	if err := checkTags(m.GetTags(), where+" tag"); err != nil {
		return err
	}
	limits := s.Limits
	for _, check := range []error{
		count(len(m.GetQueries()), where+" queries", limits.GetMaxImportQueriesPerMemory()),
		count(len(m.GetInsights()), where+" insights", limits.GetMaxImportInsightsPerMemory()),
		count(len(m.GetTags()), where+" tags", limits.GetMaxImportTagsPerMemory()),
	} {
		if check != nil {
			return check
		}
	}
	for j, insight := range m.GetInsights() {
		field := fmt.Sprintf("%s insights[%d] title and content", where, j)
		if err := together(insight.GetTitle(), insight.GetContent(), field, limits.GetMaxTextCharacters()); err != nil {
			return err
		}
	}
	m.Tags = trimmed(m.GetTags(), s.MaxTags, "tags", log)
	return nil
}
