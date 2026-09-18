package main

import (
	"context"
	"errors"
	"testing"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/memcoai"
)

func TestItListsDomainsAndSearchesTheFirst(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Domains: []*memoryv1.DomainEntry{
		{Slug: "coding", Summary: "How we build things.\nMore detail."},
		{Slug: "support", Summary: "Customer answers."},
	}})
	r.Server.Memory.Respond("Search", &memoryv1.SearchResponse{Memories: []*memoryv1.MemoryResult{{
		Idx: "memory-a-1", TimesServed: 3,
		Insights: []*memoryv1.InsightResult{{Idx: "memory-a-1-insight-1", Title: "Use a Bearer token", Updated: "2026-09-01"}},
	}}})
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t,
		"2 domain(s) available:",
		"  coding       How we build things.\n",
		"  support      Customer answers.",
		"1 memories for that query:",
		"  memory-a-1  served 3x",
		"    2026-09-01  Use a Bearer token",
	)
	sent := r.Server.Memory.Request("Search").(*memoryv1.SearchRequest)
	if sent.GetDomain() != "coding" {
		t.Fatalf("searched %q", sent.GetDomain())
	}
}

func TestACredentialWithNoDomainsSearchesNothing(t *testing.T) {
	r := exampletest.Start(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "0 domain(s) available:", "this credential reaches no domains")
	if calls := r.Server.Memory.Calls(); len(calls) != 2 {
		t.Fatalf("calls %v", calls)
	}
}

func TestAFailureIsReturned(t *testing.T) {
	r := exampletest.Start(t)
	r.Options.Token = " "
	var config *memcoai.ConfigError
	if err := run(context.Background(), r.Options, r.Out); !errors.As(err, &config) {
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
	if calls := r.Server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}
