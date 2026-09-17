package memcoai_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

// These examples are compiled, not run: each needs a credential and the
// service. They share the placeholders below, which a real program gets from
// NewClient and StartSession, as ExampleNewClient shows.
var (
	ctx     = context.Background()
	client  *memcoai.Client
	session *memcoai.Session
)

func ExampleNewClient() {
	client, err := memcoai.NewClient(memcoai.Options{}) // reads MEMCO_API_TOKEN
	if err != nil {
		log.Fatal(err)
	}
	defer func() {
		if err := client.Close(ctx); err != nil {
			log.Print(err)
		}
	}()
	if err := client.Connect(ctx); err != nil {
		log.Fatal(err)
	}
	session, err := client.Memory.StartSession(ctx, "coding")
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(session.ID)
}

func ExampleClient_Connect() {
	err := client.Connect(ctx)
	var unhealthy *memcoai.UnhealthyError
	var unavailable *memcoai.UnavailableError
	switch {
	case errors.As(err, &unhealthy):
		log.Fatal("the service is up but not serving yet: ", err)
	case errors.As(err, &unavailable):
		log.Fatal("the service cannot be reached: ", err)
	case err != nil:
		log.Fatal(err)
	}
}

func ExampleClient_Close() {
	// Give calls in flight ten seconds to finish.
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Close(ctx); err != nil {
		log.Print("closed with calls still in flight: ", err)
	}
}

func ExampleClient_Provenance() {
	provenance, err := client.Provenance()
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("built from", provenance.ServerCommit)
}

func ExampleReadProvenance() {
	provenance, err := memcoai.ReadProvenance()
	if err != nil {
		log.Fatal(err)
	}
	for _, proto := range provenance.Protos {
		fmt.Println(proto.Path, proto.SHA256)
	}
}

func ExampleSetLevel() {
	if err := memcoai.SetLevel("debug"); err != nil {
		log.Fatal(err)
	}
}

func ExampleMemoryOperations_ListDomains() {
	listed, err := client.Memory.ListDomains(ctx)
	if err != nil {
		log.Fatal(err)
	}
	for _, domain := range listed.Domains {
		fmt.Println(domain.Slug, "-", domain.Summary)
	}
}

func ExampleMemoryOperations_ListTools() {
	tools, err := client.Memory.ListTools(ctx)
	if err != nil {
		log.Fatal(err)
	}
	for _, tool := range tools {
		fmt.Println(tool.Name, tool.Available)
	}
}

func ExampleMemoryOperations_StartSession() {
	session, err := client.Memory.StartSession(ctx, "coding")
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(session.ID, session.Instructions.Content)
}

func ExampleMemoryOperations_Search() {
	result, err := client.Memory.Search(ctx, "how should a client authenticate", memcoai.SearchParams{
		Domain: "coding",
		Tags:   []memcoai.Tag{{Type: "language", Value: "go"}},
	})
	if err != nil {
		log.Fatal(err)
	}
	for _, memory := range result.Memories {
		for _, insight := range memory.Insights {
			fmt.Println(insight.Title, insight.Updated)
		}
	}
}

func ExampleMemoryOperations_GetMemory() {
	memory, err := client.Memory.GetMemory(ctx, "memory-hpc08-1")
	var notFound *memcoai.NotFoundError
	if errors.As(err, &notFound) {
		fmt.Println("gone:", notFound.Detail)
		return
	}
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(memcoai.Render(memory))
}

func ExampleMemoryOperations_CreateMemory() {
	written, err := client.Memory.CreateMemory(ctx, memcoai.CreateMemoryParams{
		Domain:  "coding",
		Query:   "why does the Go SDK dial with the dns scheme",
		Title:   "A host named after a gRPC scheme is dialled as that scheme",
		Content: "grpc.NewClient reads unix:443 as a unix socket unless the target names dns:///.",
		Tags:    []memcoai.Tag{{Type: "language", Value: "go"}},
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("undo with", written.OperationID)
}

func ExampleMemoryOperations_EnrichMemory() {
	written, err := client.Memory.EnrichMemory(ctx, memcoai.EnrichMemoryParams{
		MemoryIdx: memcoai.NewMemory,
		SessionID: "session-hpc08",
		Title:     "Retries cover reads only",
		Content:   "The SDK retries ListDomains, GetMemory and ListTools, never a write.",
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(written.OperationID)
}

func ExampleMemoryOperations_ShareFeedback() {
	result, err := client.Memory.ShareFeedback(ctx, "session-hpc08", []memcoai.FeedbackRating{
		{Idx: "memory-hpc08-1", Relevant: true, Correct: true},
		{Idx: "memory-hpc08-2-insight-1", Relevant: true, Correct: false, Comment: "out of date"},
	})
	if err != nil {
		log.Fatal(err)
	}
	for _, entry := range result.Entries {
		fmt.Println(entry.Idx, entry.Advice)
	}
}

func ExampleMemoryOperations_RevertMemory() {
	reverted, err := client.Memory.RevertMemory(ctx, "create-hpc08-1")
	if err != nil {
		log.Fatal(err)
	}
	if reverted.Outcome == memcoai.RevertOutcomeNotFound {
		fmt.Println("not processed yet, or never written")
	}
}

func ExampleMemoryOperations_ImportMemories() {
	imported, err := client.Memory.ImportMemories(ctx, []memcoai.ImportedMemory{{
		Queries:  []string{"how are Go SDK releases tagged"},
		Insights: []memcoai.ImportedInsight{{Title: "go/vX.Y.Z", Content: "A module in go/ is tagged go/vX.Y.Z."}},
	}}, memcoai.ImportMemoriesParams{Domain: "coding"})
	if err != nil {
		log.Fatal(err)
	}
	for _, outcome := range imported.Results {
		fmt.Println(outcome.Index, outcome.Status, outcome.Errors)
	}
}

func ExampleSession_Search() {
	result, err := session.Search(ctx, "how does the service count characters", memcoai.ScopedSearchParams{})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(memcoai.Render(result))
}

func ExampleSession_GetMemory() {
	memory, err := session.GetMemory(ctx, "memory-hpc08-1")
	if err != nil {
		log.Fatal(err)
	}
	if _, err := memory.Feedback(ctx, memcoai.MemoryFeedback{Relevant: true, Correct: true}); err != nil {
		log.Fatal(err)
	}
}

func ExampleSession_CreateMemory() {
	written, err := session.CreateMemory(ctx, memcoai.ScopedCreateMemoryParams{
		Query:   "what does a Go SDK error unwrap to",
		Title:   "Errors unwrap to their parent type",
		Content: "errors.As finds *PreconditionFailedError in a *SunsetError.",
	})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(written.OperationID)
}

func ExampleSession_EnrichMemory() {
	result, err := session.Search(ctx, "how does the Go SDK close", memcoai.ScopedSearchParams{})
	if err != nil || len(result.Memories) == 0 {
		log.Fatal("nothing to enrich: ", err)
	}
	_, err = session.EnrichMemory(ctx, memcoai.ScopedEnrichMemoryParams{
		MemoryIdx: result.Memories[0].Idx,
		Title:     "Close takes a context",
		Content:   "Close waits for calls in flight until its context ends, then closes regardless.",
	})
	if err != nil {
		log.Fatal(err)
	}
}

func ExampleSession_ShareFeedback() {
	result, err := session.Search(ctx, "how are tags trimmed", memcoai.ScopedSearchParams{})
	if err != nil {
		log.Fatal(err)
	}
	var ratings []memcoai.FeedbackRating
	for _, memory := range result.Memories {
		ratings = append(ratings, memcoai.FeedbackRating{Idx: memory.Idx, Relevant: true, Correct: true})
	}
	if len(ratings) > 0 {
		if _, err := session.ShareFeedback(ctx, ratings); err != nil {
			log.Fatal(err)
		}
	}
}

func ExampleSession_RevertMemory() {
	written, err := session.CreateMemory(ctx, memcoai.ScopedCreateMemoryParams{Query: "q", Title: "t", Content: "c"})
	if err != nil {
		log.Fatal(err)
	}
	reverted, err := session.RevertMemory(ctx, written.OperationID)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(reverted.Outcome)
}

func ExampleSession_ImportMemories() {
	imported, err := session.ImportMemories(ctx, []memcoai.ImportedMemory{{
		Queries:  []string{"which Go versions does the SDK support"},
		Insights: []memcoai.ImportedInsight{{Title: "Go 1.25 and later", Content: "go.mod declares go 1.25.0."}},
	}})
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(len(imported.Results))
}

func ExampleSession_Tools() {
	for _, tool := range session.Tools().Tools {
		fmt.Println(tool.Name, tool.Parameters.Required)
	}
}

func ExampleTool_Call() {
	search := session.Tools().Tools[0]
	text, err := search.Call(ctx, json.RawMessage(`{"query": "how are retries decided"}`))
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(text)
}

func ExampleToolset_Call() {
	// A model's tool call, as the provider delivered it.
	name, arguments := "memco_search", json.RawMessage(`{"query": "how does the SDK log"}`)
	text, err := session.Tools().Call(ctx, name, arguments)
	if err != nil {
		log.Fatal(err) // not something a model could fix
	}
	fmt.Println(text) // the result, or what the model got wrong
}

func ExampleToolset_ToAnthropic() {
	tools, err := json.Marshal(session.Tools().ToAnthropic())
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println(string(tools)) // the "tools" of a Messages API request
}

func ExampleToolset_ToOpenAI() {
	encoder := json.NewEncoder(os.Stdout)
	if err := encoder.Encode(session.Tools().ToOpenAI()); err != nil {
		log.Fatal(err)
	}
}

func ExampleMemory_Feedback() {
	result, err := client.Memory.Search(ctx, "how are dates reported", memcoai.SearchParams{Domain: "coding"})
	if err != nil {
		log.Fatal(err)
	}
	for _, memory := range result.Memories {
		entry, err := memory.Feedback(ctx, memcoai.MemoryFeedback{Relevant: true, Correct: true})
		if err != nil {
			log.Fatal(err)
		}
		fmt.Println(entry.Advice)
	}
}

func ExampleRender() {
	fmt.Println(memcoai.Render(memcoai.WriteResult{OperationID: "create-hpc08-1"}))
	// Output: accepted as operation create-hpc08-1
}

func ExampleBriefing() {
	listed, err := client.Memory.ListDomains(ctx)
	if err != nil {
		log.Fatal(err)
	}
	session, err := client.Memory.StartSession(ctx, "coding")
	if err != nil {
		log.Fatal(err)
	}
	for _, domain := range listed.Domains {
		if domain.Slug == "coding" {
			fmt.Println(memcoai.Briefing(domain, session.Instructions))
		}
	}
}
