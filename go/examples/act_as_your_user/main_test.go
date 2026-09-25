package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

func TestItSearchesWritesAndRevertsAsTheUser(t *testing.T) {
	r := exampletest.StartAPIClient(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "-alice sees 0 memories", "written as goex-", "-alice: create-a", "revert: MERGED", "removed again")
	for i, md := range r.Server.Memory.Metadata() {
		if bearer := strings.Join(md.Get("authorization"), ","); !strings.HasPrefix(bearer, "Bearer impersonation-goex-") {
			t.Errorf("%s carried %q", r.Server.Memory.Calls()[i], bearer)
		}
	}
	if query := r.Server.Memory.Request("CreateMemory").(*memoryv1.CreateMemoryRequest).GetQuery(); !strings.Contains(query, "(goex-") {
		t.Fatalf("query %q", query)
	}
	ended := 0
	for _, call := range r.Server.Admin.Calls() {
		if call == "EndImpersonation" {
			ended++
		}
	}
	if ended != 1 {
		t.Fatalf("ended %d keys", ended)
	}
}

func TestARevertWaitingOnIngestionStopsWithItsContextAndStillCleansUp(t *testing.T) {
	r := exampletest.StartAPIClient(t)
	r.Server.Memory.Respond("RevertMemory", &memoryv1.RevertMemoryResponse{
		OperationId: "create-a", Outcome: memoryv1.RevertOutcome_REVERT_OUTCOME_NOT_FOUND,
	})
	// Short enough for two attempts inside the deadline, long enough that the
	// attempts outlast it.
	revertPause = 100 * time.Millisecond
	t.Cleanup(func() { revertPause = time.Second })
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := run(ctx, r.Options, r.Out); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v", err)
	}
	reverts := 0
	for _, call := range r.Server.Memory.Calls() {
		if call == "RevertMemory" {
			reverts++
		}
	}
	if reverts < 2 {
		t.Fatalf("tried %d reverts", reverts)
	}
	r.Expect(t, "removed again")
}
