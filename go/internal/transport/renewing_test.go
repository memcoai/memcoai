package transport_test

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"strings"
	"sync"
	"testing"
	"testing/synctest"
	"time"

	"github.com/memcoai/memcoai/go/internal/config"
	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/transport"
)

type clock struct {
	mu sync.Mutex
	at time.Time
}

func (c *clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.at
}

func (c *clock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.at = c.at.Add(d)
}

// mints mints key-1, key-2, ... each living a hundred seconds, and records
// which it was asked to end.
type mints struct {
	clock *clock

	mu       sync.Mutex
	attempts int
	minted   int
	ended    []string
	failing  error         // the next mint fails with this
	gate     chan struct{} // when set, a mint waits for it
	started  chan struct{} // when set, receives as each mint starts
	endGate  chan struct{} // when set, an end waits for it
}

const lifetime = 100 * time.Second

func newMints() *mints { return &mints{clock: &clock{at: time.Unix(1_700_000_000, 0)}} }

func (m *mints) mint(context.Context) (*transport.Minted, error) {
	m.mu.Lock()
	gate, started := m.gate, m.started
	m.mu.Unlock()
	if started != nil {
		started <- struct{}{}
	}
	if gate != nil {
		<-gate
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.attempts++
	if m.failing != nil {
		err := m.failing
		m.failing = nil
		return nil, err
	}
	m.minted++
	return &transport.Minted{
		Value:   config.Secret(fmt.Sprintf("value-%d", m.minted)),
		RenewAt: transport.RenewAt(m.clock.Now(), lifetime),
		Expires: m.clock.Now().Add(lifetime),
		KeyID:   fmt.Sprintf("key-%d", m.minted),
	}, nil
}

func (m *mints) end(_ context.Context, key *transport.Minted) {
	m.mu.Lock()
	gate := m.endGate
	m.mu.Unlock()
	if gate != nil {
		<-gate
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.ended = append(m.ended, key.KeyID)
}

func (m *mints) counts() (int, []string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.minted, append([]string(nil), m.ended...)
}

func (m *mints) renewing() *transport.Renewing {
	return transport.NewRenewing(m.clock.Now, m.mint, m.end, slog.New(slog.DiscardHandler))
}

// leased leases and gives back at once, returning the key id leased.
func leased(t *testing.T, r *transport.Renewing) string {
	t.Helper()
	held, err := r.Lease(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	r.Release(context.Background(), held)
	return held.KeyID
}

func expectCounts(t *testing.T, m *mints, minted int, ended ...string) {
	t.Helper()
	gotMinted, gotEnded := m.counts()
	if gotMinted != minted || !slices.Equal(gotEnded, ended) {
		t.Fatalf("minted %d, ended %v; want %d, %v", gotMinted, gotEnded, minted, ended)
	}
}

func TestTheFirstLeaseMintsAndLaterOnesReuseIt(t *testing.T) {
	m := newMints()
	r := m.renewing()
	for range 3 {
		if got := leased(t, r); got != "key-1" {
			t.Fatalf("leased %s", got)
		}
	}
	expectCounts(t, m, 1)
}

// At four fifths of its lifetime a credential is renewed beside the lease
// that finds it due, which the credential still serves.
func TestACredentialIsRenewedOnceFourFifthsOfItsLifetimeHavePassed(t *testing.T) {
	m := newMints()
	r := m.renewing()
	leased(t, r)
	m.clock.Advance(79 * time.Second)
	if got := leased(t, r); got != "key-1" {
		t.Fatalf("renewed early: %s", got)
	}
	expectCounts(t, m, 1)
	m.clock.Advance(time.Second)
	if got := leased(t, r); got != "key-1" {
		t.Fatalf("the lease at 0.8 got %s", got)
	}
	eventually(t, func() bool { minted, _ := m.counts(); return minted == 2 })
	if got := leased(t, r); got != "key-2" {
		t.Fatalf("not renewed at 0.8: %s", got)
	}
	// Nothing was using key-1 any more, so it was ended as it was replaced.
	eventually(t, func() bool { _, ended := m.counts(); return len(ended) == 1 })
	expectCounts(t, m, 2, "key-1")
}

// Only a lease with no credential still serving waits for a renewal: past the
// renewal point the current one serves, however long the renewal takes.
func TestLeasesPastTheRenewalPointNeverWaitForIt(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		m := newMints()
		r := m.renewing()
		leased(t, r)
		m.clock.Advance(85 * time.Second)
		m.gate = make(chan struct{})
		for range 8 {
			if got := leased(t, r); got != "key-1" {
				t.Fatalf("leased %s", got)
			}
		}
		synctest.Wait() // the one renewal, still held at its gate
		if m.attempts != 1 {
			t.Fatalf("%d mints finished", m.attempts)
		}
		close(m.gate)
		synctest.Wait()
		if got := leased(t, r); got != "key-2" || m.attempts != 2 {
			t.Fatalf("leased %s after %d mints", got, m.attempts)
		}
	})
}

// A lease that waited for a renewal is released as soon as the new credential
// serves; ending the one it replaced does not hold it.
func TestTheReplacedCredentialsEndDoesNotHoldTheLeaseThatWaited(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		m := newMints()
		r := m.renewing()
		leased(t, r)
		m.clock.Advance(lifetime)
		m.endGate = make(chan struct{})
		if got := leased(t, r); got != "key-2" {
			t.Fatalf("leased %s", got)
		}
		synctest.Wait() // the end of key-1, still held at its gate
		expectCounts(t, m, 2)
		close(m.endGate)
		synctest.Wait()
		expectCounts(t, m, 2, "key-1")
	})
}

func TestAStaticCredentialIsNeverRenewed(t *testing.T) {
	r := transport.Static(config.Secret("static-token"))
	for range 3 {
		held, err := r.Lease(context.Background())
		if err != nil || held.Value.Reveal() != "static-token" {
			t.Fatalf("got %v, %v", held, err)
		}
		r.Release(context.Background(), held)
	}
}

func TestAReplacedCredentialIsEndedOnlyAfterItsLastLease(t *testing.T) {
	m := newMints()
	r := m.renewing()
	first, err := r.Lease(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	m.clock.Advance(lifetime)
	if got := leased(t, r); got != "key-2" {
		t.Fatalf("leased %s", got)
	}
	expectCounts(t, m, 2)
	r.Release(context.Background(), first)
	eventually(t, func() bool { _, ended := m.counts(); return len(ended) == 1 })
	expectCounts(t, m, 2, "key-1")
}

func TestAFailedMintKeepsTheCurrentCredentialAndTheNextLeaseRetries(t *testing.T) {
	m := newMints()
	r := m.renewing()
	leased(t, r)
	m.clock.Advance(lifetime)
	m.failing = fault.Internal("mint refused")
	if _, err := r.Lease(context.Background()); err == nil || err.Error() != "INTERNAL: mint refused" {
		t.Fatalf("got %v", err)
	}
	expectCounts(t, m, 1)
	// The failed lease counted no user of key-1, so the renewal ends it at once.
	if got := leased(t, r); got != "key-2" {
		t.Fatalf("leased %s", got)
	}
	expectCounts(t, m, 2, "key-1")
}

func TestWithoutAnEndAReplacedCredentialIsDropped(t *testing.T) {
	m := newMints()
	r := transport.NewRenewing(m.clock.Now, m.mint, nil, slog.New(slog.DiscardHandler))
	leased(t, r)
	m.clock.Advance(lifetime)
	if got := leased(t, r); got != "key-2" {
		t.Fatalf("leased %s", got)
	}
	r.Close(context.Background())
}

func TestCloseEndsAnIdleCredentialAtOnceAndOnlyOnce(t *testing.T) {
	m := newMints()
	r := m.renewing()
	leased(t, r)
	r.Close(context.Background())
	r.Close(context.Background())
	expectCounts(t, m, 1, "key-1")
}

func TestCloseLeavesABusyCredentialToItsLastLease(t *testing.T) {
	m := newMints()
	r := m.renewing()
	held, err := r.Lease(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	r.Close(context.Background())
	expectCounts(t, m, 1)
	r.Release(context.Background(), held)
	eventually(t, func() bool { _, ended := m.counts(); return len(ended) == 1 })
	expectCounts(t, m, 1, "key-1")
}

func TestCloseBeforeAnythingWasMintedEndsNothing(t *testing.T) {
	m := newMints()
	m.renewing().Close(context.Background())
	expectCounts(t, m, 0)
}

func TestALeaseAfterCloseIsRefusedWithoutMinting(t *testing.T) {
	m := newMints()
	r := m.renewing()
	r.Close(context.Background())
	_, err := r.Lease(context.Background())
	var f *fault.Error
	if !errors.As(err, &f) || f.Kind != fault.KindConfig || f.Detail != transport.ClosedSession {
		t.Fatalf("got %v", err)
	}
	expectCounts(t, m, 0)
}

// leaseAtOnce leases from n goroutines at once and gives each lease back,
// returning the key ids leased. With gated set, every lease is left waiting
// on the mint before the gate opens.
func leaseAtOnce(t *testing.T, m *mints, r *transport.Renewing, n int) ([]string, []error) {
	t.Helper()
	var wg sync.WaitGroup
	keys := make([]string, n)
	errs := make([]error, n)
	for i := range n {
		wg.Go(func() {
			held, err := r.Lease(context.Background())
			if err != nil {
				errs[i] = err
				return
			}
			keys[i] = held.KeyID
			r.Release(context.Background(), held)
		})
	}
	if m.gate != nil {
		// Every goroutine is now blocked: one mint on the gate, and every
		// other lease on that mint.
		synctest.Wait()
		close(m.gate)
	}
	wg.Wait()
	return keys, errs
}

func TestLeasesArrivingAtOnceShareOneMint(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		m := newMints()
		m.gate = make(chan struct{})
		keys, errs := leaseAtOnce(t, m, m.renewing(), 8)
		if err := errors.Join(errs...); err != nil {
			t.Fatal(err)
		}
		for _, key := range keys {
			if key != "key-1" {
				t.Fatalf("leased %s", key)
			}
		}
		expectCounts(t, m, 1)
	})
}

func TestLeasesArrivingAtTheRenewalPointRenewOnce(t *testing.T) {
	m := newMints()
	r := m.renewing()
	leased(t, r)
	m.clock.Advance(lifetime)
	keys, errs := leaseAtOnce(t, m, r, 8)
	if err := errors.Join(errs...); err != nil {
		t.Fatal(err)
	}
	for _, key := range keys {
		if key != "key-2" {
			t.Fatalf("leased %s", key)
		}
	}
	expectCounts(t, m, 2, "key-1")
}

func TestAMintedCredentialIsInNoRendering(t *testing.T) {
	minted := &transport.Minted{Value: config.Secret("sk-live-key"), KeyID: "key-1"}
	var logged bytes.Buffer
	slog.New(slog.NewTextHandler(&logged, nil)).Info("x", "minted", minted, "value", *minted)
	renderings := []string{logged.String()}
	for _, verb := range []string{"%v", "%+v", "%#v", "%s"} {
		renderings = append(renderings, fmt.Sprintf(verb, minted), fmt.Sprintf(verb, *minted))
	}
	for _, rendered := range renderings {
		if strings.Contains(rendered, "sk-live-key") {
			t.Errorf("the value is visible in %q", rendered)
		}
	}
	if !strings.Contains(renderings[0], "key-1") {
		t.Fatalf("control: the key id was not rendered: %q", renderings[0])
	}
}

// eventually waits for check to hold, failing t after a few seconds.
func eventually(t *testing.T, check func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !check() {
		if time.Now().After(deadline) {
			t.Fatal("gave up waiting")
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// A lease honours its context, but the mint it started runs on: the service
// may already have issued the credential, and one nobody holds could never be
// ended.
func TestALeaseCancelledMidMintReturnsAndKeepsWhatTheMintProduces(t *testing.T) {
	m := newMints()
	m.gate, m.started = make(chan struct{}), make(chan struct{}, 1)
	r := m.renewing()
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		<-m.started
		cancel()
	}()
	if _, err := r.Lease(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	m.mu.Lock()
	m.started = nil
	m.mu.Unlock()
	close(m.gate)
	eventually(t, func() bool { minted, _ := m.counts(); return minted == 1 })
	if got := leased(t, r); got != "key-1" {
		t.Fatalf("leased %s", got)
	}
	r.Close(context.Background())
	expectCounts(t, m, 1, "key-1")
}

func TestAMintThatFinishesAfterCloseIsEnded(t *testing.T) {
	m := newMints()
	m.gate, m.started = make(chan struct{}), make(chan struct{}, 1)
	r := m.renewing()
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		<-m.started
		cancel()
	}()
	if _, err := r.Lease(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	r.Close(context.Background())
	close(m.gate)
	eventually(t, func() bool { _, ended := m.counts(); return len(ended) == 1 })
	expectCounts(t, m, 1, "key-1")
}

func TestAMintFailingAfterItsLeaseWasCancelledEndsNothing(t *testing.T) {
	m := newMints()
	m.gate, m.started = make(chan struct{}), make(chan struct{}, 1)
	m.failing = fault.Internal("mint refused")
	r := m.renewing()
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		<-m.started
		cancel()
	}()
	if _, err := r.Lease(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	m.mu.Lock()
	m.started = nil
	m.mu.Unlock()
	close(m.gate)
	eventually(t, func() bool { m.mu.Lock(); defer m.mu.Unlock(); return m.failing == nil })
	if got := leased(t, r); got != "key-1" {
		t.Fatalf("leased %s", got)
	}
	expectCounts(t, m, 1)
}

// Every lease waiting on a mint that fails gets that attempt's error, rather
// than each minting again in turn and waiting out the others' failures first.
func TestAFailedMintIsSharedByEveryLeaseWaitingOnIt(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		m := newMints()
		m.gate = make(chan struct{})
		m.failing = fault.Internal("mint refused")
		r := m.renewing()
		_, errs := leaseAtOnce(t, m, r, 8)
		for _, err := range errs {
			if err == nil || err.Error() != "INTERNAL: mint refused" {
				t.Fatalf("got %v", err)
			}
		}
		if m.attempts != 1 {
			t.Fatalf("%d mints were attempted", m.attempts)
		}
		m.gate = nil
		if got := leased(t, r); got != "key-1" {
			t.Fatalf("the next lease got %s", got)
		}
	})
}

func TestALeaseWaitingOnAMintGivesUpWhenItsOwnContextEnds(t *testing.T) {
	m := newMints()
	m.gate = make(chan struct{})
	r := m.renewing()
	first := make(chan string, 1)
	go func() {
		held, err := r.Lease(context.Background())
		if err != nil {
			first <- err.Error()
			return
		}
		first <- held.KeyID
	}()
	waiting, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	started := time.Now()
	if _, err := r.Lease(waiting); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v", err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("gave up after %v", elapsed)
	}
	close(m.gate)
	if got := <-first; got != "key-1" {
		t.Fatalf("the first lease got %s", got)
	}
}

// logBuffer is a log destination a renewal may write to while a test reads.
type logBuffer struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

func (b *logBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buffer.Write(p)
}

func (b *logBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buffer.String()
}

// logged is renewing with its warnings kept.
func (m *mints) logged() (*transport.Renewing, *logBuffer) {
	logs := &logBuffer{}
	return transport.NewRenewing(m.clock.Now, m.mint, m.end, slog.New(slog.NewTextHandler(logs, nil))), logs
}

const renewalFailed = "could not renew a credential; the current one serves until it expires"

// A credential still has a fifth of its life left when it falls due, so a
// renewal that fails leaves it serving, and is only logged.
func TestAFailedRenewalFallsBackToTheCurrentCredentialWhileItLasts(t *testing.T) {
	m := newMints()
	r, logs := m.logged()
	leased(t, r)
	m.clock.Advance(85 * time.Second)
	m.failing = fault.Internal("mint refused")
	if got := leased(t, r); got != "key-1" {
		t.Fatalf("leased %s", got)
	}
	eventually(t, func() bool { return strings.Contains(logs.String(), renewalFailed) })
	if strings.Count(logs.String(), renewalFailed) != 1 || !strings.Contains(logs.String(), "key_id=key-1") ||
		!strings.Contains(logs.String(), "mint refused") {
		t.Fatalf("logs %s", logs)
	}
	expectCounts(t, m, 1)
	// The next lease past the renewal point tries again.
	if got := leased(t, r); got != "key-1" {
		t.Fatalf("leased %s", got)
	}
	eventually(t, func() bool { minted, _ := m.counts(); return minted == 2 })
	if got := leased(t, r); got != "key-2" {
		t.Fatalf("leased %s", got)
	}
	m.mu.Lock()
	attempts := m.attempts
	m.mu.Unlock()
	if attempts != 3 {
		t.Fatalf("%d mints were attempted", attempts)
	}
	eventually(t, func() bool { _, ended := m.counts(); return len(ended) == 1 })
	expectCounts(t, m, 2, "key-1")
}

func TestAFailedRenewalOfAnExpiredCredentialIsReturned(t *testing.T) {
	m := newMints()
	r, logs := m.logged()
	leased(t, r)
	m.clock.Advance(lifetime)
	m.failing = fault.Internal("mint refused")
	if _, err := r.Lease(context.Background()); err == nil || err.Error() != "INTERNAL: mint refused" {
		t.Fatalf("got %v", err)
	}
	// Returned, so not also logged.
	if strings.Contains(logs.String(), "mint refused") {
		t.Fatalf("logs %s", logs)
	}
}

func TestLeasesWaitingOnAFailedRenewalOfAnExpiredCredentialShareItsError(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		m := newMints()
		r, logs := m.logged()
		leased(t, r)
		m.clock.Advance(lifetime)
		m.gate = make(chan struct{})
		m.failing = fault.Internal("mint refused")
		_, errs := leaseAtOnce(t, m, r, 8)
		for _, err := range errs {
			if err == nil || err.Error() != "INTERNAL: mint refused" {
				t.Fatalf("got %v", err)
			}
		}
		if m.attempts != 2 || strings.Contains(logs.String(), renewalFailed) {
			t.Fatalf("%d mints; logs %s", m.attempts, logs)
		}
	})
}
