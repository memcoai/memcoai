//go:build systemtest

// Package systemtest runs the SDK against the live service. It is built only
// with -tags systemtest. The memory suite needs MEMCO_API_TOKEN; the
// administration and impersonation suites need an API client's
// MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET instead. Without its credential each
// reports itself skipped. MEMCO_API_HOST names anything but production, and
// MEMCO_API_TLS=false dials a local development server in plaintext.
//
// The organisation may be shared with the other SDKs' suites, running at the
// same time, so everything this suite creates or writes carries the gosys-
// prefix, and it only ever touches, asserts on or removes what it created.
package systemtest

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

const (
	tokenEnv        = "MEMCO_API_TOKEN"
	clientIDEnv     = "MEMCO_CLIENT_ID"
	clientSecretEnv = "MEMCO_CLIENT_SECRET"
)

// connected returns a client connected with the token, closed when t ends.
func connected(t *testing.T) *memcoai.Client {
	t.Helper()
	if os.Getenv(tokenEnv) == "" {
		t.Skip(tokenEnv + " is not set: the system test needs a credential")
	}
	// Passed explicitly: CI sets the API client's variables beside the token,
	// and those win over MEMCO_API_TOKEN for a client given no credential.
	return connect(t, memcoai.Options{Token: os.Getenv(tokenEnv)})
}

// administrator returns a client connected as the API client, closed when t
// ends.
func administrator(t *testing.T) *memcoai.Client {
	t.Helper()
	id, secret := os.Getenv(clientIDEnv), os.Getenv(clientSecretEnv)
	if id == "" || secret == "" {
		t.Skip(clientIDEnv + " and " + clientSecretEnv + " are not both set: administration and impersonation need an API client")
	}
	return connect(t, memcoai.Options{ClientID: id, ClientSecret: secret})
}

func connect(t *testing.T, opts memcoai.Options) *memcoai.Client {
	t.Helper()
	client, err := memcoai.NewClient(opts)
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

// poll calls check until it reports done, pausing firstPoll and then twice as
// long each time up to pollInterval, failing t with gaveUp's message once
// timeout has passed or the test is about to run out of time.
func poll(t *testing.T, timeout time.Duration, waiting string, check func(context.Context) (bool, error), gaveUp func() string) {
	t.Helper()
	limit := time.Now().Add(timeout)
	if deadline, ok := t.Deadline(); ok && deadline.Add(-cleanupMargin).Before(limit) {
		limit = deadline.Add(-cleanupMargin)
	}
	ctx, cancel := context.WithDeadline(t.Context(), limit)
	defer cancel()
	pause := firstPoll
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
		timer := time.NewTimer(pause)
		select {
		case <-ctx.Done():
			timer.Stop()
			t.Fatal(gaveUp())
		case <-timer.C:
		}
		pause = min(2*pause, pollInterval)
	}
}

// runTag names this run: the CI run and attempt, or random bytes locally.
func runTag() string {
	if run := os.Getenv("GITHUB_RUN_ID"); run != "" {
		attempt := os.Getenv("GITHUB_RUN_ATTEMPT")
		if attempt == "" {
			attempt = "1"
		}
		return run + "-" + attempt
	}
	return randomHex(4)
}

func randomHex(n int) string {
	random := make([]byte, n)
	_, _ = rand.Read(random)
	return hex.EncodeToString(random)
}

// unique is a name no other run, no other SDK's suite, and no other object of
// this run carries. In CI it names the run, so an object a failed cleanup left
// behind can be traced to the run that made it.
func unique() string { return "gosys-" + runTag() + "-" + randomHex(3) }

// cleanupContext bounds one cleanup call; t's own context has ended by then.
func cleanupContext() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), time.Minute)
}

// rootNetwork is the root network the suite creates customer networks under:
// the first the service lists. Its domain is the one the administration and
// impersonation tests run in.
func rootNetwork(t *testing.T, admin *memcoai.Client) memcoai.Network {
	t.Helper()
	roots, err := admin.Networks.List(t.Context(), memcoai.ListNetworksParams{ParentID: "root"})
	if err != nil {
		t.Fatal(err)
	}
	if len(roots.Networks) == 0 {
		t.Fatal("the organisation has no root network to create customer networks under")
	}
	return roots.Networks[0]
}

// customerNetwork creates a customer network under root, deleting it when t
// ends. Deleting a network takes what was placed in it too, so this is also
// the safety net for a write an impersonated session could not revert.
func customerNetwork(t *testing.T, admin *memcoai.Client, root memcoai.Network) memcoai.Network {
	t.Helper()
	name := unique()
	// Registered before the create is sent: a create the service commits but
	// the client sees fail still made a network, found here by its name.
	t.Cleanup(func() {
		ctx, cancel := cleanupContext()
		defer cancel()
		found, err := admin.Networks.List(ctx, memcoai.ListNetworksParams{Name: name, ParentID: root.ID})
		if err != nil {
			t.Logf("cleanup: finding network %s: %v", name, err)
			return
		}
		for _, network := range found.Networks {
			// The listing matches names case-insensitively; only this one is ours.
			if network.Name != name {
				continue
			}
			var missing *memcoai.NotFoundError
			if _, err := admin.Networks.Delete(ctx, network.ID); err != nil && !errors.As(err, &missing) {
				t.Logf("cleanup: deleting network %s: %v", name, err)
			}
		}
	})
	network, err := admin.Networks.Create(t.Context(), memcoai.CreateNetworkParams{
		Name: name, ParentID: root.ID, Scope: "customer",
		Description: "A network the Go SDK system test creates and deletes again.",
	})
	if err != nil {
		t.Fatal(err)
	}
	return *network
}

// externalUser creates an external user, a creator unless roles says
// otherwise, deleting it and every key it holds when t ends.
func externalUser(t *testing.T, admin *memcoai.Client, roles ...string) (*memcoai.ExternalUser, error) {
	t.Helper()
	if roles == nil {
		roles = []string{"creator"}
	}
	externalID := unique()
	t.Cleanup(func() {
		ctx, cancel := cleanupContext()
		defer cancel()
		var missing *memcoai.NotFoundError
		if err := admin.Users.Delete(ctx, externalID); err != nil && !errors.As(err, &missing) {
			t.Logf("cleanup: deleting external user %s: %v", externalID, err)
		}
	})
	return admin.Users.Create(t.Context(), externalID, memcoai.CreateUserParams{Roles: roles})
}

// placed is a new creator placed in a customer network of its own. Its
// external id doubles as the marker of what it writes: nothing else carries
// it.
func placed(t *testing.T, admin *memcoai.Client, root memcoai.Network) (network memcoai.Network, user *memcoai.ExternalUser) {
	t.Helper()
	network = customerNetwork(t, admin, root)
	user, err := externalUser(t, admin)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := admin.Networks.AddMember(t.Context(), network.ID, user.ID, memcoai.AddMemberParams{}); err != nil {
		t.Fatal(err)
	}
	return network, user
}
