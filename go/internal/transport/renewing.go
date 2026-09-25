package transport

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/memcoai/memcoai/go/internal/config"
	"github.com/memcoai/memcoai/go/internal/fault"
)

// ClosedSession refuses a lease of a closed credential.
const ClosedSession = "this session is closed; open a new one to make more calls"

// RenewAfter is the fraction of a credential's lifetime after which it is
// renewed: late enough not to mint needlessly, early enough that a call leased
// just before still has a fifth of the lifetime to finish in.
const RenewAfter = 0.8

// RenewAt is when a credential minted at now with the given lifetime is due.
func RenewAt(now time.Time, lifetime time.Duration) time.Time {
	return now.Add(time.Duration(RenewAfter * float64(lifetime)))
}

// Minted is one credential a Renewing holds, and the leases still using it.
type Minted struct {
	Value *config.Credential
	// RenewAt is when to replace it; zero never.
	RenewAt time.Time
	// Expires is when it stops working, on the same clock.
	Expires time.Time
	// KeyID names it to the service, where it has an id. Safe to log.
	KeyID string

	users   int
	retired bool
}

// flight is one mint, shared by every lease that finds the credential due
// while it runs, so a failure reaches them all rather than each minting again
// in turn.
type flight struct {
	done chan struct{}
	err  error
}

// Renewing is a credential minted on first use and renewed at its renewal
// point, one mint at a time. A credential it has replaced is ended once the
// last call carrying it has finished.
type Renewing struct {
	now  func() time.Time
	mint func(context.Context) (*Minted, error)
	end  func(context.Context, *Minted)
	log  *slog.Logger

	mu      sync.Mutex
	current *Minted
	flight  *flight
	closed  bool
}

// NewRenewing holds nothing until the first lease mints. end, which must not
// fail, may be nil when there is nothing to end a credential with.
func NewRenewing(now func() time.Time, mint func(context.Context) (*Minted, error), end func(context.Context, *Minted), log *slog.Logger) *Renewing {
	return &Renewing{now: now, mint: mint, end: end, log: log}
}

// Static holds a credential that is never renewed or ended.
func Static(value *config.Credential) *Renewing {
	return &Renewing{now: time.Now, current: &Minted{Value: value}}
}

// Lease holds a credential for one call. Give it back with Release.
//
// Past the renewal point, a renewal starts beside the call, one at a time,
// and the current credential serves until it expires. A lease waits for a
// renewal, as long as ctx allows, only when no credential serves: there is
// none yet, or the current one has expired.
//
// A renewal runs on without its caller: once a lease gives up on its context,
// the service may still issue the credential, and one nobody held could never
// be ended. So the mint is kept, or ended if the credential closed meanwhile.
func (r *Renewing) Lease(ctx context.Context) (*Minted, error) {
	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return nil, fault.Config(ClosedSession)
	}
	now, held := r.now(), r.current
	if (held == nil || !held.RenewAt.IsZero() && !now.Before(held.RenewAt)) && r.flight == nil {
		r.flight = &flight{done: make(chan struct{})}
		go r.renew(context.WithoutCancel(ctx), r.flight)
	}
	if held != nil && (held.Expires.IsZero() || now.Before(held.Expires)) {
		held.users++
		r.mu.Unlock()
		return held, nil
	}
	f := r.flight
	r.mu.Unlock()
	select {
	case <-f.done:
	case <-ctx.Done():
		return nil, fault.FromContext(ctx)
	}
	if f.err != nil {
		return nil, f.err
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return nil, fault.Config(ClosedSession)
	}
	r.current.users++
	return r.current, nil
}

// renew mints for flight f and replaces the current credential, ending the
// one it replaced if nothing is using it.
//
// A failed renewal of a credential that has not yet expired is handled, not
// returned: the current one serves on, and the next lease past the renewal
// point tries again.
func (r *Renewing) renew(ctx context.Context, f *flight) {
	fresh, err := r.mint(ctx)
	var stale, serving *Minted
	r.mu.Lock()
	r.flight = nil
	switch {
	case err != nil:
		if r.current != nil && r.now().Before(r.current.Expires) {
			serving = r.current
		}
	case r.closed:
		stale = fresh
	default:
		stale, r.current = retire(r.current), fresh
	}
	r.mu.Unlock()
	if serving != nil {
		attrs := []any{"error", err.Error()}
		if serving.KeyID != "" {
			attrs = append(attrs, "key_id", serving.KeyID)
		}
		r.log.Warn("could not renew a credential; the current one serves until it expires", attrs...)
		err = nil
	}
	f.err = err
	close(f.done)
	if stale != nil {
		r.ending(ctx, stale)
	}
}

// retire marks a replaced credential, returning it when nothing uses it, for
// the caller to end; otherwise its last Release ends it.
func retire(m *Minted) *Minted {
	if m == nil {
		return nil
	}
	m.retired = true
	if m.users > 0 {
		return nil
	}
	return m
}

// Release gives back a leased credential, ending it if it was replaced and
// this was its last lease. The end runs beside the caller rather than in its
// call, which is done and has no part in it.
func (r *Renewing) Release(ctx context.Context, held *Minted) {
	r.mu.Lock()
	held.users--
	done := held.retired && held.users == 0
	r.mu.Unlock()
	if done && r.end != nil {
		go r.end(context.WithoutCancel(ctx), held)
	}
}

// Close refuses further leases, and ends the credential once nothing uses it.
// A mint still running is ended as it lands.
func (r *Renewing) Close(ctx context.Context) {
	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return
	}
	r.closed = true
	idle := retire(r.current)
	r.current = nil
	r.mu.Unlock()
	if idle != nil {
		r.ending(ctx, idle)
	}
}

func (r *Renewing) ending(ctx context.Context, m *Minted) {
	if r.end != nil {
		r.end(ctx, m)
	}
}
