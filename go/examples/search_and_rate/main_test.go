package main

import (
	"context"
	"errors"
	"strings"
	"testing"

	"google.golang.org/protobuf/proto"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
)

func TestItRatesEveryInsightItWasShown(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.Respond("Search", &memoryv1.SearchResponse{
		SessionId: "session-a",
		Notice:    "results are sparse",
		Memories: []*memoryv1.MemoryResult{{Idx: "memory-a-1", Insights: []*memoryv1.InsightResult{
			{Idx: "memory-a-1-insight-1", Title: "Probe health without a credential", Updated: "2026-09-01", Endorsed: 4, Disputed: 1, Content: "The health service is public."},
			{Idx: "memory-a-1-insight-2", Title: "Two", Content: "Second."},
		}}},
	})
	r.Server.Memory.Respond("ShareFeedback", &memoryv1.ShareFeedbackResponse{Entries: []*memoryv1.FeedbackEntry{
		{Idx: "memory-a-1-insight-1", Relevant: true, Correct: true, Advice: "thanks"},
		{Idx: "memory-a-1-insight-2", Relevant: true, Correct: true},
	}})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t,
		"session session-a",
		"notice: results are sparse",
		"Probe health without a credential  (updated 2026-09-01)",
		"  endorsed 4 / disputed 1",
		"  The health service is public.",
		"recorded 2 rating(s)",
		"  memory-a-1-insight-1: thanks",
	)
	comment := "answered the question directly"
	want := &memoryv1.ShareFeedbackRequest{SessionId: "session-a", Feedback: []*memoryv1.FeedbackRating{
		{Idx: "memory-a-1-insight-1", Relevant: true, Correct: true, Comment: &comment},
		{Idx: "memory-a-1-insight-2", Relevant: true, Correct: true, Comment: &comment},
	}}
	if sent := r.Server.Memory.Request("ShareFeedback"); !proto.Equal(sent, want) {
		t.Fatalf("sent %v", sent)
	}
	search := r.Server.Memory.Request("Search").(*memoryv1.SearchRequest)
	if search.GetSessionId() != "session-a" || len(search.GetTags()) != 1 {
		t.Fatalf("searched %v", search)
	}
}

func TestALongInsightIsCutShort(t *testing.T) {
	r := exampletest.Start(t)
	long := strings.Repeat("é", 250)
	r.Server.Memory.Respond("Search", &memoryv1.SearchResponse{Memories: []*memoryv1.MemoryResult{{Idx: "m", Insights: []*memoryv1.InsightResult{{Idx: "i", Content: long}}}}})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "  "+long[:400]+"...\n")
}

func TestNothingMatchedRatesNothing(t *testing.T) {
	r := exampletest.Start(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "nothing matched; try a broader query")
	assertNoFeedback(t, r)
}

func TestAReferenceOnlyResultRatesNothing(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.Respond("Search", &memoryv1.SearchResponse{Memories: []*memoryv1.MemoryResult{{Idx: "memory-a-1", Reference: "see above"}}})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "nothing to rate")
	assertNoFeedback(t, r)
}

func TestAnEndedContextStopsIt(t *testing.T) {
	r := exampletest.Start(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := run(ctx, r.Options, r.Out); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}

func assertNoFeedback(t *testing.T, r *exampletest.Run) {
	t.Helper()
	for _, call := range r.Server.Memory.Calls() {
		if call == "ShareFeedback" {
			t.Fatal("feedback was sent")
		}
	}
}
