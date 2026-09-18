package memory

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

var invalidUTF8 = string([]byte{0xff, 'x'})

func tags(pairs ...string) []*memoryv1.Tag {
	var out []*memoryv1.Tag
	for i := 0; i+1 < len(pairs); i += 2 {
		out = append(out, &memoryv1.Tag{Type: pairs[i], Value: pairs[i+1]})
	}
	return out
}

func capped(limits *memoryv1.Limits, maxTags int32) Snapshot {
	return Snapshot{Limits: limits, MaxTags: maxTags}
}

var none = Snapshot{}

func TestAStartedSessionNeedsADomain(t *testing.T) {
	rejected(t, CheckStartSession(&memoryv1.StartSessionRequest{Domain: " "}), "domain must not be empty")
	if err := CheckStartSession(&memoryv1.StartSessionRequest{Domain: "coding"}); err != nil {
		t.Fatal(err)
	}
	rejected(t, CheckStartSession(&memoryv1.StartSessionRequest{Domain: invalidUTF8}), "a field value cannot be sent: "+utf8Complaint(t))
}

// utf8Complaint is what protobuf says about invalid UTF-8, whatever its wording.
func utf8Complaint(t *testing.T) string {
	t.Helper()
	err := CheckStartSession(&memoryv1.StartSessionRequest{Domain: invalidUTF8})
	var f *fault.Error
	if !errors.As(err, &f) || !strings.HasPrefix(f.Detail, "a field value cannot be sent: ") || !strings.Contains(f.Detail, "UTF-8") {
		t.Fatalf("got %v", err)
	}
	return strings.TrimPrefix(f.Detail, "a field value cannot be sent: ")
}

func TestASearchIsCheckedInOrder(t *testing.T) {
	log, _ := debugLog()
	cases := []struct {
		req  *memoryv1.SearchRequest
		want string
	}{
		{&memoryv1.SearchRequest{Query: " ", Domain: "coding"}, "query must not be empty"},
		{&memoryv1.SearchRequest{Query: "q"}, "pass a domain or a session_id: a request needs one of them to name a domain"},
		{&memoryv1.SearchRequest{Query: "q", Domain: " "}, "domain must not be empty"},
		{&memoryv1.SearchRequest{Query: "q", SessionId: " "}, "session_id must not be empty"},
		{&memoryv1.SearchRequest{Query: "q", Domain: "coding", Tags: tags("", "go")}, "tag type must not be empty"},
		{&memoryv1.SearchRequest{Query: "q", Domain: "coding", Tags: tags("language", "")}, "tag value must not be empty"},
	}
	for _, c := range cases {
		rejected(t, CheckSearch(c.req, none, log), c.want)
	}
	if err := CheckSearch(&memoryv1.SearchRequest{Query: "q", SessionId: "session-a"}, none, log); err != nil {
		t.Fatal(err)
	}
}

func TestNoCapAppliesBeforeAnyIsKnown(t *testing.T) {
	log, _ := debugLog()
	long := strings.Repeat("x", 100_000)
	req := &memoryv1.SearchRequest{Query: long, Domain: "coding", Tags: tags("a", "1", "b", "2", "c", "3")}
	if err := CheckSearch(req, none, log); err != nil || len(req.Tags) != 3 {
		t.Fatalf("got %v, %d tags", err, len(req.Tags))
	}
	create := &memoryv1.CreateMemoryRequest{Query: long, Title: long, Content: long, Domain: "coding"}
	if err := CheckCreate(create, none, log); err != nil {
		t.Fatal(err)
	}
}

func TestAnOverLongQueryIsRefusedOnceTheCapIsKnown(t *testing.T) {
	log, _ := debugLog()
	limits := &memoryv1.Limits{MaxQueryCharacters: 3}
	rejected(t, CheckSearch(&memoryv1.SearchRequest{Query: "four", Domain: "d"}, capped(limits, 0), log),
		"query is 4 characters, which exceeds the limit of 3")
	rejected(t, CheckCreate(&memoryv1.CreateMemoryRequest{Query: "four", Title: "t", Content: "c", Domain: "d"}, capped(limits, 0), log),
		"query is 4 characters, which exceeds the limit of 3")
}

func TestExcessTagsAreTrimmedAgainstTheNamedDomain(t *testing.T) {
	log, out := debugLog()
	original := tags("a", "1", "b", "2", "c", "3")
	req := &memoryv1.SearchRequest{Query: "q", Domain: "coding", Tags: original}
	if err := CheckSearch(req, capped(&memoryv1.Limits{}, 2), log); err != nil {
		t.Fatal(err)
	}
	if len(req.Tags) != 2 || req.Tags[1].GetType() != "b" || len(original) != 3 {
		t.Fatalf("got %v from %v", req.Tags, original)
	}
	if !strings.Contains(out.String(), "field=tags from=3 to=2") {
		t.Fatalf("got %q", out.String())
	}
}

func TestACreateIsCheckedInOrder(t *testing.T) {
	log, _ := debugLog()
	valid := func() *memoryv1.CreateMemoryRequest {
		return &memoryv1.CreateMemoryRequest{Query: "q", Title: "t", Content: "c", Domain: "coding"}
	}
	cases := []struct {
		edit func(*memoryv1.CreateMemoryRequest)
		want string
	}{
		{func(r *memoryv1.CreateMemoryRequest) { r.Query, r.Title = "", "" }, "query must not be empty"},
		{func(r *memoryv1.CreateMemoryRequest) { r.Title, r.Content = " ", "" }, "title must not be empty"},
		{func(r *memoryv1.CreateMemoryRequest) { r.Content, r.Domain = "", "" }, "content must not be empty"},
		{func(r *memoryv1.CreateMemoryRequest) { r.Domain = "" }, "pass a domain or a session_id: a request needs one of them to name a domain"},
		{func(r *memoryv1.CreateMemoryRequest) { r.Tags = tags(" ", "x") }, "tag type must not be empty"},
		{func(r *memoryv1.CreateMemoryRequest) { r.Content = invalidUTF8 }, "a field value cannot be sent: " + utf8Complaint(t)},
	}
	for _, c := range cases {
		req := valid()
		c.edit(req)
		rejected(t, CheckCreate(req, none, log), c.want)
	}
	if err := CheckCreate(valid(), none, log); err != nil {
		t.Fatal(err)
	}
}

func TestTitleAndContentAreBoundedTogether(t *testing.T) {
	log, _ := debugLog()
	limits := &memoryv1.Limits{MaxTextCharacters: 100}
	req := &memoryv1.CreateMemoryRequest{Query: "q", Title: strings.Repeat("t", 60), Content: strings.Repeat("c", 60), Domain: "d"}
	rejected(t, CheckCreate(req, capped(limits, 0), log),
		"title and content are 120 characters together, which exceeds the combined limit of 100")
	req.Content = strings.Repeat("c", 40)
	if err := CheckCreate(req, capped(limits, 0), log); err != nil {
		t.Fatal(err)
	}
}

func TestAnEnrichmentIsCheckedInOrder(t *testing.T) {
	log, _ := debugLog()
	valid := func() *memoryv1.EnrichMemoryRequest {
		return &memoryv1.EnrichMemoryRequest{MemoryIdx: "memory-a-1", SessionId: "session-a", Title: "t", Content: "c"}
	}
	cases := []struct {
		edit func(*memoryv1.EnrichMemoryRequest)
		want string
	}{
		{func(r *memoryv1.EnrichMemoryRequest) { r.MemoryIdx, r.SessionId = " ", "" }, "memory_idx must not be empty"},
		{func(r *memoryv1.EnrichMemoryRequest) { r.SessionId, r.Title = "", "" }, "session_id must not be empty"},
		{func(r *memoryv1.EnrichMemoryRequest) { r.Title, r.Content = "", "" }, "title must not be empty"},
		{func(r *memoryv1.EnrichMemoryRequest) { r.Content = "" }, "content must not be empty"},
		{func(r *memoryv1.EnrichMemoryRequest) { r.Sources = []string{"memory-b-1", " "} }, "sources entry must not be empty"},
		{func(r *memoryv1.EnrichMemoryRequest) { r.Tags = tags("x", "") }, "tag value must not be empty"},
	}
	for _, c := range cases {
		req := valid()
		c.edit(req)
		rejected(t, CheckEnrich(req, none, log), c.want)
	}
}

func TestOnlyTheExactSentinelOpensANewMemory(t *testing.T) {
	log, _ := debugLog()
	req := &memoryv1.EnrichMemoryRequest{MemoryIdx: NewMemory, SessionId: "s", Title: "t", Content: "c"}
	if err := CheckEnrich(req, none, log); err != nil {
		t.Fatal(err)
	}
	req.MemoryIdx = "New"
	if err := CheckEnrich(req, none, log); err != nil {
		t.Fatalf("an ordinary handle was refused: %v", err)
	}
}

func TestAnEnrichmentsHandlesAndSourcesAreCapped(t *testing.T) {
	log, out := debugLog()
	limits := &memoryv1.Limits{MaxIdxCharacters: 8, MaxSources: 2, MaxTextCharacters: 100}
	req := &memoryv1.EnrichMemoryRequest{MemoryIdx: "memory-a-1", SessionId: "s", Title: "t", Content: "c"}
	rejected(t, CheckEnrich(req, capped(limits, 0), log), "memory_idx is 10 characters, which exceeds the limit of 8")

	req.MemoryIdx = NewMemory
	req.Sources = []string{"short", "far-too-long"}
	rejected(t, CheckEnrich(req, capped(limits, 0), log), "sources entry is 12 characters, which exceeds the limit of 8")

	original := []string{"a", "b", "c"}
	req.Sources = original
	req.Tags = tags("a", "1", "b", "2", "c", "3")
	if err := CheckEnrich(req, capped(limits, 1), log); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(req.Sources, []string{"a", "b"}) || !reflect.DeepEqual(original, []string{"a", "b", "c"}) {
		t.Fatalf("sources %v from %v", req.Sources, original)
	}
	if len(req.Tags) != 3 {
		t.Fatalf("an enrichment's tags were trimmed: %v", req.Tags)
	}
	if !strings.Contains(out.String(), "field=sources from=3 to=2") {
		t.Fatalf("got %q", out.String())
	}
}

func TestFeedbackIsCheckedAndCapped(t *testing.T) {
	rejected(t, CheckShareFeedback(&memoryv1.ShareFeedbackRequest{SessionId: " "}, none), "session_id must not be empty")
	rejected(t, CheckShareFeedback(&memoryv1.ShareFeedbackRequest{SessionId: "s"}, none), "feedback must contain at least one rating")
	rejected(t, CheckShareFeedback(&memoryv1.ShareFeedbackRequest{
		SessionId: "s",
		Feedback:  []*memoryv1.FeedbackRating{{Idx: "memory-a-1"}, {Idx: " "}},
	}, none), "feedback idx must not be empty")

	limits := &memoryv1.Limits{MaxFeedbackEntries: 1, MaxIdxCharacters: 5}
	two := []*memoryv1.FeedbackRating{{Idx: "a"}, {Idx: "b"}}
	rejected(t, CheckShareFeedback(&memoryv1.ShareFeedbackRequest{SessionId: "s", Feedback: two}, capped(limits, 0)),
		"feedback has 2 entries, which exceeds the limit of 1")
	long := []*memoryv1.FeedbackRating{{Idx: "memory-a-1"}}
	rejected(t, CheckShareFeedback(&memoryv1.ShareFeedbackRequest{SessionId: "s", Feedback: long}, capped(limits, 0)),
		"feedback idx is 10 characters, which exceeds the limit of 5")
	comment := invalidUTF8
	rejected(t, CheckShareFeedback(&memoryv1.ShareFeedbackRequest{
		SessionId: "s",
		Feedback:  []*memoryv1.FeedbackRating{{Idx: "a", Comment: &comment}},
	}, none), "a field value cannot be sent: "+utf8Complaint(t))
}

func TestHandlesAreCheckedForPresenceThenLength(t *testing.T) {
	limits := capped(&memoryv1.Limits{MaxIdxCharacters: 3}, 0)
	rejected(t, CheckGetMemory(&memoryv1.GetMemoryRequest{Idx: " "}, none), "idx must not be empty")
	rejected(t, CheckGetMemory(&memoryv1.GetMemoryRequest{Idx: "abcd"}, limits), "idx is 4 characters, which exceeds the limit of 3")
	rejected(t, CheckRevert(&memoryv1.RevertMemoryRequest{OpId: ""}, none), "operation_id must not be empty")
	rejected(t, CheckRevert(&memoryv1.RevertMemoryRequest{OpId: "abcd"}, limits), "operation_id is 4 characters, which exceeds the limit of 3")
	if err := CheckRevert(&memoryv1.RevertMemoryRequest{OpId: "abc"}, limits); err != nil {
		t.Fatal(err)
	}
}

func memories(n int) []*memoryv1.ImportedMemory {
	out := make([]*memoryv1.ImportedMemory, n)
	for i := range out {
		out[i] = &memoryv1.ImportedMemory{
			Queries:  []string{"q"},
			Insights: []*memoryv1.ImportedInsight{{Title: "t", Content: "c"}},
		}
	}
	return out
}

type sent struct {
	offsets []int
	parts   []*memoryv1.ImportMemoriesRequest
}

func (s *sent) send(_ context.Context, offset int, part *memoryv1.ImportMemoriesRequest) error {
	s.offsets = append(s.offsets, offset)
	s.parts = append(s.parts, part)
	return nil
}

func TestAnImportNeedsAScopeAndAMemory(t *testing.T) {
	log, _ := debugLog()
	var calls sent
	rejected(t, Import(context.Background(), &memoryv1.ImportMemoriesRequest{Memories: memories(1)}, none, log, calls.send),
		"pass a domain or a session_id: a request needs one of them to name a domain")
	rejected(t, Import(context.Background(), &memoryv1.ImportMemoriesRequest{Domain: "d"}, none, log, calls.send),
		"memories must contain at least one memory")
	if len(calls.parts) != 0 {
		t.Fatalf("sent %d parts", len(calls.parts))
	}
}

func TestAnImportIsSplitByTheServicesCap(t *testing.T) {
	log, _ := debugLog()
	var calls sent
	req := &memoryv1.ImportMemoriesRequest{Domain: "d", SessionId: "s", Memories: memories(5)}
	if err := Import(context.Background(), req, capped(&memoryv1.Limits{MaxImportMemories: 2}, 0), log, calls.send); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(calls.offsets, []int{0, 2, 4}) {
		t.Fatalf("offsets %v", calls.offsets)
	}
	for _, part := range calls.parts {
		if part.GetDomain() != "d" || part.GetSessionId() != "s" {
			t.Fatalf("part %v", part)
		}
	}
	if len(calls.parts[0].Memories) != 2 || len(calls.parts[2].Memories) != 1 {
		t.Fatalf("parts %v", calls.parts)
	}

	calls = sent{}
	if err := Import(context.Background(), req, none, log, calls.send); err != nil || !reflect.DeepEqual(calls.offsets, []int{0}) {
		t.Fatalf("without a cap: %v %v", calls.offsets, err)
	}
	if len(calls.parts[0].Memories) != 5 {
		t.Fatalf("parts %v", calls.parts)
	}
}

func TestEachImportedMemoryIsCheckedByItsPosition(t *testing.T) {
	log, _ := debugLog()
	cases := []struct {
		edit func(*memoryv1.ImportedMemory)
		want string
	}{
		{func(m *memoryv1.ImportedMemory) { m.Queries = nil }, "memories[3] must contain at least one query"},
		{func(m *memoryv1.ImportedMemory) { m.Insights = nil }, "memories[3] must contain at least one insight"},
		{func(m *memoryv1.ImportedMemory) { m.Queries = []string{"ok", " "} }, "memories[3] queries[1] must not be empty"},
		{func(m *memoryv1.ImportedMemory) { m.Insights[0].Title = "" }, "memories[3] insights[0] title must not be empty"},
		{func(m *memoryv1.ImportedMemory) { m.Insights[0].Content = " " }, "memories[3] insights[0] content must not be empty"},
		{func(m *memoryv1.ImportedMemory) { m.Tags = tags("", "x") }, "memories[3] tag type must not be empty"},
		{func(m *memoryv1.ImportedMemory) { m.Queries[0] = invalidUTF8 }, "a field value cannot be sent: " + utf8Complaint(t)},
	}
	for _, c := range cases {
		batch := memories(4)
		c.edit(batch[3])
		var calls sent
		req := &memoryv1.ImportMemoriesRequest{Domain: "d", Memories: batch}
		rejected(t, Import(context.Background(), req, capped(&memoryv1.Limits{MaxImportMemories: 3}, 0), log, calls.send), c.want)
		if !reflect.DeepEqual(calls.offsets, []int{0}) {
			t.Fatalf("%s: offsets %v: the first group should have been sent first", c.want, calls.offsets)
		}
	}
}

func TestAnImportedMemorysCountsAreCapped(t *testing.T) {
	log, _ := debugLog()
	limits := &memoryv1.Limits{MaxImportQueriesPerMemory: 1, MaxImportInsightsPerMemory: 1, MaxImportTagsPerMemory: 1, MaxTextCharacters: 5}
	cases := []struct {
		edit func(*memoryv1.ImportedMemory)
		want string
	}{
		{func(m *memoryv1.ImportedMemory) { m.Queries = []string{"a", "b"} }, "memories[0] queries has 2 entries, which exceeds the limit of 1"},
		{func(m *memoryv1.ImportedMemory) { m.Insights = append(m.Insights, m.Insights[0]) }, "memories[0] insights has 2 entries, which exceeds the limit of 1"},
		{func(m *memoryv1.ImportedMemory) { m.Tags = tags("a", "1", "b", "2") }, "memories[0] tags has 2 entries, which exceeds the limit of 1"},
		{func(m *memoryv1.ImportedMemory) { m.Insights[0].Content = "longer" }, "memories[0] insights[0] title and content are 7 characters together, which exceeds the combined limit of 5"},
	}
	for _, c := range cases {
		batch := memories(1)
		c.edit(batch[0])
		var calls sent
		rejected(t, Import(context.Background(), &memoryv1.ImportMemoriesRequest{Domain: "d", Memories: batch}, capped(limits, 1), log, calls.send), c.want)
		if len(calls.parts) != 0 {
			t.Fatalf("%s: sent anyway", c.want)
		}
	}
}

func TestImportedTagsAreTrimmedAfterTheirCountIsChecked(t *testing.T) {
	log, _ := debugLog()
	batch := memories(1)
	batch[0].Tags = tags("a", "1", "b", "2", "c", "3")
	var calls sent
	err := Import(context.Background(), &memoryv1.ImportMemoriesRequest{Domain: "d", Memories: batch},
		capped(&memoryv1.Limits{MaxImportTagsPerMemory: 3}, 2), log, calls.send)
	if err != nil {
		t.Fatal(err)
	}
	if got := len(calls.parts[0].Memories[0].Tags); got != 2 {
		t.Fatalf("%d tags sent", got)
	}
}

func TestAnImportStopsWhenItsContextEnds(t *testing.T) {
	log, _ := debugLog()
	ctx, cancel := context.WithCancel(context.Background())
	calls := 0
	send := func(context.Context, int, *memoryv1.ImportMemoriesRequest) error {
		calls++
		cancel()
		return nil
	}
	err := Import(ctx, &memoryv1.ImportMemoriesRequest{Domain: "d", Memories: memories(4)},
		capped(&memoryv1.Limits{MaxImportMemories: 2}, 0), log, send)
	var f *fault.Error
	if !errors.As(err, &f) || f.Code != codes.Canceled || !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	if calls != 1 {
		t.Fatalf("%d calls after the context ended", calls)
	}
}

func TestAFailedGroupStopsTheImport(t *testing.T) {
	log, _ := debugLog()
	failure := fault.Internal("boom")
	calls := 0
	send := func(context.Context, int, *memoryv1.ImportMemoriesRequest) error {
		calls++
		return failure
	}
	err := Import(context.Background(), &memoryv1.ImportMemoriesRequest{Domain: "d", Memories: memories(4)},
		capped(&memoryv1.Limits{MaxImportMemories: 2}, 0), log, send)
	if !errors.Is(err, failure) || calls != 1 {
		t.Fatalf("got %v after %d calls", err, calls)
	}
}

func TestANegativeImportCapSendsOneGroup(t *testing.T) {
	log, _ := debugLog()
	var calls sent
	req := &memoryv1.ImportMemoriesRequest{Domain: "d", Memories: memories(3)}
	if err := Import(context.Background(), req, capped(&memoryv1.Limits{MaxImportMemories: -1}, 0), log, calls.send); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(calls.offsets, []int{0}) || len(calls.parts[0].Memories) != 3 {
		t.Fatalf("offsets %v", calls.offsets)
	}
}
