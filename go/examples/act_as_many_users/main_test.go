package main

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
)

func TestEachUserSearchesUnderAKeyOfTheirOwn(t *testing.T) {
	r := exampletest.StartAPIClient(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "-alice sees 0 memories", "-bob sees 0 memories", "-carol sees 0 memories", "removed again")
	var searchers []string
	metadata := r.Server.Memory.Metadata()
	for i, call := range r.Server.Memory.Calls() {
		if call != "Search" {
			continue
		}
		bearer := strings.Join(metadata[i].Get("authorization"), ",")
		for _, handle := range handles {
			if strings.HasPrefix(bearer, "Bearer impersonation-goex-") && strings.Contains(bearer, "-"+handle+"-") {
				searchers = append(searchers, handle)
			}
		}
	}
	slices.Sort(searchers)
	if !slices.Equal(searchers, handles) {
		t.Fatalf("searched as %v", searchers)
	}
}
