package memcoai

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand/v2"
	"reflect"
	"runtime"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc/codes"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

// dropSession opens a session acting as externalID, uses it, and lets it go
// unclosed.
func dropSession(t *testing.T, f *fixture, externalID string) {
	t.Helper()
	session := actingAs(t, f, externalID)
	if _, err := session.Search(ctx, "q", ScopedSearchParams{}); err != nil {
		t.Fatal(err)
	}
}

func TestADroppedSessionsKeyIsEndedByTheNextCall(t *testing.T) {
	f := credentialed(t)
	dropSession(t, f, "customer-42")
	eventually(t, "the dropped key to be ended", func() bool {
		runtime.GC()
		if _, err := f.client.Memory.ListTools(ctx); err != nil {
			t.Fatal(err)
		}
		return reflect.DeepEqual(ended(f), []string{"key-1"})
	})
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if got := ended(f); !reflect.DeepEqual(got, []string{"key-1"}) {
		t.Fatalf("ended %v", got)
	}
}

// A long-lived client whose callers forget to close their sessions must not
// lock their users out: each user may hold only so many live keys.
func TestDroppedSessionsNeverExhaustTheirUsersCap(t *testing.T) {
	f := credentialed(t)
	f.server.Admin.SetKeyCap(20)
	for range 25 {
		dropSession(t, f, "customer-42")
		runtime.GC()
	}
	eventually(t, "every dropped key to be ended", func() bool {
		runtime.GC()
		if _, err := f.client.Memory.ListTools(ctx); err != nil {
			t.Fatal(err)
		}
		return len(f.server.Admin.Unended()) == 0
	})
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	counts := map[string]int{}
	for _, id := range ended(f) {
		counts[id]++
	}
	for n := 1; n <= 25; n++ {
		if id := fmt.Sprintf("key-%d", n); counts[id] != 1 {
			t.Errorf("%s was ended %d times", id, counts[id])
		}
	}
}

// Sessions dropped with their user at the cap must not refuse that user's
// next session: the client holds each of their keys, and the mint waits for
// the ends of the ones already under way.
func TestSessionsDroppedAtTheCapDoNotRefuseTheNext(t *testing.T) {
	for range 5 {
		f := credentialed(t)
		f.server.Admin.SetKeyCap(3)
		for range 3 {
			dropSession(t, f, "customer-42")
		}
		f.server.Admin.Delay("EndImpersonation", 50*time.Millisecond)
		runtime.GC()
		runtime.GC()
		next, err := f.client.Memory.StartSession(ctx, "coding", ExternalID("customer-42"))
		if err != nil {
			t.Fatalf("StartSession: %v; admin calls %v", err, f.server.Admin.Calls())
		}
		next.Close(ctx)
	}
}

// A key whose end failed is ended before its user's next key is minted, even
// once the session that held it has been collected.
func TestAFailedEndOfACollectedSessionIsRetriedBeforeTheNextKey(t *testing.T) {
	f := credentialed(t)
	first := actingAs(t, f, "customer-42")
	f.server.Admin.FailNext("EndImpersonation", testserver.Failure{Code: codes.Unavailable, Details: "busy"})
	first.Close(ctx)
	first = nil
	runtime.GC()
	runtime.GC()
	f.server.Admin.Delay("EndImpersonation", 100*time.Millisecond)
	f.server.Forget()
	second := actingAs(t, f, "customer-42")
	defer second.Close(ctx)
	calls := f.server.Admin.Calls()
	at := slices.Index(calls, "ImpersonateExternalUser")
	if at < 0 || slices.Index(calls, "EndImpersonation") > at || slices.Index(calls, "EndImpersonation") < 0 {
		t.Fatalf("admin calls %v", calls)
	}
}

// The call that gives back a replaced key last does not wait for its end: it
// has no part in it, and its own deadline would not bound it.
func TestTheLastCallOnAReplacedKeyDoesNotWaitForItsEnd(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(10 * time.Minute)
	session := actingAs(t, f, "customer-42")
	defer session.Close(ctx)
	hold := f.server.Memory.Hold("Search")
	searched := make(chan time.Duration, 1)
	go func() {
		started := time.Now()
		if _, err := session.Search(ctx, "q", ScopedSearchParams{}); err != nil {
			t.Error(err)
		}
		searched <- time.Since(started)
	}()
	<-hold.Arrived()
	clock.Advance(8 * time.Minute)
	serving(t, "key-2", f.server.Memory.Metadata, func() error {
		_, err := session.GetMemory(ctx, "memory-a")
		return err
	}, "Bearer impersonation-customer-42-2")
	f.server.Admin.Delay("EndImpersonation", 2*time.Second)
	started := time.Now()
	hold.Release()
	<-searched
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("the search returned %v after its answer, waiting on an end", elapsed)
	}
	eventually(t, "key-1 to be ended", func() bool { return reflect.DeepEqual(ended(f), []string{"key-1"}) })
}

// stressed is one session of the stress run, and the tag every call it makes
// carries, so the fake's log says which session sent what.
type stressed struct {
	user, tag string
}

// run makes calls through one session, some of them cancelled at random.
// Only a cancelled call may fail, and only with its context's error.
func (s stressed) run(t *testing.T, f *fixture, seed uint64) error {
	random := rand.New(rand.NewPCG(seed, 7))
	session, err := f.client.Memory.StartSession(ctx, s.tag, ExternalID(s.user))
	if err != nil {
		return fmt.Errorf("%s: open: %w", s.tag, err)
	}
	defer session.Close(ctx)
	for i := range 14 {
		// A tagged call that may be cancelled is marked, so a late arrival
		// of it at the server is not mistaken for a use after its key ended.
		call, tag := ctx, fmt.Sprintf("%s/%d", s.tag, i)
		if random.IntN(4) == 0 {
			var cancel context.CancelFunc
			call, cancel = context.WithTimeout(ctx, time.Duration(random.IntN(3000))*time.Microsecond)
			defer cancel()
			tag += "/cancellable"
		}
		var err error
		switch i % 8 {
		case 0:
			_, err = session.Search(call, tag, ScopedSearchParams{})
		case 1:
			var memory *Memory
			if memory, err = session.GetMemory(call, tag); err == nil {
				_, err = memory.Feedback(call, MemoryFeedback{Relevant: true})
			}
		case 2:
			_, err = session.CreateMemory(call, ScopedCreateMemoryParams{Query: tag, Title: "t", Content: "c"})
		case 3:
			_, err = session.EnrichMemory(call, ScopedEnrichMemoryParams{MemoryIdx: NewMemory, Title: tag, Content: "c"})
		case 4:
			_, err = session.ShareFeedback(call, []FeedbackRating{{Idx: tag, Relevant: true}})
		case 5:
			_, err = session.RevertMemory(call, tag)
		case 6:
			_, err = session.ImportMemories(call, []ImportedMemory{{
				Queries: []string{tag}, Insights: []ImportedInsight{{Title: "t", Content: "c"}},
			}})
		case 7:
			args, _ := json.Marshal(map[string]string{"query": tag})
			_, err = session.Tools().Call(call, "memco_search", args)
		}
		var api *APIError
		if err != nil && (call == ctx || !errors.As(err, &api) || api.Code != codes.DeadlineExceeded && api.Code != codes.Canceled) {
			return fmt.Errorf("%s: %w", tag, err)
		}
		if random.IntN(3) == 0 {
			if _, err := f.client.Users.List(ctx, ListUsersParams{}); err != nil {
				return fmt.Errorf("%s: users: %w", tag, err)
			}
		}
	}
	return nil
}

// tagOf reads which session a memory call was made for, and whether it may
// have been cancelled; untagged calls are the ones opening a session.
func tagOf(request any) (string, bool) {
	var text string
	switch r := request.(type) {
	case *memoryv1.StartSessionRequest:
		text = r.GetDomain()
	case *memoryv1.SearchRequest:
		text = r.GetQuery()
	case *memoryv1.GetMemoryRequest:
		text = r.GetIdx()
	case *memoryv1.CreateMemoryRequest:
		text = r.GetQuery()
	case *memoryv1.EnrichMemoryRequest:
		text = r.GetTitle()
	case *memoryv1.ShareFeedbackRequest:
		text = r.GetFeedback()[0].GetIdx()
	case *memoryv1.RevertMemoryRequest:
		text = r.GetOpId()
	case *memoryv1.ImportMemoriesRequest:
		text = r.GetMemories()[0].GetQueries()[0]
	default:
		return "", false
	}
	session, _, _ := strings.Cut(text, "/")
	return session, strings.HasSuffix(text, "/cancellable")
}

// Sessions for many users, two of them for the same user, make interleaved
// calls while keys and the client's token renew every few milliseconds and
// some calls are cancelled. The fake's log must show that every call carried
// its own session's key and nothing else, that no key was ended twice or as
// another user, that no key was used after its end, and that every key was
// ended or expired.
func TestUnderLoadEveryCallCarriesItsOwnSessionsKey(t *testing.T) {
	f, clock := timed(t)
	f.server.Admin.SetKeyLifetime(time.Minute)
	f.server.Tokens.SetExpiresIn(60)
	f.server.Memory.Respond("ShareFeedback", &memoryv1.ShareFeedbackResponse{
		Entries: []*memoryv1.FeedbackEntry{{Idx: "rated", Relevant: true}},
	})
	stop := make(chan struct{})
	ticking := make(chan struct{})
	go func() {
		defer close(ticking)
		for {
			select {
			case <-stop:
				return
			case <-time.After(time.Millisecond):
				clock.Advance(5 * time.Second)
			}
		}
	}()
	var sessions []stressed
	for n := range 8 {
		sessions = append(sessions, stressed{fmt.Sprintf("user%d", n), fmt.Sprintf("s%d", n)})
	}
	sessions = append(sessions, stressed{"user0", "s8"}) // a second session for one user
	errs := make(chan error, len(sessions)+1)
	var wg sync.WaitGroup
	for n, session := range sessions {
		wg.Go(func() { errs <- session.run(t, f, uint64(n)) })
	}
	wg.Go(func() {
		for range 5 {
			if err := f.client.Connect(ctx); err != nil {
				errs <- fmt.Errorf("connect: %w", err)
				return
			}
		}
	})
	wg.Wait()
	close(stop)
	<-ticking
	close(errs)
	for err := range errs {
		if err != nil {
			t.Error(err)
		}
	}
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	verifyStress(t, f, sessions)
}

// keyID is the id of the key a bearer carries: impersonation-<user>-<n> is key-<n>.
func keyID(bearer string) string { return "key-" + bearer[strings.LastIndex(bearer, "-")+1:] }

func verifyStress(t *testing.T, f *fixture, sessions []stressed) {
	t.Helper()
	userOf := map[string]string{}
	for _, session := range sessions {
		userOf[session.tag] = session.user
	}
	administration := map[string]bool{}
	for _, method := range adminv1.AdminService_ServiceDesc.Methods {
		administration[method.MethodName] = true
	}
	owner := map[string]string{}     // key id → the session that carried it
	userOfKey := map[string]string{} // key id → the user it acts as
	endedAt := map[string]int{}      // key id → where its first end arrived
	ends := map[string]int{}         // key id → how many ends arrived
	log := f.server.Log()
	for at, call := range log {
		if len(call.Authorization) > 1 {
			t.Errorf("%s carried %d credentials", call.Method, len(call.Authorization))
		}
		bearer := strings.Join(call.Authorization, "")
		if end, ok := call.Request.(*adminv1.EndImpersonationRequest); ok {
			if ends[end.GetKeyId()]++; ends[end.GetKeyId()] == 1 {
				endedAt[end.GetKeyId()] = at
			}
		}
		tag, _ := tagOf(call.Request)
		switch {
		case call.Method == "Check" || call.Method == "IssueToken":
			if bearer != "" {
				t.Errorf("%s carried %q", call.Method, bearer)
			}
		case administration[call.Method]:
			if !strings.HasPrefix(bearer, "Bearer client-token-") {
				t.Errorf("%s carried %q", call.Method, bearer)
			}
		case tag == "":
			// ListDomains and ListTools, opening a session.
			if !strings.HasPrefix(bearer, "Bearer impersonation-") {
				t.Errorf("%s carried %q", call.Method, bearer)
			}
		case !strings.HasPrefix(bearer, "Bearer impersonation-"+userOf[tag]+"-"):
			t.Errorf("%s's %s carried %q", tag, call.Method, bearer)
		default:
			id := keyID(bearer)
			if previous, seen := owner[id]; seen && previous != tag {
				t.Errorf("%s was carried by %s and by %s", id, previous, tag)
			}
			owner[id], userOfKey[id] = tag, userOf[tag]
		}
	}
	for at, call := range log {
		if tag, cancellable := tagOf(call.Request); tag != "" && !cancellable {
			if end, ok := endedAt[keyID(strings.Join(call.Authorization, ""))]; ok && end < at {
				t.Errorf("%s's %s used its key after the key was ended", tag, call.Method)
			}
		}
		if end, ok := call.Request.(*adminv1.EndImpersonationRequest); ok {
			if user, seen := userOfKey[end.GetKeyId()]; seen && user != end.GetExternalId() {
				t.Errorf("%s, minted for %s, was ended as %s", end.GetKeyId(), user, end.GetExternalId())
			}
		}
	}
	for id, n := range ends {
		if n != 1 {
			t.Errorf("%s was ended %d times", id, n)
		}
	}
	if live := f.server.Admin.Unended(); len(live) != 0 {
		t.Errorf("left live: %v", live)
	}
	t.Logf("%d calls; %d keys carried, %d ended", len(log), len(owner), len(ends))
	if len(owner) < 2*len(sessions) || len(ends) == 0 {
		t.Fatalf("control: %d keys carried, %d ended; the run renewed too little to prove anything", len(owner), len(ends))
	}
}
