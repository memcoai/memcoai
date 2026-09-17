package memcoai

import (
	"reflect"
	"testing"

	"google.golang.org/protobuf/proto"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

var wireInstructions = &memoryv1.Instructions{Content: "c", Policy: "p", Adding: "a", Rating: "r", Next: "n"}

func TestInstructionsAreReadPartForPart(t *testing.T) {
	if got := toInstructions(wireInstructions); got != (Instructions{"c", "p", "a", "r", "n"}) {
		t.Fatalf("got %+v", got)
	}
	if got := toInstructions(nil); got != (Instructions{}) {
		t.Fatalf("absent instructions: %+v", got)
	}
}

func TestADomainListIsReadFieldForField(t *testing.T) {
	response := &memoryv1.ListDomainsResponse{
		Domains: []*memoryv1.DomainEntry{{
			Slug: "coding", Title: "Coding", Summary: "s", WhenToSearch: "ws", WhenToSave: "wv",
			WhatNotToSave: "wn", TagsDescription: "td", FilterTagTypes: []string{"language"},
			VersionTagTypes: []string{"library"}, MaxTagsPerQuery: 5,
		}},
		Instructions: wireInstructions,
		Limits: &memoryv1.Limits{
			MaxQueryCharacters: 1, MaxTextCharacters: 2, MaxIdxCharacters: 3, MaxSources: 4,
			MaxFeedbackEntries: 5, MaxImportMemories: 6, MaxImportQueriesPerMemory: 7,
			MaxImportInsightsPerMemory: 8, MaxImportTagsPerMemory: 9,
		},
		Deprecated:         true,
		DeprecationMessage: "upgrade",
		SunsetDate:         "2027-01-01",
		ServerCommit:       "abc",
	}
	want := &DomainList{
		Domains: []DomainEntry{{
			Slug: "coding", Title: "Coding", Summary: "s", WhenToSearch: "ws", WhenToSave: "wv",
			WhatNotToSave: "wn", TagsDescription: "td", FilterTagTypes: []string{"language"},
			VersionTagTypes: []string{"library"}, MaxTagsPerQuery: 5,
		}},
		Instructions:       Instructions{"c", "p", "a", "r", "n"},
		Limits:             &Limits{1, 2, 3, 4, 5, 6, 7, 8, 9},
		Deprecated:         true,
		DeprecationMessage: "upgrade",
		SunsetDate:         "2027-01-01",
		ServerCommit:       "abc",
	}
	got := toDomainList(response)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v", got)
	}
	response.Domains[0].FilterTagTypes[0] = "changed"
	if got.Domains[0].FilterTagTypes[0] != "language" {
		t.Fatal("the result shares a slice with the response")
	}
}

func TestAnOlderServiceReportsNoLimitsAndNoNotice(t *testing.T) {
	got := toDomainList(&memoryv1.ListDomainsResponse{SunsetDate: "someday"})
	if got.Limits != nil || got.Deprecated || got.DeprecationMessage != "" || got.SunsetDate != "" {
		t.Fatalf("got %+v", got)
	}
	if got.Domains == nil {
		t.Fatal("an empty domain list reads as nil")
	}
}

func TestTheToolCatalogIsRead(t *testing.T) {
	got := toTools(&memoryv1.ListToolsResponse{Tools: []*memoryv1.ToolDescriptor{
		{Name: "search", Description: "d", Available: true},
		{Name: "revert_memory"},
	}})
	want := []ToolDescriptor{{"search", "d", true}, {"revert_memory", "", false}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v", got)
	}
}

func wireMemory() *memoryv1.MemoryResult {
	return &memoryv1.MemoryResult{
		Idx: "memory-a-1", Kind: "policy", TimesServed: 3, Intents: []string{"why"},
		Insights: []*memoryv1.InsightResult{{
			Idx: "memory-a-1-insight-1", Title: "t", Content: "c", Updated: "2026-08-26",
			TimesServed: 2, Endorsed: 1, Disputed: 4,
		}, {
			Idx: "memory-a-1-insight-2", Updated: "2026-02-30",
		}},
	}
}

func TestAMemoryIsReadAndBound(t *testing.T) {
	ops := &MemoryOperations{}
	got := toMemory(wireMemory(), ops, "session-a")
	want := Memory{
		Idx: "memory-a-1", Kind: "policy", TimesServed: 3, Intents: []string{"why"},
		Insights: []Insight{
			{Idx: "memory-a-1-insight-1", Title: "t", Content: "c", Updated: "2026-08-26", TimesServed: 2, Endorsed: 1, Disputed: 4},
			{Idx: "memory-a-1-insight-2"},
		},
		ops:       ops,
		sessionID: "session-a",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v", got)
	}
	reference := toMemory(&memoryv1.MemoryResult{Idx: "memory-b-1", Reference: "memory-a-1"}, ops, "s")
	if reference.Reference != "memory-a-1" || reference.Insights == nil || len(reference.Insights) != 0 {
		t.Fatalf("got %+v", reference)
	}
}

func TestASearchResultBindsItsOwnSession(t *testing.T) {
	ops := &MemoryOperations{}
	got := toSearchResult(&memoryv1.SearchResponse{
		SessionId: "session-new", Notice: "narrowed", Instructions: wireInstructions,
		Memories: []*memoryv1.MemoryResult{wireMemory()},
	}, ops)
	if got.SessionID != "session-new" || got.Notice != "narrowed" || got.Instructions.Content != "c" {
		t.Fatalf("got %+v", got)
	}
	if len(got.Memories) != 1 || got.Memories[0].sessionID != "session-new" || got.Memories[0].ops != ops {
		t.Fatalf("memories %+v", got.Memories)
	}
	if empty := toSearchResult(&memoryv1.SearchResponse{}, ops); empty.Memories == nil || empty.Notice != "" {
		t.Fatalf("empty %+v", empty)
	}
}

func TestWritesFeedbackAndRevertsAreRead(t *testing.T) {
	if got := toWriteResult("create-a", wireInstructions); got.OperationID != "create-a" || got.Instructions.Next != "n" {
		t.Fatalf("write %+v", got)
	}
	if got := toWriteResult("", nil); got.OperationID != "" || got.Instructions != (Instructions{}) {
		t.Fatalf("un-revertible write %+v", got)
	}
	feedback := toFeedbackResult(&memoryv1.ShareFeedbackResponse{
		SessionId: "s", Instructions: wireInstructions,
		Entries: []*memoryv1.FeedbackEntry{{Idx: "i", Relevant: true, Correct: false, Advice: "look again"}, {Idx: "j"}},
	})
	if !reflect.DeepEqual(feedback.Entries, []FeedbackEntry{{"i", true, false, "look again"}, {Idx: "j"}}) || feedback.SessionID != "s" {
		t.Fatalf("feedback %+v", feedback)
	}
	revert := toRevertResult(&memoryv1.RevertMemoryResponse{OperationId: "create-a", Outcome: memoryv1.RevertOutcome_REVERT_OUTCOME_EXPIRED})
	if revert.OperationID != "create-a" || revert.Outcome != RevertOutcomeExpired {
		t.Fatalf("revert %+v", revert)
	}
	if unknown := toRevertResult(&memoryv1.RevertMemoryResponse{Outcome: 42}); unknown.Outcome != RevertOutcomeUnspecified {
		t.Fatalf("unknown outcome %+v", unknown)
	}
}

func TestSplitImportsAreRenumberedToTheCallersPositions(t *testing.T) {
	got := toImportResult([]importPart{
		{0, &memoryv1.ImportMemoriesResponse{Instructions: wireInstructions, Results: []*memoryv1.ImportOutcome{
			{Index: 0, Status: memoryv1.ImportStatus_IMPORT_STATUS_QUEUED},
			{Index: 1, Status: memoryv1.ImportStatus_IMPORT_STATUS_REJECTED, Errors: []string{"too short"}},
		}}},
		{2, &memoryv1.ImportMemoriesResponse{Instructions: &memoryv1.Instructions{Content: "second"}, Results: []*memoryv1.ImportOutcome{
			{Index: 0, Status: 42},
		}}},
	})
	want := &ImportResult{
		Results: []ImportOutcome{
			{Index: 0, Status: ImportStatusQueued, Errors: []string{}},
			{Index: 1, Status: ImportStatusRejected, Errors: []string{"too short"}},
			{Index: 2, Status: ImportStatusUnspecified, Errors: []string{}},
		},
		Instructions: Instructions{"c", "p", "a", "r", "n"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v", got)
	}
}

func TestOptionalWireFieldsAreSentOnlyWhenSet(t *testing.T) {
	version := "1.25"
	tags := wireTags([]Tag{{Type: "language", Value: "go"}, {Type: "language", Value: "go", Version: "1.25"}})
	want := []*memoryv1.Tag{{Type: "language", Value: "go"}, {Type: "language", Value: "go", Version: &version}}
	if len(tags) != 2 || !proto.Equal(tags[0], want[0]) || !proto.Equal(tags[1], want[1]) || tags[0].Version != nil {
		t.Fatalf("tags %v", tags)
	}
	if wireTags(nil) != nil {
		t.Fatal("no tags became an empty slice")
	}
	comment := "why"
	ratings := wireRatings([]FeedbackRating{{Idx: "i", Relevant: true}, {Idx: "j", Correct: true, Comment: "why"}})
	if ratings[0].Comment != nil || !proto.Equal(ratings[1], &memoryv1.FeedbackRating{Idx: "j", Correct: true, Comment: &comment}) {
		t.Fatalf("ratings %v", ratings)
	}
}

func TestAnUnsetSourceIsSentAsAgent(t *testing.T) {
	if wireSource(DataSourceUnspecified) != memoryv1.DataSource_DATA_SOURCE_AGENT {
		t.Fatal("unset")
	}
	if wireSource(DataSourceUser) != memoryv1.DataSource_DATA_SOURCE_USER {
		t.Fatal("user")
	}
}

func TestImportedMemoriesAreCopiedNotShared(t *testing.T) {
	input := []ImportedMemory{{
		Queries:  []string{"q"},
		Insights: []ImportedInsight{{Title: "t", Content: "c"}},
		Tags:     []Tag{{Type: "a", Value: "b"}},
	}}
	got := wireMemories(input)
	want := &memoryv1.ImportedMemory{
		Queries:  []string{"q"},
		Insights: []*memoryv1.ImportedInsight{{Title: "t", Content: "c"}},
		Tags:     []*memoryv1.Tag{{Type: "a", Value: "b"}},
	}
	if len(got) != 1 || !proto.Equal(got[0], want) {
		t.Fatalf("got %v", got)
	}
	got[0].Queries[0] = "changed"
	if input[0].Queries[0] != "q" {
		t.Fatal("the wire copy shares the caller's slice")
	}
}
