package main

import (
	"context"
	"errors"
	"testing"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

func TestItImportsTheBatchTwiceAndReportsEachEntry(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.Respond("ImportMemories", &memoryv1.ImportMemoriesResponse{Results: []*memoryv1.ImportOutcome{
		{Index: 0, Status: memoryv1.ImportStatus_IMPORT_STATUS_REJECTED, Errors: []string{"query too short"}},
		{Index: 1, Status: memoryv1.ImportStatus_IMPORT_STATUS_ERROR},
	}})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t,
		"[0] REJECTED: "+batch[0].Queries[0],
		"      query too short",
		"[1] ERROR: "+batch[1].Queries[0],
		"      not queued; send this one again",
		"resent: [REJECTED, ERROR]",
	)
	sent := r.Server.Memory.Request("ImportMemories").(*memoryv1.ImportMemoriesRequest)
	if sent.GetDomain() != "coding" || sent.GetSessionId() != "" || len(sent.GetMemories()) != len(batch) {
		t.Fatalf("sent %v", sent)
	}
	imports := 0
	for _, call := range r.Server.Memory.Calls() {
		if call == "ImportMemories" {
			imports++
		}
	}
	if imports != 2 {
		t.Fatalf("imported %d times", imports)
	}
}

func TestQueuedEntriesNeedNoAction(t *testing.T) {
	r := exampletest.Start(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "[0] QUEUED: ", "[1] QUEUED: ", "resent: [QUEUED, QUEUED]")
}

func TestAnEndedContextStopsIt(t *testing.T) {
	r := exampletest.Start(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := run(ctx, r.Options, r.Out); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}
