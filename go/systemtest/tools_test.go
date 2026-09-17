//go:build systemtest

package systemtest

import (
	"slices"
	"testing"

	"github.com/memcoai/memcoai/go/internal/memory"
)

// A tool reported unavailable here, with a creator credential, would mean the
// filtering behind Session.Tools misreads the service's own answer: the class
// of bug the fake server cannot catch, since it never talks to the real
// authorization stack.
func TestACreatorCredentialIsOfferedEveryTool(t *testing.T) {
	var want []string
	for _, name := range memory.OfferedTools {
		want = append(want, memory.ToolPrefix+name)
	}
	slices.Sort(want)

	for _, domain := range domains(t) {
		t.Run(domain, func(t *testing.T) {
			client := connected(t)
			catalog, err := client.Memory.ListTools(t.Context())
			if err != nil {
				t.Fatal(err)
			}
			if len(catalog) == 0 {
				t.Fatal("the service returned no tools")
			}
			var unavailable []string
			for _, tool := range catalog {
				if !tool.Available {
					unavailable = append(unavailable, tool.Name)
				}
			}
			if len(unavailable) > 0 {
				t.Fatalf("reported unavailable to this creator credential: %v", unavailable)
			}

			session, err := client.Memory.StartSession(t.Context(), domain)
			if err != nil {
				t.Fatal(err)
			}
			var offered []string
			for _, tool := range session.Tools().Tools {
				offered = append(offered, tool.Name)
			}
			slices.Sort(offered)
			if !slices.Equal(offered, want) {
				t.Fatalf("offered %v, want every tool: %v", offered, want)
			}
		})
	}
}
