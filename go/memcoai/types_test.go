package memcoai

import (
	"testing"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

func TestEnumValuesAreTheContracts(t *testing.T) {
	for value, name := range memoryv1.DataSource_name {
		if got := DataSource(value).String(); "DATA_SOURCE_"+got != name {
			t.Errorf("DataSource(%d) = %q, contract %q", value, got, name)
		}
	}
	for value, name := range memoryv1.RevertOutcome_name {
		if got := RevertOutcome(value).String(); "REVERT_OUTCOME_"+got != name {
			t.Errorf("RevertOutcome(%d) = %q, contract %q", value, got, name)
		}
	}
	for value, name := range memoryv1.ImportStatus_name {
		if got := ImportStatus(value).String(); "IMPORT_STATUS_"+got != name {
			t.Errorf("ImportStatus(%d) = %q, contract %q", value, got, name)
		}
	}
	if DataSourceAgent != DataSource(memoryv1.DataSource_DATA_SOURCE_AGENT) ||
		RevertOutcomeRefused != RevertOutcome(memoryv1.RevertOutcome_REVERT_OUTCOME_REFUSED) ||
		ImportStatusDuplicate != ImportStatus(memoryv1.ImportStatus_IMPORT_STATUS_DUPLICATE) {
		t.Error("a constant differs from the contract's number")
	}
}

func TestAValueThisBuildDoesNotKnowIsNamedByItsNumber(t *testing.T) {
	if got := RevertOutcome(42).String(); got != "RevertOutcome(42)" {
		t.Errorf("got %q", got)
	}
	if got := ImportStatus(-1).String(); got != "ImportStatus(-1)" {
		t.Errorf("got %q", got)
	}
	if got := DataSource(9).String(); got != "DataSource(9)" {
		t.Errorf("got %q", got)
	}
}
