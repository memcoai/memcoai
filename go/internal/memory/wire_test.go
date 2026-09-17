package memory

import (
	"errors"
	"testing"

	"google.golang.org/grpc/codes"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

func TestOnlyARealDayIsKept(t *testing.T) {
	kept := []string{"2026-08-26", "2024-02-29", "2000-02-29", "0001-01-01", "9999-12-31"}
	for _, day := range kept {
		if got := Day(day); got != day {
			t.Errorf("%q: got %q", day, got)
		}
	}
	dropped := []string{
		"", "2026-8-26", "2026-08-6", "26-08-26", "2026/08/26", "2026-08-26T00:00:00Z",
		"2026-08-26\n", " 2026-08-26", "2026-13-01", "2026-00-10", "2026-02-30",
		"2023-02-29", "1900-02-29", "0000-02-29", "0000-01-01", "2026-W35-3",
		"２０２６-08-26", "abcd-ef-gh",
	}
	for _, day := range dropped {
		if got := Day(day); got != "" {
			t.Errorf("%q: kept as %q", day, got)
		}
	}
}

func TestUnknownEnumValuesFoldToUnspecified(t *testing.T) {
	if got := FoldRevertOutcome(memoryv1.RevertOutcome_REVERT_OUTCOME_MERGED); got != memoryv1.RevertOutcome_REVERT_OUTCOME_MERGED {
		t.Errorf("merged: %v", got)
	}
	if got := FoldRevertOutcome(99); got != memoryv1.RevertOutcome_REVERT_OUTCOME_UNSPECIFIED {
		t.Errorf("99: %v", got)
	}
	if got := FoldImportStatus(memoryv1.ImportStatus_IMPORT_STATUS_DUPLICATE); got != memoryv1.ImportStatus_IMPORT_STATUS_DUPLICATE {
		t.Errorf("duplicate: %v", got)
	}
	if got := FoldImportStatus(-1); got != memoryv1.ImportStatus_IMPORT_STATUS_UNSPECIFIED {
		t.Errorf("-1: %v", got)
	}
}

func TestAResponseWithoutAMemoryIsNotFound(t *testing.T) {
	_, err := RequireMemory(&memoryv1.GetMemoryResponse{}, "memory-a-1")
	var f *fault.Error
	if !errors.As(err, &f) || f.Code != codes.NotFound || f.Detail != `no memory was returned for "memory-a-1"` {
		t.Fatalf("got %v", err)
	}
	// Presence, not content: an all-empty memory is still a memory.
	memory, err := RequireMemory(&memoryv1.GetMemoryResponse{Memory: &memoryv1.MemoryResult{}}, "x")
	if err != nil || memory == nil {
		t.Fatalf("got %v, %v", memory, err)
	}
}
