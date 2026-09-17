// Import_memories contributes a batch of memories in one call, and reads what
// became of each.
//
// Use this to move knowledge you already hold — a wiki export, a runbook,
// notes from another system — into a domain in bulk. For knowledge learned
// during a task, CreateMemory is the call: it returns an operation id you can
// undo with, and an import does not.
//
// Run it with:
//
//	export MEMCO_API_TOKEN=...
//	go run ./import_memories
package main

import (
	"context"
	"errors"
	"log"
	"os"
	"os/signal"
	"strings"

	"github.com/memcoai/memcoai/go/memcoai"
)

const domain = "coding"

var batch = []memcoai.ImportedMemory{
	{
		// Queries are what someone would search to find this memory. More
		// than one is worth giving: they are the ways in, not a description.
		Queries: []string{
			"how do I authenticate against the Memco memory API",
			"which header does the memory API take a token in",
		},
		Insights: []memcoai.ImportedInsight{{
			Title: "The Bearer prefix is case-sensitive",
			Content: "Both an API key and a session token go in the same Authorization " +
				"header. A lowercase 'bearer' is rejected with UNAUTHENTICATED, " +
				"which reads as a bad credential rather than a malformed header.",
		}},
		Tags: []memcoai.Tag{{Type: "language", Value: "go"}},
	},
	{
		Queries: []string{"why is a memory write not retried after a connection blip"},
		Insights: []memcoai.ImportedInsight{{
			Title: "gRPC retries are at-least-once, so writes stay out of the policy",
			Content: "A retry sent after the server committed produces a duplicate, not " +
				"a second chance. Only ListDomains, GetMemory and ListTools are replayed.",
		}},
	},
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	err := run(ctx, memcoai.Options{}, log.New(os.Stdout, "", 0))
	stop()
	if err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context, opts memcoai.Options, out *log.Logger) (err error) {
	client, err := memcoai.NewClient(opts)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, client.Close(ctx)) }()
	if err := client.Connect(ctx); err != nil {
		return err
	}

	// No session: a standalone upload belongs to no series of work. Pass
	// SessionID instead when the batch was gathered during one task.
	//
	// The batch can be any length. The service caps how many memories one
	// call carries, and the SDK divides a longer batch into calls of that
	// size, so a wiki export goes in as one statement, not a chunking loop.
	result, err := client.Memory.ImportMemories(ctx, batch, memcoai.ImportMemoriesParams{Domain: domain})
	if err != nil {
		return err
	}
	report(result, out)

	// Sending the same batch twice is safe and free: an import is written
	// under an identity derived from its own content, so a second run reports
	// DUPLICATE and writes nothing. Nothing undoes an import, and this is what
	// stands in for that.
	again, err := client.Memory.ImportMemories(ctx, batch, memcoai.ImportMemoriesParams{Domain: domain})
	if err != nil {
		return err
	}
	out.Printf("resent: [%s]", strings.Join(statuses(again), ", "))
	return nil
}

// report prints what became of each entry, and what to do about it.
func report(result *memcoai.ImportResult, out *log.Logger) {
	for _, outcome := range result.Results {
		// Index always indexes batch, whatever the service's limit is: the SDK
		// renumbers every response back into the slice it was given.
		out.Printf("[%d] %s: %s", outcome.Index, outcome.Status, batch[outcome.Index].Queries[0])
		switch outcome.Status {
		case memcoai.ImportStatusRejected:
			// The entry itself was not usable; fix it and send it again.
			for _, problem := range outcome.Errors {
				out.Printf("      %s", problem)
			}
		case memcoai.ImportStatusError:
			// Usable, but not queued. Resubmitting is the whole remedy.
			out.Print("      not queued; send this one again")
		}
	}
}

// statuses names each entry's status, in order.
func statuses(result *memcoai.ImportResult) []string {
	names := make([]string, 0, len(result.Results))
	for _, outcome := range result.Results {
		names = append(names, outcome.Status.String())
	}
	return names
}
