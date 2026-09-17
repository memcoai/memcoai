package memcoai

import (
	"context"
	"errors"
	"reflect"
	"runtime"
	"slices"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/proto"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

func sentAs[T proto.Message](t *testing.T, f *fixture, method string) T {
	t.Helper()
	request, ok := f.server.Memory.Request(method).(T)
	if !ok {
		t.Fatalf("%s was not called", method)
	}
	return request
}

func TestListDomainsReturnsTheDomains(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Domains: []*memoryv1.DomainEntry{{Slug: "coding"}}})
	got, err := f.client.Memory.ListDomains(ctx)
	if err != nil || len(got.Domains) != 1 || got.Domains[0].Slug != "coding" {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestListToolsReturnsTheCatalog(t *testing.T) {
	f := connected(t)
	got, err := f.client.Memory.ListTools(ctx)
	if err != nil || len(got) != 10 || got[0] != (ToolDescriptor{"list_domains", "list_domains tool", true}) {
		t.Fatalf("got %+v, %v", got, err)
	}
}

func TestSearchSendsWhatItWasGiven(t *testing.T) {
	f := connected(t)
	result, err := f.client.Memory.Search(ctx, "how", SearchParams{Domain: "coding", Tags: []Tag{{Type: "language", Value: "go", Version: "1.25"}}})
	if err != nil || result.SessionID != "session-a" {
		t.Fatalf("got %+v, %v", result, err)
	}
	version := "1.25"
	want := &memoryv1.SearchRequest{Query: "how", Domain: "coding", Tags: []*memoryv1.Tag{{Type: "language", Value: "go", Version: &version}}}
	if got := sentAs[*memoryv1.SearchRequest](t, f, "Search"); !proto.Equal(got, want) {
		t.Fatalf("sent %v", got)
	}
}

func TestStartSessionAlsoFetchesTheCatalog(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("StartSession", &memoryv1.StartSessionResponse{SessionId: "session-b", Instructions: wireInstructions})
	session, err := f.client.Memory.StartSession(ctx, "coding")
	if err != nil {
		t.Fatal(err)
	}
	if session.ID != "session-b" || session.Instructions.Content != "c" || !session.catalogKnown || len(session.catalog) != 10 {
		t.Fatalf("got %+v", session)
	}
	calls := f.server.Memory.Calls()
	if len(calls) != 2 || !slices.Contains(calls, "StartSession") || !slices.Contains(calls, "ListTools") {
		t.Fatalf("calls %v", calls)
	}
}

func TestABlankDomainOpensNothing(t *testing.T) {
	f := connected(t)
	_, err := f.client.Memory.StartSession(ctx, " ")
	invalid(t, err, "domain must not be empty")
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

func TestAFailedStartSessionIsReportedEvenIfTheCatalogArrived(t *testing.T) {
	f := connected(t)
	f.server.Memory.Delay("StartSession", 100*time.Millisecond)
	f.server.Memory.FailNext("StartSession", testserver.Failure{Code: codes.PermissionDenied, Details: "no"})
	_, err := f.client.Memory.StartSession(ctx, "coding")
	expect[*PermissionError](t, err)
	if calls := f.server.Memory.Calls(); !slices.Contains(calls, "ListTools") {
		t.Fatalf("control: the catalog was never fetched: %v", calls)
	}
}

func TestAFailedCatalogStillOpensTheSession(t *testing.T) {
	f := connected(t)
	for range 3 {
		f.server.Memory.FailNext("ListTools", testserver.Failure{Code: codes.Unavailable, Details: "down"})
	}
	session, err := f.client.Memory.StartSession(ctx, "coding")
	if err != nil || session.catalogKnown || session.ID != "session-a" {
		t.Fatalf("got %+v, %v", session, err)
	}
}

func TestACancelledStartSessionLeavesNothingRunning(t *testing.T) {
	f := connected(t)
	f.server.Memory.Delay("StartSession", time.Second)
	f.server.Memory.Delay("ListTools", time.Second)
	before := runtime.NumGoroutine()
	short, cancel := context.WithTimeout(ctx, 30*time.Millisecond)
	defer cancel()
	_, err := f.client.Memory.StartSession(short, "coding")
	expect[*TimeoutError](t, err)
	deadline := time.Now().Add(time.Second)
	for runtime.NumGoroutine() > before+2 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if after := runtime.NumGoroutine(); after > before+2 {
		t.Fatalf("goroutines %d -> %d", before, after)
	}
}

func TestGetMemoryReturnsTheRequestedHandle(t *testing.T) {
	f := connected(t)
	memory, err := f.client.Memory.GetMemory(ctx, "memory-a-1")
	if err != nil || memory.Idx != "memory-a-1" {
		t.Fatalf("got %+v, %v", memory, err)
	}
	f.server.Memory.Respond("GetMemory", &memoryv1.GetMemoryResponse{})
	_, err = f.client.Memory.GetMemory(ctx, "memory-a-2")
	if got := as[*NotFoundError](t, err); got.Detail != `no memory was returned for "memory-a-2"` {
		t.Fatalf("got %q", got.Detail)
	}
}

func TestCreateMemorySendsEveryFieldAndDefaultsTheSource(t *testing.T) {
	f := connected(t)
	result, err := f.client.Memory.CreateMemory(ctx, CreateMemoryParams{
		Query: "q", Title: "t", Content: "c", SessionID: "session-a", Tags: []Tag{{Type: "a", Value: "b"}},
	})
	if err != nil || result.OperationID != "create-a" {
		t.Fatalf("got %+v, %v", result, err)
	}
	want := &memoryv1.CreateMemoryRequest{
		Query: "q", Title: "t", Content: "c", SessionId: "session-a",
		Tags: []*memoryv1.Tag{{Type: "a", Value: "b"}}, Source: memoryv1.DataSource_DATA_SOURCE_AGENT,
	}
	if got := sentAs[*memoryv1.CreateMemoryRequest](t, f, "CreateMemory"); !proto.Equal(got, want) {
		t.Fatalf("sent %v", got)
	}
	if _, err := f.client.Memory.CreateMemory(ctx, CreateMemoryParams{Query: "q", Title: "t", Content: "c", Domain: "d", Source: DataSourceUser}); err != nil {
		t.Fatal(err)
	}
	if got := sentAs[*memoryv1.CreateMemoryRequest](t, f, "CreateMemory"); got.GetSource() != memoryv1.DataSource_DATA_SOURCE_USER {
		t.Fatalf("source %v", got.GetSource())
	}
	f.server.Memory.Respond("CreateMemory", &memoryv1.CreateMemoryResponse{})
	if result, err := f.client.Memory.CreateMemory(ctx, CreateMemoryParams{Query: "q", Title: "t", Content: "c", Domain: "d"}); err != nil || result.OperationID != "" {
		t.Fatalf("an un-revertible write: %+v, %v", result, err)
	}
}

func TestEnrichMemoryNamesItsMemory(t *testing.T) {
	f := connected(t)
	result, err := f.client.Memory.EnrichMemory(ctx, EnrichMemoryParams{
		MemoryIdx: NewMemory, SessionID: "session-a", Title: "t", Content: "c", Sources: []string{"memory-b-1"},
	})
	if err != nil || result.OperationID != "enrich-a" {
		t.Fatalf("got %+v, %v", result, err)
	}
	want := &memoryv1.EnrichMemoryRequest{
		MemoryIdx: "new", SessionId: "session-a", Title: "t", Content: "c",
		Sources: []string{"memory-b-1"}, Source: memoryv1.DataSource_DATA_SOURCE_AGENT,
	}
	if got := sentAs[*memoryv1.EnrichMemoryRequest](t, f, "EnrichMemory"); !proto.Equal(got, want) {
		t.Fatalf("sent %v", got)
	}
}

func TestShareFeedbackSendsTheRatings(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("ShareFeedback", &memoryv1.ShareFeedbackResponse{
		SessionId: "session-a", Entries: []*memoryv1.FeedbackEntry{{Idx: "i", Relevant: true, Advice: "good"}},
	})
	result, err := f.client.Memory.ShareFeedback(ctx, "session-a", []FeedbackRating{{Idx: "i", Relevant: true, Comment: "useful"}})
	if err != nil || result.Entries[0].Advice != "good" {
		t.Fatalf("got %+v, %v", result, err)
	}
	comment := "useful"
	want := &memoryv1.ShareFeedbackRequest{SessionId: "session-a", Feedback: []*memoryv1.FeedbackRating{{Idx: "i", Relevant: true, Comment: &comment}}}
	if got := sentAs[*memoryv1.ShareFeedbackRequest](t, f, "ShareFeedback"); !proto.Equal(got, want) {
		t.Fatalf("sent %v", got)
	}
}

func TestARevertThatFindsNothingIsAnOutcome(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("RevertMemory", &memoryv1.RevertMemoryResponse{OperationId: "create-a", Outcome: memoryv1.RevertOutcome_REVERT_OUTCOME_NOT_FOUND})
	result, err := f.client.Memory.RevertMemory(ctx, "create-a")
	if err != nil || result.Outcome != RevertOutcomeNotFound {
		t.Fatalf("got %+v, %v", result, err)
	}
	if got := sentAs[*memoryv1.RevertMemoryRequest](t, f, "RevertMemory"); got.GetOpId() != "create-a" {
		t.Fatalf("sent %v", got)
	}
}

func TestAnImportNeedsAScope(t *testing.T) {
	f := connected(t)
	_, err := f.client.Memory.ImportMemories(ctx, []ImportedMemory{{Queries: []string{"q"}, Insights: []ImportedInsight{{"t", "c"}}}}, ImportMemoriesParams{})
	invalid(t, err, "pass a domain or a session_id: a request needs one of them to name a domain")
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

func batch(n int) []ImportedMemory {
	out := make([]ImportedMemory, n)
	for i := range out {
		out[i] = ImportedMemory{Queries: []string{"q"}, Insights: []ImportedInsight{{Title: "t", Content: "c"}}}
	}
	return out
}

// The check between groups is pinned in internal/memory; this is the call in flight.
func TestAnImportCancelledDuringAGroupSendsNoMore(t *testing.T) {
	f := started(t)
	f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Limits: &memoryv1.Limits{MaxImportMemories: 2}})
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	f.server.Forget()
	f.server.Memory.Delay("ImportMemories", 100*time.Millisecond)
	short, cancel := context.WithCancel(ctx)
	go func() {
		for len(f.server.Memory.Calls()) == 0 {
			time.Sleep(time.Millisecond)
		}
		cancel()
	}()
	_, err := f.client.Memory.ImportMemories(short, batch(6), ImportMemoriesParams{Domain: "d"})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	expect[Error](t, err)
	if calls := f.server.Memory.Calls(); len(calls) != 1 {
		t.Fatalf("calls %v", calls)
	}
}

func TestMemoryFeedbackIsSentUnderTheSessionThatReturnedIt(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("Search", &memoryv1.SearchResponse{
		SessionId: "session-s", Memories: []*memoryv1.MemoryResult{{Idx: "memory-a-1"}},
	})
	f.server.Memory.Respond("ShareFeedback", &memoryv1.ShareFeedbackResponse{Entries: []*memoryv1.FeedbackEntry{{Idx: "memory-a-1", Correct: true}}})
	result, err := f.client.Memory.Search(ctx, "q", SearchParams{Domain: "coding"})
	if err != nil {
		t.Fatal(err)
	}
	entry, err := result.Memories[0].Feedback(ctx, MemoryFeedback{Relevant: true, Correct: true, Comment: "why"})
	if err != nil || !entry.Correct {
		t.Fatalf("got %+v, %v", entry, err)
	}
	comment := "why"
	want := &memoryv1.ShareFeedbackRequest{SessionId: "session-s", Feedback: []*memoryv1.FeedbackRating{{Idx: "memory-a-1", Relevant: true, Correct: true, Comment: &comment}}}
	if got := sentAs[*memoryv1.ShareFeedbackRequest](t, f, "ShareFeedback"); !proto.Equal(got, want) {
		t.Fatalf("sent %v", got)
	}
	if _, err := result.Memories[0].Feedback(ctx, MemoryFeedback{Relevant: false}); err != nil {
		t.Fatalf("a second rating: %v", err)
	}
}

func TestFeedbackOnAFetchedMemoryNeedsASession(t *testing.T) {
	f := connected(t)
	memory, err := f.client.Memory.GetMemory(ctx, "memory-a-1")
	if err != nil {
		t.Fatal(err)
	}
	f.server.Forget()
	_, err = memory.Feedback(ctx, MemoryFeedback{Relevant: true})
	invalid(t, err, "session_id must not be empty")
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

func TestFeedbackOnAHandBuiltMemoryIsRefused(t *testing.T) {
	_, err := Memory{Idx: "memory-a-1"}.Feedback(ctx, MemoryFeedback{})
	invalid(t, err, "this memory has no session to record feedback against")
}

func TestFeedbackTheServiceDidNotRecordIsInternal(t *testing.T) {
	f := connected(t)
	session, err := f.client.Memory.StartSession(ctx, "coding")
	if err != nil {
		t.Fatal(err)
	}
	memory, err := session.GetMemory(ctx, "memory-a-1")
	if err != nil {
		t.Fatal(err)
	}
	_, err = memory.Feedback(ctx, MemoryFeedback{Relevant: true})
	if got := as[*InternalError](t, err); got.Code != codes.Internal || got.Detail != `the service recorded no feedback entry for "memory-a-1"` {
		t.Fatalf("got %+v", got)
	}
	if got := sentAs[*memoryv1.ShareFeedbackRequest](t, f, "ShareFeedback"); got.GetSessionId() != "session-a" {
		t.Fatalf("sent %v", got)
	}
}

func TestFeedbackAfterCloseIsRefused(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("Search", &memoryv1.SearchResponse{SessionId: "s", Memories: []*memoryv1.MemoryResult{{Idx: "m"}}})
	result, err := f.client.Memory.Search(ctx, "q", SearchParams{Domain: "coding"})
	if err != nil {
		t.Fatal(err)
	}
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	_, err = result.Memories[0].Feedback(ctx, MemoryFeedback{})
	if got := as[*ConfigError](t, err); got.Message != closedMessage {
		t.Fatalf("got %q", got.Message)
	}
}

func TestEveryOperationRejectsLocallyWithoutSending(t *testing.T) {
	f := connected(t)
	m := f.client.Memory
	checks := map[string]error{
		"query must not be empty": func() error { _, err := m.Search(ctx, "", SearchParams{Domain: "d"}); return err }(),
		"idx must not be empty":   func() error { _, err := m.GetMemory(ctx, " "); return err }(),
		"title must not be empty": func() error {
			_, err := m.CreateMemory(ctx, CreateMemoryParams{Query: "q", Domain: "d"})
			return err
		}(),
		"memory_idx must not be empty": func() error {
			_, err := m.EnrichMemory(ctx, EnrichMemoryParams{SessionID: "s", Title: "t", Content: "c"})
			return err
		}(),
		"feedback must contain at least one rating": func() error { _, err := m.ShareFeedback(ctx, "s", nil); return err }(),
		"operation_id must not be empty":            func() error { _, err := m.RevertMemory(ctx, ""); return err }(),
		"memories must contain at least one memory": func() error {
			_, err := m.ImportMemories(ctx, nil, ImportMemoriesParams{Domain: "d"})
			return err
		}(),
	}
	for want, err := range checks {
		invalid(t, err, want)
	}
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
	if !reflect.DeepEqual(f.server.Health.Checked(), []string(nil)) {
		t.Fatalf("probes %v", f.server.Health.Checked())
	}
}
