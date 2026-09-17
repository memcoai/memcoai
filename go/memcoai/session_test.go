package memcoai

import (
	"reflect"
	"testing"

	"google.golang.org/protobuf/proto"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

func opened(t *testing.T, f *fixture) *Session {
	t.Helper()
	session, err := f.client.Memory.StartSession(ctx, "coding")
	if err != nil {
		t.Fatal(err)
	}
	f.server.Forget()
	return session
}

func TestASessionCarriesItsIDAndInstructions(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("StartSession", &memoryv1.StartSessionResponse{SessionId: "session-z", Instructions: wireInstructions})
	session := opened(t, f)
	if session.ID != "session-z" || session.Instructions != (Instructions{"c", "p", "a", "r", "n"}) {
		t.Fatalf("got %+v", session)
	}
}

func TestTheSessionIsBoundOntoEveryScopedCall(t *testing.T) {
	f := connected(t)
	s := opened(t, f)
	if _, err := s.Search(ctx, "q", ScopedSearchParams{Tags: []Tag{{Type: "a", Value: "b"}}}); err != nil {
		t.Fatal(err)
	}
	if got := sentAs[*memoryv1.SearchRequest](t, f, "Search"); !proto.Equal(got, &memoryv1.SearchRequest{
		Query: "q", SessionId: "session-a", Tags: []*memoryv1.Tag{{Type: "a", Value: "b"}},
	}) {
		t.Errorf("search %v", got)
	}
	if _, err := s.CreateMemory(ctx, ScopedCreateMemoryParams{Query: "q", Title: "t", Content: "c"}); err != nil {
		t.Fatal(err)
	}
	if got := sentAs[*memoryv1.CreateMemoryRequest](t, f, "CreateMemory"); got.GetSessionId() != "session-a" || got.GetDomain() != "" {
		t.Errorf("create %v", got)
	}
	if _, err := s.EnrichMemory(ctx, ScopedEnrichMemoryParams{MemoryIdx: "memory-a-1", Title: "t", Content: "c"}); err != nil {
		t.Fatal(err)
	}
	if got := sentAs[*memoryv1.EnrichMemoryRequest](t, f, "EnrichMemory"); got.GetSessionId() != "session-a" {
		t.Errorf("enrich %v", got)
	}
	if _, err := s.ShareFeedback(ctx, []FeedbackRating{{Idx: "i"}}); err != nil {
		t.Fatal(err)
	}
	if got := sentAs[*memoryv1.ShareFeedbackRequest](t, f, "ShareFeedback"); got.GetSessionId() != "session-a" {
		t.Errorf("feedback %v", got)
	}
	if _, err := s.ImportMemories(ctx, batch(1)); err != nil {
		t.Fatal(err)
	}
	if got := sentAs[*memoryv1.ImportMemoriesRequest](t, f, "ImportMemories"); got.GetSessionId() != "session-a" || got.GetDomain() != "" {
		t.Errorf("import %v", got)
	}
	if _, err := s.RevertMemory(ctx, "create-a"); err != nil {
		t.Fatal(err)
	}
	if got := sentAs[*memoryv1.RevertMemoryRequest](t, f, "RevertMemory"); got.GetOpId() != "create-a" {
		t.Errorf("revert %v", got)
	}
	memory, err := s.GetMemory(ctx, "memory-a-1")
	if err != nil || memory.sessionID != "session-a" {
		t.Fatalf("get %+v, %v", memory, err)
	}
}

func TestAScopedCallStillRejectsLocally(t *testing.T) {
	f := connected(t)
	s := opened(t, f)
	_, err := s.Search(ctx, " ", ScopedSearchParams{})
	invalid(t, err, "query must not be empty")
	_, err = s.EnrichMemory(ctx, ScopedEnrichMemoryParams{MemoryIdx: NewMemory, Title: "t"})
	invalid(t, err, "content must not be empty")
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

func TestAScopedImportIsSplitAndRenumbered(t *testing.T) {
	f := started(t)
	f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Limits: &memoryv1.Limits{MaxImportMemories: 2}})
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	s := opened(t, f)
	result, err := s.ImportMemories(ctx, batch(5))
	if err != nil {
		t.Fatal(err)
	}
	var indices []int
	for _, outcome := range result.Results {
		indices = append(indices, outcome.Index)
	}
	if !reflect.DeepEqual(indices, []int{0, 1, 2, 3, 4}) {
		t.Fatalf("indices %v", indices)
	}
	if calls := f.server.Memory.Calls(); len(calls) != 3 {
		t.Fatalf("calls %v", calls)
	}

	f.server.Forget()
	broken := batch(5)
	broken[4].Queries = nil
	_, err = s.ImportMemories(ctx, broken)
	invalid(t, err, "memories[4] must contain at least one query")
	if calls := f.server.Memory.Calls(); len(calls) != 2 {
		t.Fatalf("the groups before the bad entry were not sent first: %v", calls)
	}
}
