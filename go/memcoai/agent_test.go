package memcoai

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"slices"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/proto"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/memory"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

func toolset(t *testing.T, f *fixture) *Toolset {
	t.Helper()
	return opened(t, f).Tools()
}

func toolNames(tools *Toolset) []string {
	var names []string
	for _, tool := range tools.Tools {
		names = append(names, tool.Name)
	}
	return names
}

func called(t *testing.T, tools *Toolset, name, args string) string {
	t.Helper()
	text, err := tools.Call(ctx, name, json.RawMessage(args))
	if err != nil {
		t.Fatalf("%s(%s): %v", name, args, err)
	}
	return text
}

func TestEveryOfferedOperationIsOneToolInOrder(t *testing.T) {
	f := connected(t)
	var want []string
	for _, name := range memory.OfferedTools {
		want = append(want, "memco_"+name)
	}
	tools := toolset(t, f)
	if got := toolNames(tools); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
	for i, tool := range tools.Tools {
		if tool.Description != memory.Description(memory.OfferedTools[i]) || tool.Description == "" {
			t.Errorf("%s: description %q", tool.Name, tool.Description)
		}
	}
}

func TestToolsAreFilteredByWhatTheCredentialMayDo(t *testing.T) {
	scenarios := map[string][]string{
		"creator": memory.OfferedTools,
		"reader":  {"search", "share_feedback"},
		"admin":   {},
	}
	for role, available := range scenarios {
		t.Run(role, func(t *testing.T) {
			f := connected(t)
			catalog := &memoryv1.ListToolsResponse{}
			for _, name := range memory.OfferedTools {
				catalog.Tools = append(catalog.Tools, &memoryv1.ToolDescriptor{Name: name, Available: slices.Contains(available, name)})
			}
			f.server.Memory.Respond("ListTools", catalog)
			var want []string
			for _, name := range available {
				want = append(want, "memco_"+name)
			}
			if got := toolNames(toolset(t, f)); !reflect.DeepEqual(got, want) {
				t.Fatalf("got %v, want %v", got, want)
			}
		})
	}
}

func TestAnOperationMissingFromTheCatalogIsNotOffered(t *testing.T) {
	f := connected(t)
	f.server.Memory.Respond("ListTools", &memoryv1.ListToolsResponse{Tools: []*memoryv1.ToolDescriptor{{Name: "search", Available: true}}})
	if got := toolNames(toolset(t, f)); !reflect.DeepEqual(got, []string{"memco_search"}) {
		t.Fatalf("got %v", got)
	}
	f.server.Memory.Respond("ListTools", &memoryv1.ListToolsResponse{})
	if got := toolNames(toolset(t, f)); len(got) != 0 {
		t.Fatalf("an empty catalog offered %v", got)
	}
}

func TestTheCatalogIsFetchedOncePerSession(t *testing.T) {
	f := connected(t)
	session := opened(t, f)
	session.Tools()
	session.Tools()
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

func TestAFailedCatalogOffersEveryTool(t *testing.T) {
	f := connected(t)
	for range 3 {
		f.server.Memory.FailNext("ListTools", testserver.Failure{Code: codes.Unavailable, Details: "down"})
	}
	if got := toolNames(toolset(t, f)); len(got) != len(memory.OfferedTools) {
		t.Fatalf("got %v", got)
	}
}

func TestAToolRendersTheResult(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	f.server.Memory.Respond("Search", &memoryv1.SearchResponse{Memories: []*memoryv1.MemoryResult{{Idx: "memory-a-1", TimesServed: 1}}})
	got := called(t, tools, "memco_search", `{"query": "how", "tags": [{"type": "language", "value": "go"}]}`)
	if !strings.HasPrefix(got, "1 memory\n\nmemory-a-1  served 1x") {
		t.Fatalf("got %q", got)
	}
	want := &memoryv1.SearchRequest{Query: "how", SessionId: "session-a", Tags: []*memoryv1.Tag{{Type: "language", Value: "go"}}}
	if sent := sentAs[*memoryv1.SearchRequest](t, f, "Search"); !proto.Equal(sent, want) {
		t.Fatalf("sent %v", sent)
	}
}

func TestARatingArrivesAsAnObjectAndReachesTheWire(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	f.server.Memory.Respond("ShareFeedback", &memoryv1.ShareFeedbackResponse{Entries: []*memoryv1.FeedbackEntry{
		{Idx: "memory-a-1", Relevant: true, Correct: false, Advice: "say why"},
	}})
	got := called(t, tools, "memco_share_feedback", `{"feedback": [{"idx": "memory-a-1", "relevant": true, "correct": false, "comment": "stale"}]}`)
	if got != "memory-a-1  relevant=true correct=false  say why" {
		t.Fatalf("got %q", got)
	}
	comment := "stale"
	want := &memoryv1.ShareFeedbackRequest{SessionId: "session-a", Feedback: []*memoryv1.FeedbackRating{{Idx: "memory-a-1", Relevant: true, Comment: &comment}}}
	if sent := sentAs[*memoryv1.ShareFeedbackRequest](t, f, "ShareFeedback"); !proto.Equal(sent, want) {
		t.Fatalf("sent %v", sent)
	}
}

func TestAWriteIsTheAgentsAndReportsItsOperation(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	if got := called(t, tools, "memco_create_memory", `{"query": "q", "title": "t", "content": "c"}`); got != "accepted as operation create-a" {
		t.Fatalf("got %q", got)
	}
	if sent := sentAs[*memoryv1.CreateMemoryRequest](t, f, "CreateMemory"); sent.GetSource() != memoryv1.DataSource_DATA_SOURCE_AGENT {
		t.Fatalf("source %v", sent.GetSource())
	}
	got := called(t, tools, "memco_create_memory", `{"query": "q", "title": "t", "content": "c", "source": "user"}`)
	if got != `invalid request: unknown argument(s): "source"` {
		t.Fatalf("got %q", got)
	}
	f.server.Memory.Respond("CreateMemory", &memoryv1.CreateMemoryResponse{})
	if got := called(t, tools, "memco_create_memory", `{"query": "q", "title": "t", "content": "c"}`); got != "accepted; this write cannot be undone" {
		t.Fatalf("got %q", got)
	}
}

func TestEnrichFetchAndRevertReachTheWire(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	if got := called(t, tools, "memco_enrich_memory", `{"memory_idx": "new", "title": "t", "content": "c", "sources": ["memory-b-1"]}`); got != "accepted as operation enrich-a" {
		t.Fatalf("enrich %q", got)
	}
	if sent := sentAs[*memoryv1.EnrichMemoryRequest](t, f, "EnrichMemory"); sent.GetMemoryIdx() != "new" || !reflect.DeepEqual(sent.GetSources(), []string{"memory-b-1"}) {
		t.Fatalf("sent %v", sent)
	}
	if got := called(t, tools, "memco_get_memory", `{"idx": "memory-a-1"}`); got != "memory-a-1  served 0x" {
		t.Fatalf("get %q", got)
	}
	if got := called(t, tools, "memco_revert_memory", `{"operation_id": "create-a"}`); got != "create-a: merged" {
		t.Fatalf("revert %q", got)
	}
}

func TestWhatAModelGotWrongComesBackAsText(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	cases := map[string][2]string{
		"missing":       {`{}`, "invalid request: missing required argument(s): query"},
		"wrong type":    {`{"query": 3}`, "invalid request: query must be a string, not number"},
		"bound session": {`{"query": "q", "session_id": "other"}`, `invalid request: unknown argument(s): "session_id"`},
		"blank":         {`{"query": " "}`, "invalid request: query must not be empty"},
		"not json":      {`not json at all`, "invalid request: arguments are not valid JSON: "},
		"a string":      {`"just a string"`, "invalid request: arguments must be an object, not string"},
		"a list":        {`[1, 2]`, "invalid request: arguments must be an object, not array"},
		"null":          {`null`, "invalid request: arguments must be an object, not null"},
		"a number":      {`42`, "invalid request: arguments must be an object, not number"},
	}
	for name, c := range cases {
		if got := called(t, tools, "memco_search", c[0]); !strings.HasPrefix(got, c[1]) {
			t.Errorf("%s: got %q", name, got)
		}
	}
	if got := called(t, tools, "memco_search", `{"query": "q", "tags": null}`); got != "0 memories" {
		t.Errorf("a null optional argument: %q", got)
	}
	f.server.Memory.Respond("GetMemory", &memoryv1.GetMemoryResponse{})
	if got := called(t, tools, "memco_get_memory", `{"idx": "memory-z-1"}`); got != `nothing found: no memory was returned for "memory-z-1"` {
		t.Errorf("not found: %q", got)
	}
	if calls := f.server.Memory.Calls(); !reflect.DeepEqual(calls, []string{"Search", "GetMemory"}) {
		t.Errorf("calls %v", calls)
	}
}

func TestAnInventedToolNameIsReportedWithTheRealOnes(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	got := called(t, tools, "memco_delete\"everything", `{}`)
	want := `invalid request: no tool named "memco_delete\"everything"; there is ` +
		"memco_create_memory, memco_enrich_memory, memco_get_memory, memco_revert_memory, memco_search, memco_share_feedback"
	if got != want {
		t.Fatalf("got %q", got)
	}
}

func TestFailuresAModelCannotFixAreReturned(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	f.server.Memory.Fail(&testserver.Failure{Code: codes.Unauthenticated, Details: "revoked"})
	text, err := tools.Call(ctx, "memco_search", json.RawMessage(`{"query": "q"}`))
	if text != "" {
		t.Fatalf("text %q", text)
	}
	expect[*AuthenticationError](t, err)
	text, err = tools.Tools[0].Call(ctx, json.RawMessage(`{"query": "q"}`))
	if text != "" {
		t.Fatalf("text %q", text)
	}
	expect[*AuthenticationError](t, err)

	f.server.Memory.Fail(nil)
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	text, err = tools.Call(cancelled, "memco_search", json.RawMessage(`{"query": "q"}`))
	if text != "" || !errors.Is(err, context.Canceled) {
		t.Fatalf("a cancelled call: %q, %v", text, err)
	}
}

func TestOnlyMalformedRequestsAndMissingHandlesAreRecoverable(t *testing.T) {
	recoverable := []error{
		&InvalidRequestError{APIError{Code: codes.InvalidArgument}},
		&NotFoundError{APIError{Code: codes.NotFound}},
	}
	for _, err := range recoverable {
		if !IsAgentRecoverable(err) {
			t.Errorf("%T is not recoverable", err)
		}
	}
	unrecoverable := []error{
		nil,
		errors.New("x"),
		&ConfigError{},
		&AuthenticationError{},
		&PermissionError{},
		&PreconditionFailedError{},
		&SunsetError{},
		&ResourceExhaustedError{},
		&UnavailableError{},
		&UnhealthyError{},
		&TimeoutError{},
		&InternalError{},
	}
	for _, err := range unrecoverable {
		if IsAgentRecoverable(err) {
			t.Errorf("%T is recoverable", err)
		}
	}
}

func TestTheProviderShapesCarryTheSchemaUnchanged(t *testing.T) {
	f := connected(t)
	tools := toolset(t, f)
	anthropic := tools.ToAnthropic()
	openai := tools.ToOpenAI()
	if len(anthropic) != len(tools.Tools) || len(openai) != len(tools.Tools) {
		t.Fatalf("%d, %d for %d tools", len(anthropic), len(openai), len(tools.Tools))
	}
	for i, tool := range tools.Tools {
		if anthropic[i].Name != tool.Name || anthropic[i].Description != tool.Description ||
			!reflect.DeepEqual(anthropic[i].InputSchema, tool.Parameters) {
			t.Errorf("anthropic %d: %+v", i, anthropic[i])
		}
		if openai[i].Type != "function" || openai[i].Function.Name != tool.Name ||
			!reflect.DeepEqual(openai[i].Function.Parameters, tool.Parameters) {
			t.Errorf("openai %d: %+v", i, openai[i])
		}
	}
	encoded, err := json.Marshal(anthropic[0])
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	schema := decoded["input_schema"].(map[string]any)
	if schema["type"] != "object" || schema["required"] == nil || decoded["name"] != "memco_search" {
		t.Fatalf("encoded %s", encoded)
	}
	encoded, _ = json.Marshal(openai[1])
	if !strings.Contains(string(encoded), `"type":"function","function":{"name":"memco_get_memory"`) {
		t.Fatalf("encoded %s", encoded)
	}
}

func TestParametersAreFreshAndNeverNull(t *testing.T) {
	f := connected(t)
	session := opened(t, f)
	first := session.Tools()
	first.Tools[0].Parameters.Properties["query"]["description"] = "changed"
	second := session.Tools()
	if second.Tools[0].Parameters.Properties["query"]["description"] == "changed" {
		t.Fatal("a schema is shared between toolsets")
	}
	for _, tool := range second.Tools {
		if tool.Parameters.Type != "object" || tool.Parameters.Required == nil {
			t.Errorf("%s: %+v", tool.Name, tool.Parameters)
		}
	}
}

func instructions(content string) Instructions { return Instructions{Content: content} }

func TestAReferenceSaysHowToFetchWhatItStandsFor(t *testing.T) {
	got := Render(Memory{Idx: "memory-a-2", Reference: "memory-a-1"})
	want := "memory-a-2  already returned in this session as memory-a-1; fetch it again with memco_get_memory"
	if got != want {
		t.Fatalf("got %q", got)
	}
}

func TestAFullyPopulatedMemoryRendersExactlyThisText(t *testing.T) {
	got := Render(Memory{
		Idx: "memory-a-1", Kind: "policy", TimesServed: 9,
		Intents: []string{"how do I authenticate", "what is the prefix"},
		Insights: []Insight{
			{Idx: "memory-a-1-insight-1", Title: "T1", Content: "C1", Updated: "2026-08-01", TimesServed: 4, Endorsed: 7, Disputed: 3},
			{Idx: "memory-a-1-insight-2", Title: "T2", Content: "C2", TimesServed: 1},
		},
	})
	want := "memory-a-1  served 9x  (policy)\n" +
		"  retrieved before by: how do I authenticate; what is the prefix\n" +
		"  memory-a-1-insight-1  T1\n" +
		"    [updated 2026-08-01, served 4x, endorsed 7x, disputed 3x]\n" +
		"    C1\n" +
		"  memory-a-1-insight-2  T2\n" +
		"    [never updated, served 1x]\n" +
		"    C2"
	if got != want {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}

func TestASearchResultNamesWhatItLeftOut(t *testing.T) {
	got := Render(SearchResult{
		SessionID:    "session-a",
		Memories:     []Memory{{Idx: "memory-a-1", TimesServed: 1}},
		Notice:       "three more matched than fit",
		Instructions: instructions("search again as questions arise"),
	})
	want := "1 memory\n\nthree more matched than fit\n\nmemory-a-1  served 1x\n\nsearch again as questions arise"
	if got != want {
		t.Fatalf("got %q", got)
	}
	if got := Render(SearchResult{Memories: []Memory{{Idx: "a"}, {Idx: "b"}}}); got != "2 memories\n\na  served 0x\n\nb  served 0x" {
		t.Fatalf("got %q", got)
	}
}

func TestEveryPartOfTheGuidanceReachesTheModel(t *testing.T) {
	got := Render(WriteResult{OperationID: "create-a", Instructions: Instructions{
		Content: "the content", Policy: "the policy", Adding: "the adding", Rating: "the rating", Next: "the next",
	}})
	want := strings.Join([]string{"accepted as operation create-a", "the content", "the policy", "the adding", "the rating", "the next"}, "\n\n")
	if got != want {
		t.Fatalf("got %q", got)
	}
}

func TestRatingsAndRevertsRenderInWords(t *testing.T) {
	got := Render(FeedbackResult{Entries: []FeedbackEntry{
		{Idx: "memory-a-1", Relevant: true, Correct: false, Advice: "say why"},
		{Idx: "memory-a-2", Relevant: false, Correct: true},
	}})
	if got != "memory-a-1  relevant=true correct=false  say why\nmemory-a-2  relevant=false correct=true" {
		t.Fatalf("feedback %q", got)
	}
	if got := Render(RevertResult{OperationID: "create-a", Outcome: RevertOutcomeMemoryRemoved}); got != "create-a: memory removed" {
		t.Fatalf("revert %q", got)
	}
	if got := Render(RevertResult{Outcome: RevertOutcomeNotFound}); got != "null: not found" {
		t.Fatalf("revert without an id %q", got)
	}
	if got := Render(&WriteResult{OperationID: "x"}); got != "accepted as operation x" {
		t.Fatalf("a pointer result %q", got)
	}
}

func TestTheBriefingSaysWhatTheServiceSaid(t *testing.T) {
	got := Briefing(DomainEntry{
		Slug: "coding", Title: "Software Development", Summary: "What engineers learned.",
		WhenToSearch: "before work", WhenToSave: "after learning", WhatNotToSave: "secrets",
		TagsDescription: "Tags describe the stack.", FilterTagTypes: []string{"language"},
		VersionTagTypes: []string{"language", "library"}, MaxTagsPerQuery: 5,
	}, instructions("Session started."))
	want := strings.Join([]string{
		"You share a persistent memory with your team, in the 'coding' domain (Software Development). " +
			"Search it before working anything out from scratch, and save what you learn so nobody has to learn it twice.",
		"What engineers learned.",
		"When to search: before work",
		"When to save: after learning",
		"What not to save: secrets",
		"Tags describe the stack.",
		"These tag types narrow the results rather than boosting them, so a wrong one returns nothing: language.",
		"These carry a version, and one on any other type is dropped: language, library.",
		"At most 5 tags per call.",
		"The domain and the session are already chosen, so list_domains and start_session are not among your tools: " +
			"where a tool's description names one, what it would have told you is above.",
		"Pass 'new' as memory_idx to memco_enrich_memory to open a new memory.",
		"Session started.",
	}, "\n\n")
	if got != want {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}

func TestTheBriefingDropsWhatTheDomainLeftEmpty(t *testing.T) {
	if got := Briefing(DomainEntry{Slug: "open", Title: "Open", MaxTagsPerQuery: -1}, Instructions{}); strings.Contains(got, "At most") {
		t.Fatalf("a negative cap was briefed: %q", got)
	}
	got := Briefing(DomainEntry{Slug: "open", Title: "Open"}, Instructions{})
	if strings.Contains(got, "\n\n\n") || strings.Contains(got, "When to") || strings.Contains(got, "At most") ||
		strings.Contains(got, "narrow the results") || strings.Contains(got, "carry a version") {
		t.Fatalf("got %q", got)
	}
	if !strings.HasSuffix(got, "Pass 'new' as memory_idx to memco_enrich_memory to open a new memory.") {
		t.Fatalf("got %q", got)
	}
}
