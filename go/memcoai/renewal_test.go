package memcoai

import (
	"context"
	"encoding/xml"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"

	"github.com/memcoai/memcoai/go/internal/testserver"
)

// B: past the renewal point the current key still serves, so no call waits
// for the renewal, however slow it is.
func TestPastTheRenewalPointCallsDoNotWaitForTheRenewal(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	mint := f.server.Admin.Hold("ImpersonateExternalUser")
	clock.Advance(510 * time.Second) // 0.85
	for range 3 {
		searching, cancel := context.WithTimeout(ctx, time.Second)
		_, err := session.Search(searching, "q", ScopedSearchParams{})
		cancel()
		if err != nil {
			t.Fatalf("a call waited on the renewal: %v", err)
		}
	}
	<-mint.Arrived()
	if got := bearers(f.server.Memory.Metadata()); got[len(got)-1] != keyOne {
		t.Fatalf("authorization %q", got)
	}
	mint.Release()
	serving(t, "key-2", f.server.Memory.Metadata, func() error {
		_, err := session.Search(ctx, "q", ScopedSearchParams{})
		return err
	}, "Bearer impersonation-customer-42-2")
	if n := strings.Count(strings.Join(f.server.Admin.Calls(), " "), "ImpersonateExternalUser"); n != 2 {
		t.Fatalf("%d mints for one renewal", n)
	}
}

// C: the old key is ended after the new one serves, beside the calls, once,
// and a close waits for that end.
func TestTheOldKeysEndHoldsNoCallAndIsCountedForClose(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	f.server.Admin.Delay("EndImpersonation", 500*time.Millisecond)
	clock.Advance(510 * time.Second)
	serving(t, "key-2", f.server.Memory.Metadata, func() error {
		started := time.Now()
		_, err := session.Search(ctx, "q", ScopedSearchParams{})
		if elapsed := time.Since(started); elapsed > 400*time.Millisecond {
			t.Errorf("a call took %v, waiting on an end", elapsed)
		}
		return err
	}, "Bearer impersonation-customer-42-2")
	eventually(t, "key-1's end to be sent", func() bool { return slices.Contains(f.server.Admin.Calls(), "EndImpersonation") })
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1", "key-2"}) || len(f.server.Admin.Unended()) != 0 {
		t.Fatalf("ended %v; unended %v", got, f.server.Admin.Unended())
	}
}

// D: an impersonated open keeps to its caller's deadline, whether its mint or
// the end of a key it gives up on is slow; that key is still ended.
func TestAnImpersonatedOpenKeepsToItsDeadline(t *testing.T) {
	for _, slow := range []string{"ImpersonateExternalUser", "StartSession"} {
		t.Run(slow, func(t *testing.T) {
			f := credentialed(t)
			if slow == "StartSession" {
				f.server.Memory.Delay("StartSession", time.Second)
			} else {
				f.server.Admin.Delay("ImpersonateExternalUser", time.Second)
			}
			f.server.Admin.Delay("EndImpersonation", time.Second)
			opening, cancel := context.WithTimeout(ctx, 300*time.Millisecond)
			defer cancel()
			started := time.Now()
			_, err := f.client.Memory.StartSession(opening, "coding", ExternalID("customer-42"))
			expect[*TimeoutError](t, err)
			if elapsed := time.Since(started); elapsed > 700*time.Millisecond {
				t.Fatalf("the open returned after %v, past its 300ms deadline", elapsed)
			}
			eventually(t, "key-1 to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
		})
	}
}

// 1: a call made without a deadline gets Options.Timeout for all of it,
// waiting on a renewal included.
func TestACallWithoutADeadlineWaitsForARenewalNoLongerThanTheTimeout(t *testing.T) {
	const timeout = 300 * time.Millisecond
	f, clock := timed(t, func(o *Options) { o.Timeout = timeout })
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42") // key-1
	defer session.Close(ctx)
	clock.Advance(5 * time.Minute)
	// A second key for the user whose end fails: the renewal of key-1 ends it
	// first, which takes a timeout of its own.
	other := actingAs(t, f, "customer-42")
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.Unavailable, Details: "busy"})
	other.Close(ctx)
	f.server.Admin.Delay("EndImpersonation", 5*time.Second)
	f.server.Admin.Delay("ImpersonateExternalUser", 5*time.Second)
	clock.Advance(5 * time.Minute) // key-1 has expired, so the call must wait
	started := time.Now()
	_, err := session.Search(context.Background(), "q", ScopedSearchParams{})
	elapsed := time.Since(started)
	expect[*TimeoutError](t, err)
	if elapsed > timeout+200*time.Millisecond {
		t.Fatalf("a call bounded by Options.Timeout=%v took %v", timeout, elapsed)
	}
}

// 2: a close that stops waiting before a mint lands still leaves that key
// ended rather than live against its user's cap.
func TestAKeyMintedAfterTheCloseSweptIsEnded(t *testing.T) {
	f := credentialed(t)
	open := actingAs(t, f, "customer-1") // left open, for the sweep to end
	f.server.Admin.Delay("EndImpersonation", 400*time.Millisecond)
	hold := f.server.Admin.Hold("ImpersonateExternalUser")
	opened := make(chan error, 1)
	go func() {
		_, err := f.client.Memory.StartSession(context.Background(), "coding", ExternalID("customer-2"))
		opened <- err
	}()
	<-hold.Arrived()
	closing, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()
	closed := make(chan error, 1)
	go func() { closed <- f.client.Close(closing) }()
	time.Sleep(150 * time.Millisecond) // Close stopped waiting; its sweep is ending key-1
	f.server.Admin.Delay("EndImpersonation", 0)
	hold.Release() // key-2 lands after the sweep
	expect[*TimeoutError](t, <-closed)
	expect[*ConfigError](t, <-opened)
	eventually(t, "key-2 to be ended", func() bool { return len(f.server.Admin.Unended()) == 0 })
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1", "key-2"}) {
		t.Fatalf("ended %v", got)
	}
	_ = open
}

// 3: encoding/xml, and fmt's handling of a misused verb, bypass the methods
// that redact; neither may print a key's value.
func TestACreatedKeysValueSurvivesNoEncodingOrMisusedVerb(t *testing.T) {
	f := credentialed(t)
	created, err := f.client.Users.CreateKey(ctx, "customer-7", CreateKeyParams{Preset: "mcp_ro"})
	if err != nil {
		t.Fatal(err)
	}
	if created.Value() != "mk_live_new-key-value" {
		t.Fatalf("control: the value is %q", created.Value())
	}
	options := Options{Token: "sk-live-secret", ClientSecret: "cs-live-secret"}
	var rendered []string
	for _, v := range []any{created, *created, options, &options} {
		encoded, err := xml.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		rendered = append(rendered, string(encoded))
	}
	// The verb is built at run time: go vet, rightly, refuses it as written.
	misused := "%" + "w"
	rendered = append(rendered,
		fmt.Sprintf(misused, created), fmt.Sprintf(misused, *created), fmt.Sprintf(misused, []CreatedKey{*created}))
	for _, text := range rendered {
		if strings.Contains(text, "new-key-value") || strings.Contains(text, "live-secret") {
			t.Errorf("a secret is visible in %s", text)
		}
	}
	if !strings.Contains(rendered[0], "mk_live_ne") {
		t.Fatalf("control: the prefix was not encoded: %s", rendered[0])
	}
}

// A session closed while one call holds its key and a renewal is minting the
// next: neither key is ended under its use, the renewal's key is ended as it
// lands, and each key is ended once.
func TestASessionClosedDuringACallAndARenewalEndsEachKeyOnce(t *testing.T) {
	for range 10 {
		f, clock := timed(t)
		f.server.Admin.SetKeyLifetime(10 * time.Minute)
		session := actingAs(t, f, "customer-42")
		search := f.server.Memory.Hold("Search")
		searched := make(chan error, 1)
		go func() { _, err := session.Search(ctx, "q", ScopedSearchParams{}); searched <- err }()
		<-search.Arrived()
		clock.Advance(9 * time.Minute)
		mint := f.server.Admin.Hold("ImpersonateExternalUser")
		// Served by key-1, which is due; the renewal starts beside it.
		if _, err := session.GetMemory(ctx, "m"); err != nil {
			t.Fatal(err)
		}
		<-mint.Arrived()
		session.Close(ctx)
		if got := ended(f); len(got) != 0 {
			t.Fatalf("ended %v while key-1 is in use and key-2 minting", got)
		}
		mint.Release()
		eventually(t, "key-2 to be ended", func() bool { return slices.Contains(ended(f), "key-2") })
		if slices.Contains(ended(f), "key-1") {
			t.Fatal("key-1 was ended under its search")
		}
		search.Release()
		if err := <-searched; err != nil {
			t.Fatal(err)
		}
		eventually(t, "key-1 to be ended", func() bool { return len(ended(f)) == 2 })
		if err := f.client.Close(ctx); err != nil {
			t.Fatal(err)
		}
		if got := ended(f); len(got) != 2 || got[0] == got[1] || len(f.server.Admin.Unended()) != 0 {
			t.Fatalf("ended %v, unended %v", got, f.server.Admin.Unended())
		}
	}
}

// expired is a credentialed session whose key has just expired, so its next
// call must wait for a new one.
func expired(t *testing.T, edits ...func(*Options)) (*fixture, *Session) {
	t.Helper()
	f, clock := timed(t, edits...)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	t.Cleanup(func() { session.Close(ctx) })
	clock.Advance(10 * time.Minute)
	f.server.Forget()
	return f, session
}

// within fails t unless elapsed is between from and to of budget.
func within(t *testing.T, elapsed, budget time.Duration, from, to float64) {
	t.Helper()
	if elapsed < time.Duration(from*float64(budget)) || elapsed > time.Duration(to*float64(budget)) {
		t.Fatalf("took %v of a %v budget, want %.2f-%.2f of it", elapsed, budget, from, to)
	}
}

const budget = time.Second

// A call has one deadline, which covers waiting for a credential and the call
// itself: a mint that never lands times out then, and the call is never sent.
func TestWaitingForACredentialIsBoundedByTheCallsDeadline(t *testing.T) {
	f, session := expired(t)
	t.Cleanup(f.server.Admin.Hold("ImpersonateExternalUser").Release)
	calling, cancel := context.WithTimeout(ctx, budget)
	defer cancel()
	started := time.Now()
	_, err := session.Search(calling, "q", ScopedSearchParams{})
	expect[*TimeoutError](t, err)
	within(t, time.Since(started), budget, 0.95, 1.3)
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("sent %v", calls)
	}
}

// The call gets only what the wait left of its deadline.
func TestTheCallGetsWhatTheWaitLeftOfItsDeadline(t *testing.T) {
	f, session := expired(t)
	f.server.Admin.Delay("ImpersonateExternalUser", budget*3/10)
	t.Cleanup(f.server.Memory.Hold("Search").Release)
	calling, cancel := context.WithTimeout(ctx, budget)
	defer cancel()
	started := time.Now()
	_, err := session.Search(calling, "q", ScopedSearchParams{})
	expect[*TimeoutError](t, err)
	within(t, time.Since(started), budget, 0.95, 1.3)
	if got := bearers(f.server.Memory.Metadata()); !reflect.DeepEqual(got, []string{"Bearer impersonation-customer-42-2"}) {
		t.Fatalf("authorization %q", got)
	}
}

// A call that needs no wait, the key still serving even past its renewal
// point, has the whole of its deadline.
func TestACallWithAServingCredentialHasItsWholeDeadline(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	f.server.Memory.Delay("Search", budget*7/10)
	for _, advance := range []time.Duration{0, 510 * time.Second} { // fresh, then due
		clock.Advance(advance)
		t.Cleanup(f.server.Admin.Hold("ImpersonateExternalUser").Release)
		calling, cancel := context.WithTimeout(ctx, budget)
		_, err := session.Search(calling, "q", ScopedSearchParams{})
		cancel()
		if err != nil {
			t.Fatalf("after %v: %v", advance, err)
		}
	}
}

// Without a deadline of its own a call gets Options.Timeout from its start,
// shared the same way.
func TestWithoutADeadlineTheTimeoutIsSharedTheSameWay(t *testing.T) {
	f, session := expired(t, func(o *Options) { o.Timeout = budget })
	t.Cleanup(f.server.Admin.Hold("ImpersonateExternalUser").Release)
	started := time.Now()
	_, err := session.Search(context.Background(), "q", ScopedSearchParams{})
	expect[*TimeoutError](t, err)
	within(t, time.Since(started), budget, 0.95, 1.3)

	f, session = expired(t, func(o *Options) { o.Timeout = budget })
	f.server.Admin.Delay("ImpersonateExternalUser", budget*3/10)
	t.Cleanup(f.server.Memory.Hold("Search").Release)
	started = time.Now()
	_, err = session.Search(context.Background(), "q", ScopedSearchParams{})
	expect[*TimeoutError](t, err)
	within(t, time.Since(started), budget, 0.95, 1.3)

	f, clock := timed(t, func(o *Options) { o.Timeout = budget })
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	serving := actingAs(t, f, "customer-42")
	defer serving.Close(ctx)
	clock.Advance(510 * time.Second)
	t.Cleanup(f.server.Admin.Hold("ImpersonateExternalUser").Release)
	f.server.Memory.Delay("Search", budget*7/10)
	if _, err := serving.Search(context.Background(), "q", ScopedSearchParams{}); err != nil {
		t.Fatal(err)
	}
}

// skewed is a credentialed client whose clock runs ahead of the service's by
// ahead, with keys that live an hour.
func skewed(t *testing.T, ahead time.Duration) *fixture {
	t.Helper()
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(time.Hour)
	f.server.Admin.SetClock(func() time.Time { return clock.Now().Add(-ahead) })
	f.server.Admin.SetKeyCap(5)
	return f
}

// A client whose clock runs ahead of the service's by more than a key's
// lifetime reads every key minted for it as expired already. That is said,
// once, rather than every call minting another key it never ends until the
// user's cap refuses them all.
func TestAKeyAlreadyExpiredByThisMachinesClockIsAnErrorNamingTheClock(t *testing.T) {
	f := skewed(t, 2*time.Hour)
	for range 3 {
		_, err := f.client.Memory.StartSession(ctx, "coding", ExternalID("customer-42"))
		got := as[*ConfigError](t, err)
		if !strings.Contains(got.Message, "already expired by this machine's clock") ||
			strings.Contains(got.Message, "impersonation-customer") {
			t.Fatalf("got %q", got.Message)
		}
	}
	mints := strings.Count(strings.Join(f.server.Admin.Calls(), " "), "ImpersonateExternalUser")
	if mints != 3 || len(f.server.Memory.Calls()) != 0 {
		t.Fatalf("%d mints; memory calls %v", mints, f.server.Memory.Calls())
	}
	// Each key is live at the service, whose clock is behind, so each is ended.
	eventually(t, "every key to be ended", func() bool { return len(f.server.Admin.Unended()) == 0 })
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1", "key-2", "key-3"}) {
		t.Fatalf("ended %v", got)
	}
}

// Skew shorter than a key's lifetime only shortens it on this clock.
func TestAClockAheadByLessThanAKeysLifetimeStillWorks(t *testing.T) {
	f := skewed(t, 30*time.Minute)
	session := actingAs(t, f, "customer-42")
	for range 5 {
		if _, err := session.Search(ctx, "q", ScopedSearchParams{}); err != nil {
			t.Fatal(err)
		}
	}
	session.Close(ctx)
	if mints := strings.Count(strings.Join(f.server.Admin.Calls(), " "), "ImpersonateExternalUser"); mints != 1 {
		t.Fatalf("%d mints", mints)
	}
	if len(f.server.Admin.Unended()) != 0 {
		t.Fatalf("left live: %v", f.server.Admin.Unended())
	}
}

// A service that says how many seconds a key has left is timed on this
// machine's clock alone, so a clock hours ahead of the service's changes
// nothing: the key serves, and is renewed at four fifths of what it had left.
func TestAKeysSecondsLeftTimeItWhateverThisMachinesClockSays(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(time.Hour)
	f.server.Admin.SetClock(func() time.Time { return clock.Now().Add(-2 * time.Hour) })
	f.server.Admin.SendExpiresIn(true)
	session := actingAs(t, f, "customer-42")
	search := func() error { _, err := session.Search(ctx, "q", ScopedSearchParams{}); return err }
	for range 3 {
		if err := search(); err != nil {
			t.Fatal(err)
		}
	}
	clock.Advance(48*time.Minute - time.Second)
	if err := search(); err != nil {
		t.Fatal(err)
	}
	if mints := strings.Count(strings.Join(f.server.Admin.Calls(), " "), "ImpersonateExternalUser"); mints != 1 {
		t.Fatalf("renewed early: %d mints", mints)
	}
	clock.Advance(time.Second) // 0.8 of the hour
	serving(t, "key-2", f.server.Memory.Metadata, search, "Bearer impersonation-customer-42-2")
	eventually(t, "key-1 to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
	session.Close(ctx)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1", "key-2"}) || len(f.server.Admin.Unended()) != 0 {
		t.Fatalf("ended %v; unended %v", got, f.server.Admin.Unended())
	}
}
