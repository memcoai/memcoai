// Package exampletest runs an example against the fake service.
package exampletest

import (
	"bytes"
	"log"
	"strings"
	"testing"

	"github.com/memcoai/memcoai/go/internal/testserver"
	"github.com/memcoai/memcoai/go/memcoai"
)

// Run is one example run: the fake it talks to, the options that reach it, and
// what it printed.
type Run struct {
	Server  *testserver.Harness
	Options memcoai.Options
	Out     *log.Logger

	printed bytes.Buffer
}

// Start starts a fake service for t.
func Start(t *testing.T) *Run {
	t.Helper()
	server := testserver.Start(t)
	plaintext := false
	r := &Run{
		Server:  server,
		Options: memcoai.Options{Token: "test-token", Host: server.Address, TLS: &plaintext, LogLevel: "none"},
	}
	r.Out = log.New(&r.printed, "", 0)
	return r
}

// StartAPIClient starts a fake service for t, reached with an API client's
// credentials rather than a token.
func StartAPIClient(t *testing.T) *Run {
	t.Helper()
	r := Start(t)
	r.Options.Token, r.Options.ClientID, r.Options.ClientSecret = "", "test-client", "test-secret"
	return r
}

// Printed returns what the example printed.
func (r *Run) Printed() string { return r.printed.String() }

// Expect fails t unless the example printed every line of want, in order.
func (r *Run) Expect(t *testing.T, want ...string) {
	t.Helper()
	rest := r.Printed()
	for _, line := range want {
		at := strings.Index(rest, line)
		if at < 0 {
			t.Fatalf("missing %q in order; printed:\n%s", line, r.Printed())
		}
		rest = rest[at+len(line):]
	}
}
