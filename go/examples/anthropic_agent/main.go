// Anthropic_agent wires a Claude agent to Memco shared memory, with web search
// to fall back on.
//
// The agent runs the same task twice. The first run has nothing in memory to
// go on, so it searches the web; if it saves what it finds, the second run can
// just search memory instead. Both runs report the tokens and time spent, so
// you can see the difference memory makes.
//
// Run it with:
//
//	export MEMCO_API_TOKEN=...
//	export ANTHROPIC_API_KEY=...
//	go run ./anthropic_agent
//
// Set MEMCO_EXAMPLE_MODEL to run another Claude model; it must support the
// web_search_20260209 tool and server-side fallbacks. Web search is
// Anthropic's server-side tool, so the Messages API runs it and this program
// only sees the results. A request the model declines is re-run server-side on
// the fallback model Anthropic recommends for that refusal; drop Fallbacks and
// its beta to turn that off.
//
// Running it for real writes a new memory to whatever domain and credential
// you point it at.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"os"
	"os/signal"
	"strings"
	"time"

	"github.com/anthropics/anthropic-sdk-go"

	"github.com/memcoai/memcoai/go/memcoai"
)

const (
	domain   = "coding"
	identity = "You are an engineering assistant for the team that builds the Memco SDKs."
	task     = "One of our services runs as a Cloud Run Function and calls several Google " +
		"Cloud APIs — Secrets Manager, Pub/Sub, BigQuery, and Workflows — over gRPC. " +
		"Since upgrading grpcio to 1.78.1, those calls started failing. Find out " +
		"what's going on and report it."
	// maxTurns bounds one run's requests, so a model that never stops costs a
	// known amount.
	maxTurns = 20
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	llm := anthropic.NewClient() // reads ANTHROPIC_API_KEY
	err := run(ctx, memcoai.Options{}, &llm, model(), 10*time.Second, log.New(os.Stdout, "", 0))
	stop()
	if err != nil {
		log.Fatal(err)
	}
}

func model() string {
	if name := os.Getenv("MEMCO_EXAMPLE_MODEL"); name != "" {
		return name
	}
	return "claude-opus-5"
}

// report is what one run spent: tokens on research, and time overall.
type report struct {
	tokens  int64
	elapsed time.Duration
}

// run runs the task twice, waiting settle between the runs for the first
// run's write to become searchable.
func run(ctx context.Context, opts memcoai.Options, llm *anthropic.Client, model string, settle time.Duration, out *log.Logger) (err error) {
	client, err := memcoai.NewClient(opts)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, client.Close(ctx)) }()
	if err := client.Connect(ctx); err != nil {
		return err
	}
	entry, found, err := findDomain(ctx, client, out)
	if !found {
		return err
	}

	out.Print("=== first run: nothing in memory yet ===\n")
	cold, err := runOnce(ctx, client, llm, model, entry, out)
	if err != nil {
		return err
	}

	out.Print("=== waiting for the write to become searchable ===\n")
	if err := wait(ctx, settle); err != nil {
		return err
	}

	out.Print("=== second run: the first run's finding is in memory now ===\n")
	warm, err := runOnce(ctx, client, llm, model, entry, out)
	if err != nil {
		return err
	}

	summarise(cold, warm, out)
	return nil
}

// findDomain returns the domain the task runs in. When the credential cannot
// reach it, it says which domains it can reach instead.
func findDomain(ctx context.Context, client *memcoai.Client, out *log.Logger) (memcoai.DomainEntry, bool, error) {
	listed, err := client.Memory.ListDomains(ctx)
	if err != nil {
		return memcoai.DomainEntry{}, false, err
	}
	var slugs []string
	for _, entry := range listed.Domains {
		if entry.Slug == domain {
			return entry, true, nil
		}
		slugs = append(slugs, entry.Slug)
	}
	out.Printf("no domain %q for this credential; available: %s", domain, strings.Join(slugs, ", "))
	return memcoai.DomainEntry{}, false, nil
}

// wait pauses for d, or until ctx ends.
func wait(ctx context.Context, d time.Duration) error {
	select {
	case <-time.After(d):
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// summarise compares what the two runs spent.
func summarise(cold, warm report, out *log.Logger) {
	out.Print("=== summary ===")
	out.Printf("cold run: %d tokens, %.1fs (nothing in memory yet)", cold.tokens, cold.elapsed.Seconds())
	out.Printf("warm run: %d tokens, %.1fs (memory answered it)", warm.tokens, warm.elapsed.Seconds())
	if cold.tokens > 0 {
		saved := cold.tokens - warm.tokens
		out.Printf("tokens saved: %d (%.0f%%)", saved, 100*float64(saved)/float64(cold.tokens))
	}
	if cold.elapsed > 0 {
		saved := cold.elapsed - warm.elapsed
		out.Printf("time saved: %.1fs (%.0f%%)", saved.Seconds(), 100*saved.Seconds()/cold.elapsed.Seconds())
	}
}

// runOnce runs the task to its answer in a new session, printing each tool
// call and the answer.
func runOnce(ctx context.Context, client *memcoai.Client, llm *anthropic.Client, model string, entry memcoai.DomainEntry, out *log.Logger) (report, error) {
	started := time.Now()
	session, err := client.Memory.StartSession(ctx, domain)
	if err != nil {
		return report{}, err
	}
	out.Printf("session %s in %s\n", session.ID, entry.Slug)

	toolset := session.Tools()
	params := anthropic.BetaMessageNewParams{
		Model:     model,
		MaxTokens: 16000,
		System:    []anthropic.BetaTextBlockParam{{Text: identity + "\n\n" + memcoai.Briefing(entry, session.Instructions)}},
		Messages:  []anthropic.BetaMessageParam{anthropic.NewBetaUserMessage(anthropic.NewBetaTextBlock(task))},
		Tools:     tools(toolset),
		Fallbacks: anthropic.BetaFallbacksParamOfDefault(),
		Betas:     []anthropic.AnthropicBeta{anthropic.AnthropicBetaServerSideFallback2026_07_01},
	}
	tokens, err := converse(ctx, llm, params, toolset, out)
	return report{tokens, time.Since(started)}, err
}

// tools offers the model the session's Memco tools and, to fall back on,
// Anthropic's web search.
func tools(toolset *memcoai.Toolset) []anthropic.BetaToolUnionParam {
	var offered []anthropic.BetaToolUnionParam
	for _, tool := range toolset.ToAnthropic() {
		offered = append(offered, anthropic.BetaToolUnionParam{OfTool: &anthropic.BetaToolParam{
			Name:        tool.Name,
			Description: anthropic.String(tool.Description),
			InputSchema: anthropic.BetaToolInputSchemaParam{
				Properties: tool.InputSchema.Properties,
				Required:   tool.InputSchema.Required,
			},
		}})
	}
	return append(offered, anthropic.BetaToolUnionParam{OfWebSearchTool20260209: &anthropic.BetaWebSearchTool20260209Param{
		MaxUses: anthropic.Int(5),
	}})
}

// converse sends the conversation until the model answers, declines or runs
// out of turns, and returns the tokens spent on research. The answer's own
// tokens are left out: it is the same work in both runs.
func converse(ctx context.Context, llm *anthropic.Client, params anthropic.BetaMessageNewParams, toolset *memcoai.Toolset, out *log.Logger) (int64, error) {
	var tokens int64
	for range maxTurns {
		message, err := llm.Beta.Messages.New(ctx, params)
		if err != nil {
			return tokens, err
		}
		params.Messages = append(params.Messages, message.ToParam())
		trace(message, out)

		switch message.StopReason {
		case anthropic.BetaStopReasonPauseTurn:
			// A long server-side turn paused; sending it back resumes it.
			tokens += spent(message)
			continue
		case anthropic.BetaStopReasonRefusal:
			// Declined by the model and by the fallback.
			out.Printf("the model declined (%s)\n", message.StopDetails.Category)
			return tokens, nil
		case anthropic.BetaStopReasonToolUse:
			results, err := call(ctx, toolset, message)
			if err != nil {
				return tokens, err
			}
			// A reply that asked only for Anthropic's own tools has no results
			// to send back, and is the answer.
			if len(results) > 0 {
				tokens += spent(message)
				params.Messages = append(params.Messages, anthropic.NewBetaUserMessage(results...))
				continue
			}
		}
		// Anything else is the answer. Tools run only when the model stopped to
		// ask for them: a reply cut short by max_tokens can end in a tool call
		// that was never finished.
		out.Printf("%s\n\n", answer(message))
		return tokens, nil
	}
	out.Printf("gave up after %d turns\n", maxTurns)
	return tokens, nil
}

// call runs the Memco tools the model asked for. A mistake the model can fix
// comes back as text for it to read; any other failure is returned, and ends
// the run.
func call(ctx context.Context, toolset *memcoai.Toolset, message *anthropic.BetaMessage) ([]anthropic.BetaContentBlockParamUnion, error) {
	var results []anthropic.BetaContentBlockParamUnion
	for _, block := range message.Content {
		use, ok := block.AsAny().(anthropic.BetaToolUseBlock)
		if !ok {
			continue
		}
		text, err := toolset.Call(ctx, use.Name, json.RawMessage(use.JSON.Input.Raw()))
		if err != nil {
			return nil, err
		}
		results = append(results, anthropic.NewBetaToolResultBlock(use.ID, text, false))
	}
	return results, nil
}

// trace prints every tool the model used, Memco's and Anthropic's.
func trace(message *anthropic.BetaMessage, out *log.Logger) {
	for _, block := range message.Content {
		switch use := block.AsAny().(type) {
		case anthropic.BetaToolUseBlock:
			out.Printf("  -> %s(%s)", use.Name, use.JSON.Input.Raw())
		case anthropic.BetaServerToolUseBlock:
			out.Printf("  -> %s(%s)", use.Name, use.JSON.Input.Raw())
		}
	}
}

// spent is what one reply cost, in tokens.
func spent(message *anthropic.BetaMessage) int64 {
	return message.Usage.InputTokens + message.Usage.OutputTokens
}

// answer is a reply's text. With web search it arrives as several cited
// blocks.
func answer(message *anthropic.BetaMessage) string {
	var text strings.Builder
	for _, block := range message.Content {
		if part, ok := block.AsAny().(anthropic.BetaTextBlock); ok {
			text.WriteString(part.Text)
		}
	}
	return text.String()
}
