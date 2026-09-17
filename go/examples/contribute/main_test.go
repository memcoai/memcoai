package main

import (
	"context"
	"errors"
	"testing"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

func TestItWritesEnrichesAndRevertsTheCreate(t *testing.T) {
	r := exampletest.Start(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "created: create-a", "enriched: enrich-a", "revert: MERGED")
	created := r.Server.Memory.Request("CreateMemory").(*memoryv1.CreateMemoryRequest)
	if created.GetSessionId() != "session-a" || created.GetDomain() != "" || len(created.GetTags()) != 2 {
		t.Fatalf("created %v", created)
	}
	enriched := r.Server.Memory.Request("EnrichMemory").(*memoryv1.EnrichMemoryRequest)
	if enriched.GetMemoryIdx() != "new" || enriched.GetSessionId() != "session-a" {
		t.Fatalf("enriched %v", enriched)
	}
	if reverted := r.Server.Memory.Request("RevertMemory").(*memoryv1.RevertMemoryRequest); reverted.GetOpId() != "create-a" {
		t.Fatalf("reverted %v", reverted)
	}
}

func TestEachRevertOutcomeIsExplained(t *testing.T) {
	cases := map[memoryv1.RevertOutcome]string{
		memoryv1.RevertOutcome_REVERT_OUTCOME_EXPIRED:   "  outside the revert window",
		memoryv1.RevertOutcome_REVERT_OUTCOME_NOT_FOUND: "  ingestion may still be running; try again shortly",
	}
	for outcome, explained := range cases {
		t.Run(outcome.String(), func(t *testing.T) {
			r := exampletest.Start(t)
			r.Server.Memory.Respond("RevertMemory", &memoryv1.RevertMemoryResponse{OperationId: "create-a", Outcome: outcome})
			if err := run(context.Background(), r.Options, r.Out); err != nil {
				t.Fatal(err)
			}
			r.Expect(t, explained)
		})
	}
}

func TestAWriteThatCannotBeUndoneIsNotReverted(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.Respond("CreateMemory", &memoryv1.CreateMemoryResponse{})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "created: \n", "  (accepted, but not revertible)", "enriched: enrich-a")
	for _, call := range r.Server.Memory.Calls() {
		if call == "RevertMemory" {
			t.Fatal("reverted a write with no operation id")
		}
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
