// Contribute writes knowledge back, adds to it, and undoes a write.
//
// Run it with:
//
//	export MEMCO_API_TOKEN=...
//	go run ./contribute
package main

import (
	"context"
	"errors"
	"log"
	"os"
	"os/signal"

	"github.com/memcoai/memcoai/go/memcoai"
)

const domain = "coding"

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

	// The calls below pass the session's ID by hand rather than going through
	// the session. That is the shape to prefer when the ID has to travel
	// somewhere a *Session cannot: into a database row, across a queue, or
	// into a log line to be reattached later.
	session, err := client.Memory.StartSession(ctx, domain)
	if err != nil {
		return err
	}

	created, err := create(ctx, client, session.ID, out)
	if err != nil {
		return err
	}
	// The enrichment is left in place, so each run adds one memory to the
	// domain. Undoing it is the same call as for the create; only the create
	// is reverted below, so there is one revert to read rather than two.
	if err := enrich(ctx, client, session.ID, out); err != nil {
		return err
	}
	if created == "" {
		return nil
	}
	return revert(ctx, client, created, out)
}

// create writes a new memory, and returns the operation id that undoes it.
func create(ctx context.Context, client *memcoai.Client, sessionID string, out *log.Logger) (string, error) {
	// Writes are accepted asynchronously, so the result addresses the
	// operation rather than the memory it will become.
	created, err := client.Memory.CreateMemory(ctx, memcoai.CreateMemoryParams{
		Query: "how do I authenticate against the Memco memory API",
		Title: "Retries must not be applied to memory writes",
		Content: "A write is accepted asynchronously and its operation id identifies " +
			"that one acceptance, so retrying a write that appears to fail can " +
			"record it twice. Retry the read operations instead, and use the " +
			"returned operation id to undo a write you did not mean to make.",
		SessionID: sessionID,
		Tags: []memcoai.Tag{
			{Type: "language", Value: "go"},
			{Type: "task", Value: "implementation"},
		},
	})
	if err != nil {
		return "", err
	}
	out.Printf("created: %s", created.OperationID)
	// An empty operation id means the write was accepted but cannot be
	// undone: the content is worth more than the ability to revert it.
	if created.OperationID == "" {
		out.Print("  (accepted, but not revertible)")
	}
	return created.OperationID, nil
}

// enrich adds to a memory, here a new one.
func enrich(ctx context.Context, client *memcoai.Client, sessionID string, out *log.Logger) error {
	// Enrichment adds to a memory a search returned, so the addition lands
	// alongside the existing insights instead of becoming a rival memory.
	// NewMemory opens a standalone memory instead; the service reads it
	// case-sensitively, so prefer the constant to typing "new".
	enriched, err := client.Memory.EnrichMemory(ctx, memcoai.EnrichMemoryParams{
		MemoryIdx: memcoai.NewMemory,
		SessionID: sessionID,
		Title:     "Connecting is what teaches a client the service's limits",
		Content: "Connect probes health and then calls ListDomains. The second call " +
			"carries the credential, so a bad token fails there, and it reports " +
			"the caps the service enforces — which is why an oversized field is " +
			"refused locally rather than after a round trip.",
	})
	if err != nil {
		return err
	}
	out.Printf("enriched: %s", enriched.OperationID)
	return nil
}

// revert undoes a write, and says what that removed.
func revert(ctx context.Context, client *memcoai.Client, operationID string, out *log.Logger) error {
	// Reverting reports what it actually removed. Not found, expired and
	// refused are outcomes, not errors: they describe caller-visible state.
	reverted, err := client.Memory.RevertMemory(ctx, operationID)
	if err != nil {
		return err
	}
	out.Printf("revert: %s", reverted.Outcome)
	switch reverted.Outcome {
	case memcoai.RevertOutcomeExpired:
		out.Print("  outside the revert window")
	case memcoai.RevertOutcomeNotFound:
		out.Print("  ingestion may still be running; try again shortly")
	}
	return nil
}
