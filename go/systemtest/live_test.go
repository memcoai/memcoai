//go:build systemtest

// Package systemtest runs the SDK against the live service. It is built only
// with -tags systemtest, and needs MEMCO_API_TOKEN (plus MEMCO_API_HOST for
// anything but production).
package systemtest

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

const tokenEnv = "MEMCO_API_TOKEN"

// connected returns a connected client, closed when t ends.
func connected(t *testing.T) *memcoai.Client {
	t.Helper()
	if os.Getenv(tokenEnv) == "" {
		t.Skip(tokenEnv + " is not set: the system test needs a credential")
	}
	client, err := memcoai.NewClient(memcoai.Options{})
	if err != nil {
		t.Fatal(err)
	}
	// Connect leaves the channel open when it fails, so the close is
	// registered first.
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		if err := client.Close(ctx); err != nil {
			t.Errorf("closing the client: %v", err)
		}
	})
	if err := client.Connect(t.Context()); err != nil {
		t.Fatal(err)
	}
	return client
}

// domains returns every domain the credential reaches, failing t if there are
// none.
func domains(t *testing.T) []string {
	t.Helper()
	listed, err := connected(t).Memory.ListDomains(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(listed.Domains) == 0 {
		t.Fatal("the service returned no domains")
	}
	slugs := make([]string, 0, len(listed.Domains))
	for _, entry := range listed.Domains {
		slugs = append(slugs, entry.Slug)
	}
	return slugs
}

// cleanupMargin is left before go test's own -timeout. A test that runs into
// that timeout panics without running its cleanups, which would strand a probe
// in a live domain; one that runs into this margin fails and cleans up.
const cleanupMargin = 2 * time.Minute

// poll calls check every pollInterval until it reports done, failing t with
// gaveUp's message once timeout has passed or the test is about to run out of
// time.
func poll(t *testing.T, timeout time.Duration, waiting string, check func(context.Context) (bool, error), gaveUp func() string) {
	t.Helper()
	limit := time.Now().Add(timeout)
	if deadline, ok := t.Deadline(); ok && deadline.Add(-cleanupMargin).Before(limit) {
		limit = deadline.Add(-cleanupMargin)
	}
	ctx, cancel := context.WithDeadline(t.Context(), limit)
	defer cancel()
	for {
		done, err := check(ctx)
		switch {
		case done:
			return
		case ctx.Err() != nil:
			t.Fatal(gaveUp())
		case err != nil:
			t.Fatal(err)
		}
		t.Log(waiting)
		timer := time.NewTimer(pollInterval)
		select {
		case <-ctx.Done():
			timer.Stop()
			t.Fatal(gaveUp())
		case <-timer.C:
		}
	}
}
