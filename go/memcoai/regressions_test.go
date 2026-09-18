package memcoai

// Each test here pins a defect found in one of the SDKs; the comment names it.

import (
	"context"
	"net"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc/codes"

	"github.com/memcoai/memcoai/go/internal/testserver"
)

// A whitespace domain or session was sent as though it named one.
func TestAWhitespaceScopeIsRefusedEndToEnd(t *testing.T) {
	f := connected(t)
	_, err := f.client.Memory.Search(ctx, "q", SearchParams{Domain: "  "})
	invalid(t, err, "domain must not be empty")
	_, err = f.client.Memory.CreateMemory(ctx, CreateMemoryParams{Query: "q", Title: "t", Content: "c", SessionID: "\t"})
	invalid(t, err, "session_id must not be empty")
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

// A deadline the caller had already spent was replaced by the default.
func TestAnExpiredContextIsNotWidened(t *testing.T) {
	f := connected(t)
	expired, cancel := context.WithDeadline(ctx, time.Now().Add(-time.Second))
	defer cancel()
	_, err := f.client.Memory.GetMemory(expired, "memory-a-1")
	expect[*TimeoutError](t, err)
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

// Closing while calls were in flight surfaced a raw transport error.
func TestClosingUnderLoadOnlyEverReturnsTypedErrors(t *testing.T) {
	for range 5 {
		f := connected(t)
		f.server.Memory.Delay("Search", 20*time.Millisecond)
		var calls sync.WaitGroup
		errs := make(chan error, 64)
		for range 8 {
			calls.Go(func() {
				m := f.client.Memory
				for _, call := range []func() error{
					func() error { _, err := m.ListDomains(ctx); return err },
					func() error { _, err := m.Search(ctx, "q", SearchParams{Domain: "d"}); return err },
					func() error { _, err := m.GetMemory(ctx, "memory-a-1"); return err },
					func() error { _, err := m.StartSession(ctx, "d"); return err },
				} {
					errs <- call()
				}
			})
		}
		time.Sleep(5 * time.Millisecond)
		if err := f.client.Close(ctx); err != nil {
			t.Fatal(err)
		}
		calls.Wait()
		close(errs)
		for err := range errs {
			if err != nil {
				expect[Error](t, err)
			}
		}
	}
}

// A bracketed IPv6 host was dialled as a hostname.
func TestAnIPv6HostIsDialled(t *testing.T) {
	listener, err := net.Listen("tcp", "[::1]:0")
	if err != nil {
		t.Skip("no IPv6 loopback here")
	}
	_ = listener.Close()
	server := testserver.StartOn(t, "[::1]:0")
	client, err := NewClient(Options{Token: testToken, Host: server.Address, Plaintext: true})
	if err != nil {
		t.Fatal(err)
	}
	closeLater(t, client)
	if err := client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
}

// An un-revertible write's empty operation id was sent to RevertMemory.
func TestAnEmptyOperationIDIsRefused(t *testing.T) {
	f := connected(t)
	_, err := f.client.Memory.RevertMemory(ctx, "")
	invalid(t, err, "operation_id must not be empty")
}

// Text protobuf cannot encode surfaced as an internal marshalling failure.
func TestUnsendableTextIsAnInvalidRequest(t *testing.T) {
	f := connected(t)
	bad := string([]byte{0xff})
	checks := []func() error{
		func() error { _, err := f.client.Memory.Search(ctx, bad, SearchParams{Domain: "d"}); return err },
		func() error {
			_, err := f.client.Memory.Search(ctx, "q", SearchParams{Domain: "d", Tags: []Tag{{Type: "a", Value: "b", Version: bad}}})
			return err
		},
		func() error { _, err := f.client.Memory.GetMemory(ctx, bad); return err },
		func() error {
			_, err := f.client.Memory.CreateMemory(ctx, CreateMemoryParams{Query: "q", Title: bad, Content: "c", Domain: "d"})
			return err
		},
		func() error {
			_, err := f.client.Memory.ShareFeedback(ctx, "s", []FeedbackRating{{Idx: "i", Comment: bad}})
			return err
		},
		func() error {
			_, err := f.client.Memory.ImportMemories(ctx, []ImportedMemory{{
				Queries: []string{"q"}, Insights: []ImportedInsight{{Title: "t", Content: bad}},
			}}, ImportMemoriesParams{Domain: "d"})
			return err
		},
	}
	for i, check := range checks {
		got := as[*InvalidRequestError](t, check())
		if len(got.Detail) < 30 || got.Detail[:30] != "a field value cannot be sent: " {
			t.Errorf("%d: %q", i, got.Detail)
		}
	}
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("calls %v", calls)
	}
}

// A blank tag's own message was replaced by the encoding guard's.
func TestABlankTagKeepsItsOwnMessage(t *testing.T) {
	f := connected(t)
	_, err := f.client.Memory.Search(ctx, string([]byte{0xff}), SearchParams{Domain: "d", Tags: []Tag{{Value: "x"}}})
	invalid(t, err, "tag type must not be empty")
}

// A transient failure at connect left the client unable to connect again.
func TestConnectRecoversFromATransientFailure(t *testing.T) {
	f := started(t)
	for range 3 {
		f.server.Health.FailNext(testserver.Failure{Code: codes.Unavailable, Details: "starting"})
	}
	expect[*UnavailableError](t, f.client.Connect(ctx))
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
}
