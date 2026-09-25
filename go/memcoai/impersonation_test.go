package memcoai

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"runtime"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/proto"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
	"github.com/memcoai/memcoai/go/internal/transport"
)

const keyOne = "Bearer impersonation-customer-42-1"

// actingAs opens a session acting as externalID.
func actingAs(t *testing.T, f *fixture, externalID string) *Session {
	t.Helper()
	session, err := f.client.Memory.StartSession(ctx, "coding", ExternalID(externalID))
	if err != nil {
		t.Fatalf("StartSession: %v", err)
	}
	return session
}

// warned reports whether ending a key failed and said so.
func warned(f *fixture) bool { return f.logs.Has("could not end impersonation") }

// ended returns the key ids EndImpersonation was sent, in order.
func ended(f *fixture) []string {
	var ids []string
	for _, request := range f.server.Admin.Received() {
		if end, ok := request.(*adminv1.EndImpersonationRequest); ok {
			ids = append(ids, end.GetKeyId())
		}
	}
	return ids
}

func TestOpeningImpersonatesThenListsDomainsThenStartsUnderTheKey(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	calls := f.server.Calls()
	// ListTools runs beside StartSession, so only its presence is fixed.
	if len(calls) != 4 || !reflect.DeepEqual(calls[:2], []string{"ImpersonateExternalUser", "ListDomains"}) ||
		!slices.Contains(calls[2:], "StartSession") || !slices.Contains(calls[2:], "ListTools") {
		t.Fatalf("calls %v", calls)
	}
	if sent := f.server.Admin.Request("ImpersonateExternalUser"); !proto.Equal(sent, &adminv1.ImpersonateExternalUserRequest{ExternalId: "customer-42"}) {
		t.Fatalf("sent %v", sent)
	}
	if got := bearers(f.server.Admin.Metadata()); !reflect.DeepEqual(got, []string{"Bearer client-token-1"}) {
		t.Fatalf("the key was minted under %q", got)
	}
	if got := bearers(f.server.Memory.Metadata()); !reflect.DeepEqual(got, []string{keyOne, keyOne, keyOne}) {
		t.Fatalf("the session opened under %q", got)
	}
}

func TestTheSessionLearnsItsLimitsUnderItsOwnKey(t *testing.T) {
	f := credentialed(t)
	f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Limits: &memoryv1.Limits{MaxQueryCharacters: 10}})
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	f.server.Forget()
	_, err := session.Search(ctx, "a query longer than ten", ScopedSearchParams{})
	invalid(t, err, "query is 23 characters, which exceeds the limit of 10")
	if calls := f.server.Calls(); len(calls) != 0 {
		t.Fatalf("sent %v", calls)
	}
}

func TestEveryCallThroughTheSessionCarriesTheKey(t *testing.T) {
	f := credentialed(t)
	f.server.Memory.Respond("ShareFeedback", &memoryv1.ShareFeedbackResponse{
		Entries: []*memoryv1.FeedbackEntry{{Idx: "memory-a", Relevant: true}},
	})
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	f.server.Forget()
	calls := []func() error{
		func() error { _, err := session.Search(ctx, "q", ScopedSearchParams{}); return err },
		func() error {
			memory, err := session.GetMemory(ctx, "memory-a")
			if err != nil {
				return err
			}
			_, err = memory.Feedback(ctx, MemoryFeedback{Relevant: true, Correct: true})
			return err
		},
		func() error {
			_, err := session.CreateMemory(ctx, ScopedCreateMemoryParams{Query: "q", Title: "t", Content: "c"})
			return err
		},
		func() error {
			_, err := session.EnrichMemory(ctx, ScopedEnrichMemoryParams{MemoryIdx: NewMemory, Title: "t", Content: "c"})
			return err
		},
		func() error {
			_, err := session.ShareFeedback(ctx, []FeedbackRating{{Idx: "memory-a", Relevant: true}})
			return err
		},
		func() error { _, err := session.RevertMemory(ctx, "create-a"); return err },
		func() error {
			_, err := session.ImportMemories(ctx, []ImportedMemory{{
				Queries: []string{"q"}, Insights: []ImportedInsight{{Title: "t", Content: "c"}},
			}})
			return err
		},
		func() error {
			_, err := session.Tools().Call(ctx, "memco_search", json.RawMessage(`{"query":"q"}`))
			return err
		},
	}
	for _, call := range calls {
		if err := call(); err != nil {
			t.Fatal(err)
		}
	}
	got := bearers(f.server.Memory.Metadata())
	if len(got) != 9 {
		t.Fatalf("control: %d calls, %v", len(got), f.server.Memory.Calls())
	}
	for i, bearer := range got {
		if bearer != keyOne {
			t.Errorf("%s carried %q", f.server.Memory.Calls()[i], bearer)
		}
	}
	if calls := f.server.Admin.Calls(); len(calls) != 0 {
		t.Fatalf("admin calls %v", calls)
	}
}

func TestCallsOnTheClientKeepTheClientsOwnCredential(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	f.server.Forget()
	if _, err := f.client.Memory.ListTools(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.Networks.List(ctx, ListNetworksParams{}); err != nil {
		t.Fatal(err)
	}
	for _, got := range [][]string{bearers(f.server.Memory.Metadata()), bearers(f.server.Admin.Metadata())} {
		if !reflect.DeepEqual(got, []string{"Bearer client-token-1"}) {
			t.Fatalf("authorization %q", got)
		}
	}
}

func TestATokenClientMintsUnderItsTokenAndKeepsIt(t *testing.T) {
	f := connected(t)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	if _, err := f.client.Memory.ListTools(ctx); err != nil {
		t.Fatal(err)
	}
	if got := bearers(f.server.Admin.Metadata()); !reflect.DeepEqual(got, []string{"Bearer " + testToken}) {
		t.Fatalf("minted under %q", got)
	}
	if got := bearers(f.server.Memory.Metadata()); len(got) < 2 || got[len(got)-1] != "Bearer "+testToken || got[0] != keyOne {
		t.Fatalf("authorization %q", got)
	}
}

func TestAKeyNeverReachesALogOrAnError(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	f.server.Memory.FailNext("Search", testserver.Failure{Code: codes.Unauthenticated, Details: "key rejected"})
	_, err := session.Search(ctx, "q", ScopedSearchParams{})
	expect[*AuthenticationError](t, err)
	session.Close(ctx)
	if !f.logs.Has("rpc=ImpersonateExternalUser") {
		t.Fatalf("control: nothing was logged at debug: %s", f.logs)
	}
	for _, text := range []string{f.logs.String(), err.Error(), fmt.Sprintf("%+v %#v", err, err)} {
		if strings.Contains(text, "impersonation-customer") {
			t.Errorf("a key is visible in %q", text)
		}
	}
}

func TestABlankExternalIDOrDomainSendsNothing(t *testing.T) {
	f := credentialed(t)
	_, err := f.client.Memory.StartSession(ctx, "coding", ExternalID(" "))
	invalid(t, err, "external_id must not be empty")
	_, err = f.client.Memory.StartSession(ctx, "\t", ExternalID("customer-42"))
	invalid(t, err, "domain must not be empty")
	if calls := f.server.Calls(); len(calls) != 0 {
		t.Fatalf("sent %v", calls)
	}
}

func TestARefusedImpersonationOpensNothing(t *testing.T) {
	f := credentialed(t)
	f.server.Admin.FailNext("ImpersonateExternalUser", testserver.Failure{Code: codes.NotFound, Details: "no such user"})
	_, err := f.client.Memory.StartSession(ctx, "coding", ExternalID("customer-42"))
	if got := as[*NotFoundError](t, err); got.Detail != "no such user" {
		t.Fatalf("got %+v", got)
	}
	if calls := f.server.Calls(); !reflect.DeepEqual(calls, []string{"ImpersonateExternalUser"}) {
		t.Fatalf("calls %v", calls)
	}
}

func TestAFailureAfterTheMintEndsTheKey(t *testing.T) {
	for _, failing := range []string{"ListDomains", "StartSession"} {
		t.Run(failing, func(t *testing.T) {
			f := credentialed(t)
			f.server.Memory.FailNext(failing, testserver.Failure{Code: codes.PermissionDenied, Details: "not here"})
			_, err := f.client.Memory.StartSession(ctx, "coding", ExternalID("customer-42"))
			expect[*PermissionError](t, err)
			eventually(t, "key-1 to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
		})
	}
}

func TestClosingTheSessionEndsTheKeyOnce(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	session.Close(ctx)
	session.Close(ctx)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("ended %v", got)
	}
	sent := f.server.Admin.Request("EndImpersonation")
	if !proto.Equal(sent, &adminv1.EndImpersonationRequest{ExternalId: "customer-42", KeyId: "key-1"}) {
		t.Fatalf("sent %v", sent)
	}
	if got := bearers(f.server.Admin.Metadata()); len(got) == 0 || got[len(got)-1] != "Bearer client-token-1" {
		t.Fatalf("ended under %q", got)
	}
}

func TestAClosedSessionRefusesEveryCallLocally(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	memory, err := session.GetMemory(ctx, "memory-a")
	if err != nil {
		t.Fatal(err)
	}
	session.Close(ctx)
	f.server.Forget()
	calls := []func() error{
		func() error { _, err := session.Search(ctx, "q", ScopedSearchParams{}); return err },
		func() error { _, err := session.RevertMemory(ctx, "create-a"); return err },
		func() error { _, err := memory.Feedback(ctx, MemoryFeedback{Relevant: true}); return err },
		func() error {
			_, err := session.Tools().Call(ctx, "memco_search", json.RawMessage(`{"query":"q"}`))
			return err
		},
	}
	for _, call := range calls {
		if got := as[*ConfigError](t, call()); got.Message != transport.ClosedSession {
			t.Errorf("got %q", got.Message)
		}
	}
	if calls := f.server.Calls(); len(calls) != 0 {
		t.Fatalf("sent %v", calls)
	}
}

func TestClosingAPlainSessionChangesNothing(t *testing.T) {
	f := connected(t)
	session, err := f.client.Memory.StartSession(ctx, "coding")
	if err != nil {
		t.Fatal(err)
	}
	session.Close(ctx)
	if _, err := session.Search(ctx, "q", ScopedSearchParams{}); err != nil {
		t.Fatal(err)
	}
	if calls := f.server.Admin.Calls(); len(calls) != 0 {
		t.Fatalf("admin calls %v", calls)
	}
}

func TestADueKeyIsRenewedAndTheOldOneEnded(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	f.server.Forget()
	search := func() {
		t.Helper()
		if _, err := session.Search(ctx, "q", ScopedSearchParams{}); err != nil {
			t.Fatal(err)
		}
	}
	search()
	clock.Advance(8*time.Minute - time.Second)
	search()
	if calls := f.server.Admin.Calls(); len(calls) != 0 {
		t.Fatalf("renewed early: %v", calls)
	}
	clock.Advance(time.Second)
	// Due, and still serving while the renewal runs beside the calls.
	search()
	serving(t, "key-2", f.server.Memory.Metadata, func() error {
		_, err := session.Search(ctx, "q", ScopedSearchParams{})
		return err
	}, "Bearer impersonation-customer-42-2")
	if got := bearers(f.server.Memory.Metadata()); !reflect.DeepEqual(got[:3], []string{keyOne, keyOne, keyOne}) {
		t.Fatalf("authorization %q", got)
	}
	eventually(t, "key-1 to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
	if calls := f.server.Admin.Calls(); !reflect.DeepEqual(calls, []string{"ImpersonateExternalUser", "EndImpersonation"}) {
		t.Fatalf("admin calls %v", calls)
	}
}

func TestRenewalNeverEndsAKeyUnderACallStillUsingIt(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	f.server.Forget()
	hold := f.server.Memory.Hold("Search")
	searched := make(chan error, 1)
	go func() {
		_, err := session.Search(ctx, "q", ScopedSearchParams{})
		searched <- err
	}()
	<-hold.Arrived()
	clock.Advance(8 * time.Minute)
	serving(t, "key-2", f.server.Memory.Metadata, func() error {
		_, err := session.GetMemory(ctx, "memory-a")
		return err
	}, "Bearer impersonation-customer-42-2")
	if got := ended(f); len(got) != 0 {
		t.Fatalf("ended %v under a call still using it", got)
	}
	hold.Release()
	if err := <-searched; err != nil {
		t.Fatal(err)
	}
	eventually(t, "key-1 to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
}

func TestAFailedEndIsLoggedAndRetriedWhenTheClientCloses(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.Unavailable, Details: "busy"})
	session.Close(ctx)
	if !f.logs.Has(`level=WARN msg="`+endFailed+`"`) || !f.logs.Has("key_id=key-1") {
		t.Fatalf("logs %s", f.logs)
	}
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1", "key-1"}) {
		t.Fatalf("ended %v", got)
	}
}

func TestAnEndAnsweredNotFoundCountsAsEnded(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.NotFound, Details: "already ended"})
	session.Close(ctx)
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("ended %v", got)
	}
	if warned(f) {
		t.Fatalf("logs %s", f.logs)
	}
}

func TestClosingTheClientEndsTheKeysOfSessionsLeftOpen(t *testing.T) {
	f := credentialed(t)
	first := actingAs(t, f, "customer-1")
	second := actingAs(t, f, "customer-2")
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	runtime.KeepAlive(second)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1", "key-2"}) {
		t.Fatalf("ended %v", got)
	}
	// After the client, closing a session is a quiet no-op.
	first.Close(ctx)
	if got := ended(f); len(got) != 2 || warned(f) {
		t.Fatalf("ended %v; logs %s", got, f.logs)
	}
}

func TestTheCloseSweepCountsNotFoundAsEndedAndGoesOn(t *testing.T) {
	f := credentialed(t)
	open := []*Session{actingAs(t, f, "customer-1"), actingAs(t, f, "customer-2")}
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.NotFound, Details: "expired"})
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	runtime.KeepAlive(open)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1", "key-2"}) || warned(f) {
		t.Fatalf("ended %v; logs %s", got, f.logs)
	}
}

func TestTheCloseSweepStopsAtTheFirstFailureWithOneWarning(t *testing.T) {
	f := credentialed(t)
	var open []*Session
	for _, user := range []string{"customer-1", "customer-2", "customer-3"} {
		open = append(open, actingAs(t, f, user))
	}
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.Unavailable, Details: "busy"})
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	runtime.KeepAlive(open)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("ended %v", got)
	}
	warning := `level=WARN msg="could not end impersonation keys; they expire on their own"`
	if strings.Count(f.logs.String(), "could not end impersonation") != 1 || !f.logs.Has(warning) || !f.logs.Has(`key_ids="key-1, key-2, key-3"`) {
		t.Fatalf("logs %s", f.logs)
	}
}

func TestClosingTheClientWaitsForCallsInFlightBeforeEndingKeys(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	hold := f.server.Memory.Hold("Search")
	searched := make(chan error, 1)
	go func() {
		_, err := session.Search(ctx, "q", ScopedSearchParams{})
		searched <- err
	}()
	<-hold.Arrived()
	closed := make(chan error, 1)
	go func() { closed <- f.client.Close(ctx) }()
	time.Sleep(50 * time.Millisecond)
	if got := ended(f); len(got) != 0 {
		t.Fatalf("ended %v under a call in flight", got)
	}
	hold.Release()
	if err := <-searched; err != nil {
		t.Fatal(err)
	}
	if err := <-closed; err != nil {
		t.Fatal(err)
	}
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("ended %v", got)
	}
}

// The service may issue a key after the open asking for it was cancelled; one
// nobody holds would stay live, counted against the user's cap, until it
// expired.
func TestACancelledOpenEndsTheKeyItGoesOnToMint(t *testing.T) {
	f := credentialed(t)
	hold := f.server.Admin.Hold("ImpersonateExternalUser")
	opening, cancel := context.WithCancel(ctx)
	go func() {
		<-hold.Arrived()
		cancel()
	}()
	_, err := f.client.Memory.StartSession(opening, "coding", ExternalID("customer-42"))
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	expect[Error](t, err)
	hold.Release()
	eventually(t, "the key to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("memory calls %v", calls)
	}
}

func TestConcurrentSessionsForTwoUsersNeverCrossKeys(t *testing.T) {
	f := credentialed(t)
	users := []string{"alice", "bob"}
	var wg sync.WaitGroup
	errs := make(chan error, 2*10)
	for _, user := range users {
		wg.Go(func() {
			session, err := f.client.Memory.StartSession(ctx, "coding", ExternalID(user))
			if err != nil {
				errs <- err
				return
			}
			defer session.Close(ctx)
			var searches sync.WaitGroup
			for i := range 10 {
				searches.Go(func() {
					if _, err := session.Search(ctx, fmt.Sprintf("%s %d", user, i), ScopedSearchParams{}); err != nil {
						errs <- err
					}
				})
			}
			searches.Wait()
		})
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	metadata, searched := f.server.Memory.Metadata(), 0
	for i, request := range f.server.Memory.Received() {
		search, ok := request.(*memoryv1.SearchRequest)
		if !ok {
			continue
		}
		searched++
		user, _, _ := strings.Cut(search.GetQuery(), " ")
		if got := bearers(metadata[i : i+1])[0]; !strings.HasPrefix(got, "Bearer impersonation-"+user+"-") {
			t.Errorf("%s's search carried %q", user, got)
		}
	}
	if searched != 20 {
		t.Fatalf("control: %d searches", searched)
	}
}

// A caller's cancellation must not strand a live key against its user's cap.
func TestEndingAKeyIsNotStoppedByTheCallersCancellation(t *testing.T) {
	f := credentialed(t)
	session := actingAs(t, f, "customer-42")
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	session.Close(cancelled)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) || warned(f) {
		t.Fatalf("ended %v; logs %s", got, f.logs)
	}
}

func TestARenewalEndsTheOldKeyEvenAfterItsCallWasCancelled(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	hold := f.server.Memory.Hold("Search")
	searching, cancel := context.WithCancel(ctx)
	searched := make(chan error, 1)
	go func() {
		_, err := session.Search(searching, "q", ScopedSearchParams{})
		searched <- err
	}()
	<-hold.Arrived()
	clock.Advance(8 * time.Minute)
	serving(t, "key-2", f.server.Memory.Metadata, func() error {
		_, err := session.GetMemory(ctx, "memory-a")
		return err
	}, "Bearer impersonation-customer-42-2")
	cancel()
	if err := <-searched; !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	hold.Release()
	eventually(t, "key-1 to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
}

func TestTheCloseSweepRunsWhateverTheCloseContext(t *testing.T) {
	f := credentialed(t)
	open := actingAs(t, f, "customer-42")
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if err := f.client.Close(cancelled); err != nil {
		t.Fatal(err)
	}
	runtime.KeepAlive(open)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) || warned(f) {
		t.Fatalf("ended %v; logs %s", got, f.logs)
	}
}

// A key leaves the registry inside the call that ends it, so a close waiting
// for that call cannot find it and end it a second time.
func TestAKeyIsEndedOnceWhenTheClientClosesDuringItsEnd(t *testing.T) {
	for range 20 {
		f := credentialed(t)
		session := actingAs(t, f, "customer-42")
		hold := f.server.Admin.Hold("EndImpersonation")
		closedSession := make(chan struct{})
		go func() {
			session.Close(ctx)
			close(closedSession)
		}()
		<-hold.Arrived()
		closed := make(chan error, 1)
		go func() { closed <- f.client.Close(ctx) }()
		hold.Release()
		<-closedSession
		if err := <-closed; err != nil {
			t.Fatal(err)
		}
		if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
			t.Fatalf("ended %v", got)
		}
	}
}

func TestAnExpiredKeyIsDroppedRatherThanEnded(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.Unavailable, Details: "busy"})
	session.Close(ctx)
	clock.Advance(10 * time.Minute)
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("ended %v", got)
	}
}

func TestAMintDropsExpiredKeysSoTheCloseSendsNothingForThem(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	open := actingAs(t, f, "customer-1") // left open: only the sweep would end it
	clock.Advance(10 * time.Minute)
	later := actingAs(t, f, "customer-2")
	// Back to before key-1 expired: had the mint kept it, the sweep would
	// find it live again and end it.
	clock.Advance(-10 * time.Minute)
	later.Close(ctx)
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	runtime.KeepAlive(open)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-2"}) {
		t.Fatalf("ended %v", got)
	}
}

// Each key a user holds counts against their cap until it expires, so one
// that could not be ended is tried again before another is minted for them.
func TestAFailedEndIsRetriedBeforeThatUsersNextKey(t *testing.T) {
	f := credentialed(t)
	f.server.Admin.SetKeyCap(1) // the next key can only be minted once key-1 is ended
	first := actingAs(t, f, "customer-42")
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.Unavailable, Details: "busy"})
	first.Close(ctx)
	other := actingAs(t, f, "customer-7")
	defer other.Close(ctx)
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("another user's mint retried: ended %v", got)
	}
	f.server.Forget()
	second := actingAs(t, f, "customer-42")
	defer second.Close(ctx)
	if calls := f.server.Admin.Calls(); !reflect.DeepEqual(calls, []string{"EndImpersonation", "ImpersonateExternalUser"}) {
		t.Fatalf("admin calls %v", calls)
	}
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("ended %v", got)
	}
	// Reachable to the end: once collected, a closed session's abandoned key is
	// retried beside the next call too, which would race the mint's own retry.
	runtime.KeepAlive(first)
}

// Renewing a key mints the next before ending the last, so a user at the cap
// cannot renew: the key they hold serves on until it expires.
func TestAKeyRenewalRefusedAtTheCapIsRiddenOutOnTheCurrentKeyUntilItExpires(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	f.server.Admin.SetKeyCap(1)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	f.server.Forget()
	clock.Advance(510 * time.Second) // 0.85
	if _, err := session.Search(ctx, "q", ScopedSearchParams{}); err != nil {
		t.Fatal(err)
	}
	if got := bearers(f.server.Memory.Metadata()); !reflect.DeepEqual(got, []string{keyOne}) {
		t.Fatalf("authorization %q", got)
	}
	eventually(t, "the refused renewal to be logged", func() bool { return f.logs.Has(renewalFailed) })
	if calls := f.server.Admin.Calls(); !reflect.DeepEqual(calls, []string{"ImpersonateExternalUser"}) {
		t.Fatalf("admin calls %v", calls)
	}
	if !f.logs.Has("key_id=key-1") {
		t.Fatalf("logs %s", f.logs)
	}
	clock.Advance(90 * time.Second) // 1.0
	f.server.Admin.FailNext("ImpersonateExternalUser", testserver.Failure{Code: codes.ResourceExhausted, Details: "customer-42 holds 1 live impersonation keys"})
	_, err := session.Search(ctx, "q", ScopedSearchParams{})
	expect[*ResourceExhaustedError](t, err)
}
