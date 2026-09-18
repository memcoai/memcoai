package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
	"google.golang.org/grpc/codes"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
	"github.com/memcoai/memcoai/go/memcoai"
)

// messages is a fake Messages API that answers each request with the next
// scripted reply and records what it was sent.
type messages struct {
	mu       sync.Mutex
	replies  []string
	requests []map[string]any
	betas    []string
}

func (m *messages) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	var request map[string]any
	if err == nil {
		err = json.Unmarshal(body, &request)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if err != nil || r.URL.Path != "/v1/messages" || len(m.replies) == 0 {
		http.Error(w, `{"type":"error","error":{"type":"api_error","message":"unscripted"}}`, http.StatusInternalServerError)
		return
	}
	m.requests = append(m.requests, request)
	m.betas = append(m.betas, r.Header.Get("anthropic-beta"))
	reply := m.replies[0]
	m.replies = m.replies[1:]
	w.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(w, reply)
}

func (m *messages) sent(t *testing.T) []map[string]any {
	t.Helper()
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.requests
}

func encoded(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func reply(t *testing.T, stop string, content ...map[string]any) string {
	t.Helper()
	if content == nil {
		content = []map[string]any{}
	}
	message := map[string]any{
		"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
		"content": content, "stop_reason": stop,
		"usage": map[string]any{"input_tokens": 10, "output_tokens": 5},
	}
	if stop == "refusal" {
		message["stop_details"] = map[string]any{"type": "refusal", "category": "cyber", "explanation": "declined"}
	}
	return encoded(t, message)
}

func text(s string) map[string]any { return map[string]any{"type": "text", "text": s} }

func toolUse(id, name, input string) map[string]any {
	return map[string]any{"type": "tool_use", "id": id, "name": name, "input": json.RawMessage(input)}
}

type agent struct {
	*exampletest.Run
	messages *messages
	llm      anthropic.Client
}

func started(t *testing.T, replies ...string) *agent {
	t.Helper()
	r := exampletest.Start(t)
	r.Server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Domains: []*memoryv1.DomainEntry{{Slug: "coding", Title: "Coding", Summary: "How we build things."}}})
	fake := &messages{replies: replies}
	server := httptest.NewServer(fake)
	t.Cleanup(server.Close)
	llm := anthropic.NewClient(option.WithBaseURL(server.URL), option.WithAPIKey("test"), option.WithMaxRetries(0))
	return &agent{Run: r, messages: fake, llm: llm}
}

func (a *agent) run(ctx context.Context) error {
	return run(ctx, a.Options, &a.llm, "claude-test", 0, a.Out)
}

// toolResults returns the tool_result blocks of a request's last message.
func toolResults(t *testing.T, request map[string]any) []map[string]any {
	t.Helper()
	history := request["messages"].([]any)
	last := history[len(history)-1].(map[string]any)
	var results []map[string]any
	for _, block := range last["content"].([]any) {
		if block := block.(map[string]any); block["type"] == "tool_result" {
			results = append(results, block)
		}
	}
	return results
}

func resultText(t *testing.T, result map[string]any) string {
	t.Helper()
	var parts []string
	for _, block := range result["content"].([]any) {
		parts = append(parts, block.(map[string]any)["text"].(string))
	}
	return strings.Join(parts, "")
}

func TestTheTaskRunsTwiceAndTheToolsAnswerFromMemory(t *testing.T) {
	a := started(t,
		reply(t, "tool_use", text("checking memory"), toolUse("toolu_1", "memco_search", `{"query": "grpcio 1.78.1"}`)),
		reply(t, "end_turn", text("cold answer")),
		reply(t, "pause_turn", map[string]any{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": map[string]any{"query": "grpcio"}}),
		reply(t, "end_turn", text("warm answer")),
	)
	a.Server.Memory.Respond("Search", &memoryv1.SearchResponse{SessionId: "session-a", Memories: []*memoryv1.MemoryResult{{Idx: "memory-a-1", TimesServed: 2}}})
	if err := a.run(context.Background()); err != nil {
		t.Fatalf("%v\n%s", err, a.Printed())
	}
	a.Expect(t,
		"=== first run: nothing in memory yet ===",
		"session session-a in coding",
		`  -> memco_search({"query":"grpcio 1.78.1"})`,
		"cold answer",
		"=== second run: the first run's finding is in memory now ===",
		`  -> web_search({"query":"grpcio"})`,
		"warm answer",
		"=== summary ===",
		"cold run: 15 tokens",
		"warm run: 15 tokens",
		"tokens saved: 0 (0%)",
	)

	sent := a.messages.sent(t)
	if len(sent) != 4 {
		t.Fatalf("%d requests", len(sent))
	}
	results := toolResults(t, sent[1])
	want := memcoai.Render(memcoai.SearchResult{SessionID: "session-a", Memories: []memcoai.Memory{{Idx: "memory-a-1", TimesServed: 2}}})
	if len(results) != 1 || results[0]["tool_use_id"] != "toolu_1" || resultText(t, results[0]) != want {
		t.Fatalf("tool results %v, want %q", results, want)
	}
	// A paused turn is sent back as it stands, for the service to continue.
	history := sent[3]["messages"].([]any)
	if len(history) != 2 || history[1].(map[string]any)["role"] != "assistant" {
		t.Fatalf("resumed with %v", history)
	}
	if web := encoded(t, sent[3]["tools"]); !strings.Contains(web, `"max_uses":5`) {
		t.Fatalf("web search is not capped: %s", web)
	}
}

func TestTheAnswerIsPrintedWhole(t *testing.T) {
	a := started(t,
		reply(t, "end_turn", text("The cause is "), text("the new resolver.")),
		reply(t, "end_turn", text("again")),
	)
	if err := a.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	a.Expect(t, "The cause is the new resolver.\n")
}

// A reply cut short can end in a tool call that was never finished, and a
// tool_use reply may hold only the service's own tools; neither runs a Memco tool.
func TestOnlyACompleteRequestForAMemcoToolRunsOne(t *testing.T) {
	serverOnly := map[string]any{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": map[string]any{"query": "q"}}
	a := started(t,
		reply(t, "max_tokens", text("partial"), toolUse("toolu_1", "memco_search", `{"query": "q"}`)),
		reply(t, "tool_use", serverOnly, text("done")),
	)
	if err := a.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	a.Expect(t, "partial", "done")
	for _, call := range a.Server.Memory.Calls() {
		if call == "Search" {
			t.Fatal("a tool call was run from a reply that did not ask for it")
		}
	}
	if n := len(a.messages.sent(t)); n != 2 {
		t.Fatalf("%d requests", n)
	}
}

func TestARunThatNeverEndsIsCutOff(t *testing.T) {
	var replies []string
	for range 2 * maxTurns {
		replies = append(replies, reply(t, "pause_turn"))
	}
	a := started(t, replies...)
	if err := a.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	a.Expect(t, "gave up after 20 turns", "gave up after 20 turns")
	if n := len(a.messages.sent(t)); n != 2*maxTurns {
		t.Fatalf("%d requests", n)
	}
}

func TestTheRequestOffersTheSessionsToolsWebSearchAndFallbacks(t *testing.T) {
	a := started(t, reply(t, "end_turn", text("a")), reply(t, "end_turn", text("b")))
	if err := a.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	sent := a.messages.sent(t)
	if len(sent) != 2 {
		t.Fatalf("%d requests", len(sent))
	}
	request := sent[0]
	if request["model"] != "claude-test" || request["fallbacks"] != "default" {
		t.Fatalf("model %v, fallbacks %v", request["model"], request["fallbacks"])
	}
	if a.messages.betas[0] != string(anthropic.AnthropicBetaServerSideFallback2026_07_01) {
		t.Fatalf("betas %q", a.messages.betas[0])
	}
	var names []string
	for _, tool := range request["tools"].([]any) {
		tool := tool.(map[string]any)
		names = append(names, tool["name"].(string))
		if tool["name"] == "memco_search" {
			schema := tool["input_schema"].(map[string]any)
			if schema["type"] != "object" || schema["required"].([]any)[0] != "query" || tool["description"] == "" {
				t.Fatalf("memco_search %v", tool)
			}
		}
		if tool["name"] == "web_search" && tool["type"] != "web_search_20260209" {
			t.Fatalf("web search %v", tool)
		}
	}
	if want := "memco_search memco_get_memory memco_create_memory memco_enrich_memory memco_share_feedback memco_revert_memory web_search"; strings.Join(names, " ") != want {
		t.Fatalf("tools %v", names)
	}
	system := encoded(t, request["system"])
	if !strings.Contains(system, "You are an engineering assistant") || !strings.Contains(system, "How we build things.") {
		t.Fatalf("system %s", system)
	}
	if prompt := encoded(t, request["messages"]); !strings.Contains(prompt, "grpcio to 1.78.1") {
		t.Fatalf("messages %s", prompt)
	}
}

func TestAMistakeTheModelCanFixGoesBackAsText(t *testing.T) {
	a := started(t,
		reply(t, "tool_use", toolUse("toolu_1", "memco_search", `{"query": 7}`), toolUse("toolu_2", "memco_get_memory", `{"idx": "memory-gone"}`)),
		reply(t, "end_turn", text("done")),
		reply(t, "end_turn", text("done")),
	)
	a.Server.Memory.FailNext("GetMemory", testserver.Failure{Code: codes.NotFound, Details: "no such memory"})
	if err := a.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	sent := a.messages.sent(t)
	if len(sent) != 3 {
		t.Fatalf("%d requests", len(sent))
	}
	results := toolResults(t, sent[1])
	if len(results) != 2 ||
		resultText(t, results[0]) != "invalid request: query must be a string, not number" ||
		resultText(t, results[1]) != "nothing found: no such memory" {
		t.Fatalf("tool results %v", results)
	}
}

func TestAFailureTheModelCannotFixEndsTheRun(t *testing.T) {
	a := started(t, reply(t, "tool_use", toolUse("toolu_1", "memco_search", `{"query": "q"}`)))
	a.Server.Memory.FailNext("Search", testserver.Failure{Code: codes.Unauthenticated, Details: "expired"})
	var rejected *memcoai.AuthenticationError
	if err := a.run(context.Background()); !errors.As(err, &rejected) {
		t.Fatalf("got %v", err)
	}
	if n := len(a.messages.sent(t)); n != 1 {
		t.Fatalf("%d requests", n)
	}
}

func TestARefusalEndsThatRun(t *testing.T) {
	a := started(t, reply(t, "refusal"), reply(t, "refusal"))
	if err := a.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	a.Expect(t, "the model declined (cyber)", "the model declined (cyber)", "=== summary ===")
}

func TestAMissingDomainRunsNothing(t *testing.T) {
	a := started(t)
	a.Server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Domains: []*memoryv1.DomainEntry{{Slug: "support"}, {Slug: "sales"}}})
	if err := a.run(context.Background()); err != nil {
		t.Fatal(err)
	}
	a.Expect(t, `no domain "coding" for this credential; available: support, sales`)
}

func TestAModelFailureIsReturned(t *testing.T) {
	a := started(t)
	var failed *anthropic.Error
	if err := a.run(context.Background()); !errors.As(err, &failed) {
		t.Fatalf("got %v", err)
	}
}

func TestAnEndedContextStopsIt(t *testing.T) {
	a := started(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := a.run(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}

func TestTheModelCanBeChosenFromTheEnvironment(t *testing.T) {
	t.Setenv("MEMCO_EXAMPLE_MODEL", "")
	if got := model(); got != "claude-opus-5" {
		t.Fatalf("default %q", got)
	}
	t.Setenv("MEMCO_EXAMPLE_MODEL", "claude-sonnet-5")
	if got := model(); got != "claude-sonnet-5" {
		t.Fatalf("override %q", got)
	}
}
