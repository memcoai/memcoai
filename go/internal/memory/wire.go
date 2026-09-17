package memory

import (
	"fmt"
	"time"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/fault"
)

// Day keeps a YYYY-MM-DD date that names a real day, and drops anything else.
// It stays a string: a time.Time would carry a time zone the date does not.
func Day(s string) string {
	parsed, err := time.Parse(time.DateOnly, s)
	if err != nil || parsed.Year() < 1 {
		return ""
	}
	return s
}

func FoldRevertOutcome(v memoryv1.RevertOutcome) memoryv1.RevertOutcome {
	if _, known := memoryv1.RevertOutcome_name[int32(v)]; known {
		return v
	}
	return memoryv1.RevertOutcome_REVERT_OUTCOME_UNSPECIFIED
}

func FoldImportStatus(v memoryv1.ImportStatus) memoryv1.ImportStatus {
	if _, known := memoryv1.ImportStatus_name[int32(v)]; known {
		return v
	}
	return memoryv1.ImportStatus_IMPORT_STATUS_UNSPECIFIED
}

// RequireMemory refuses a response that carries no memory.
func RequireMemory(resp *memoryv1.GetMemoryResponse, idx string) (*memoryv1.MemoryResult, error) {
	if resp.GetMemory() == nil {
		return nil, fault.NotFound(fmt.Sprintf("no memory was returned for %q", idx))
	}
	return resp.GetMemory(), nil
}
