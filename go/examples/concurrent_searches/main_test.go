package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
	"github.com/memcoai/memcoai/go/memcoai"
)

func TestEveryQueryIsSearchedUnderTheSession(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.Respond("Search", &memoryv1.SearchResponse{Memories: []*memoryv1.MemoryResult{
		{Idx: "m1", Insights: []*memoryv1.InsightResult{{Idx: "i1"}, {Idx: "i2"}}},
		{Idx: "m2", Reference: "earlier"},
	}})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	var want []string
	for _, query := range queries {
		want = append(want, fmt.Sprintf("%-32s %s\n", "2 memories, 2 insights", query))
	}
	r.Expect(t, want...)
	if n := strings.Count(r.Printed(), "\n"); n != len(queries) {
		t.Fatalf("printed %d lines", n)
	}
	if sent := r.Server.Memory.Request("Search").(*memoryv1.SearchRequest); sent.GetSessionId() != "session-a" {
		t.Fatalf("sent %v", sent)
	}
}

func TestTheSearchesRunConcurrently(t *testing.T) {
	r := exampletest.Start(t)
	delay := 300 * time.Millisecond
	r.Server.Memory.Delay("Search", delay)
	started := time.Now()
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed >= time.Duration(len(queries)-1)*delay {
		t.Fatalf("took %s: the searches ran one after another", elapsed)
	}
}

func TestOneFailedSearchDoesNotSinkTheBatch(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.FailNext("Search", testserver.Failure{Code: codes.Internal, Details: "boom"})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	if strings.Count(r.Printed(), "failed: INTERNAL: boom") != 1 || strings.Count(r.Printed(), "0 memories, 0 insights") != len(queries)-1 {
		t.Fatalf("printed:\n%s", r.Printed())
	}
}

func TestAFailureTheBatchCannotReportIsReturned(t *testing.T) {
	r := exampletest.Start(t)
	client, err := memcoai.NewClient(r.Options)
	if err != nil {
		t.Fatal(err)
	}
	session, err := client.Memory.StartSession(context.Background(), "coding")
	if err != nil {
		t.Fatal(err)
	}
	if err := client.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	var config *memcoai.ConfigError
	if _, err := search(context.Background(), session, "q"); !errors.As(err, &config) {
		t.Fatalf("got %v", err)
	}
}

func TestAnEndedContextStopsIt(t *testing.T) {
	r := exampletest.Start(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := run(ctx, r.Options, r.Out); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}
