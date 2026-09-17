// Concurrent_searches runs many searches at once on one connection.
//
// One client holds one channel, and gRPC multiplexes concurrent calls over it,
// so there is no need for a client per goroutine. Failures are handled per
// search, so one bad query does not sink the batch.
//
// Run it with:
//
//	export MEMCO_API_TOKEN=...
//	go run ./concurrent_searches
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"os/signal"
	"sync"

	"github.com/memcoai/memcoai/go/memcoai"
)

var queries = []string{
	"how does gRPC health checking work",
	"what does a case-sensitive Bearer prefix imply for clients",
	"how should a client tell a transient failure from a permanent one",
	"when should a write be retried",
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
	// A Client and a Session are safe for concurrent use.
	session, err := client.Memory.StartSession(ctx, "coding")
	if err != nil {
		return err
	}

	summaries, err := searchAll(ctx, session)
	if err != nil {
		return err
	}
	for i, query := range queries {
		out.Printf("%-32s %s", summaries[i], query)
	}
	return nil
}

// searchAll runs every query at once, and summarises each in order.
func searchAll(ctx context.Context, session *memcoai.Session) ([]string, error) {
	// Each goroutine writes only its own slot, so nothing needs a lock.
	summaries := make([]string, len(queries))
	failures := make([]error, len(queries))
	var wg sync.WaitGroup
	for i, query := range queries {
		wg.Go(func() { summaries[i], failures[i] = search(ctx, session, query) })
	}
	wg.Wait()
	return summaries, errors.Join(failures...)
}

// search runs one search and summarises how it went. A failure the service
// reported is part of the summary; any other is returned.
func search(ctx context.Context, session *memcoai.Session, query string) (string, error) {
	result, err := session.Search(ctx, query, memcoai.ScopedSearchParams{})
	var api *memcoai.APIError
	switch {
	case errors.As(err, &api) && ctx.Err() == nil:
		return "failed: " + api.Error(), nil
	case err != nil:
		return "", err
	}
	insights := 0
	for _, memory := range result.Memories {
		insights += len(memory.Insights)
	}
	return fmt.Sprintf("%d memories, %d insights", len(result.Memories), insights), nil
}
