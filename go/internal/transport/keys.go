package transport

import (
	"slices"
	"strings"
	"sync"
	"time"
)

// Keys registers the impersonation keys a client has minted and not yet
// ended, so the keys nobody else will end are ended by the client: those of
// sessions dropped unclosed, those whose end failed, and at close whatever is
// left. Each counts against its user's cap until it is ended or expires, so
// expired keys are forgotten rather than ended. The zero value is ready.
type Keys struct {
	mu      sync.Mutex
	live    map[string]liveKey
	holders uint64
	dropped []uint64
	swept   bool
}

type liveKey struct {
	externalID string
	expires    time.Time
	// holder numbers the session credential holding the key.
	holder uint64
	// ending is closed once the end under way is done; nil while none is,
	// and set so no other end is sent meanwhile.
	ending chan struct{}
	// abandoned is set once ending the key failed.
	abandoned bool
}

// finish reports an end of key done, to whatever waits on it.
func finish(key *liveKey) {
	if key.ending != nil {
		close(key.ending)
		key.ending = nil
	}
}

// Key names one registered key.
type Key struct{ ExternalID, ID string }

// Holder numbers a new session credential, for Add and Drop.
func (k *Keys) Holder() uint64 {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.holders++
	return k.holders
}

// Add registers a key a session credential minted, reporting false once the
// registry has been swept: nothing would end the key then.
func (k *Keys) Add(key Key, expires time.Time, holder uint64) bool {
	k.mu.Lock()
	defer k.mu.Unlock()
	if k.swept {
		return false
	}
	if k.live == nil {
		k.live = map[string]liveKey{}
	}
	k.live[key.ID] = liveKey{externalID: key.ExternalID, expires: expires, holder: holder}
	return true
}

// Claim marks a key as being ended, reporting false when there is nothing to
// end: an end of it is under way, or it is no longer registered because it
// was ended, expired or swept.
func (k *Keys) Claim(id string) bool {
	k.mu.Lock()
	defer k.mu.Unlock()
	key, ok := k.live[id]
	if !ok || key.ending != nil {
		return false
	}
	key.ending = make(chan struct{})
	k.live[id] = key
	return true
}

// Ended forgets a key the service no longer holds.
func (k *Keys) Ended(id string) {
	k.mu.Lock()
	defer k.mu.Unlock()
	if key, ok := k.live[id]; ok {
		finish(&key)
		delete(k.live, id)
	}
}

// Unclaimed gives back a claimed key whose end did not happen; abandoned
// marks one whose end failed, to be tried again.
func (k *Keys) Unclaimed(id string, abandoned bool) {
	k.mu.Lock()
	defer k.mu.Unlock()
	if key, ok := k.live[id]; ok {
		finish(&key)
		key.abandoned = key.abandoned || abandoned
		k.live[id] = key
	}
}

// Drop queues the keys of a session credential that was collected unclosed.
// It only records: it runs on the runtime's cleanup goroutine.
func (k *Keys) Drop(holder uint64) {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.dropped = append(k.dropped, holder)
}

// Dropped returns, claimed, the keys of the session credentials dropped since
// it was last asked, or nothing when none were.
func (k *Keys) Dropped() []Key {
	k.mu.Lock()
	defer k.mu.Unlock()
	if len(k.dropped) == 0 {
		return nil
	}
	return k.take(func(liveKey) bool { return false })
}

// Before returns, claimed, the keys to end before minting another for
// externalID: those of dropped session credentials, and the user's own whose
// end failed. It also returns what to wait on for the user's keys already
// being ended elsewhere, which free a place under the user's cap too. Keys
// past their expiry are forgotten on the way.
func (k *Keys) Before(externalID string, now time.Time) ([]Key, []<-chan struct{}) {
	k.mu.Lock()
	defer k.mu.Unlock()
	for id, key := range k.live {
		if !key.expires.After(now) {
			finish(&key)
			delete(k.live, id)
		}
	}
	var waits []<-chan struct{}
	for _, key := range k.live {
		if key.ending != nil && key.externalID == externalID {
			waits = append(waits, key.ending)
		}
	}
	return k.take(func(key liveKey) bool { return key.abandoned && key.externalID == externalID }), waits
}

// take claims the keys of dropped holders and those also wanted, skipping
// any already being ended, and empties the dropped queue. The caller holds mu.
func (k *Keys) take(wanted func(liveKey) bool) []Key {
	dropped := k.dropped
	k.dropped = nil
	var keys []Key
	for id, key := range k.live {
		if key.ending == nil && (slices.Contains(dropped, key.holder) || wanted(key)) {
			key.ending = make(chan struct{})
			k.live[id] = key
			keys = append(keys, Key{key.externalID, id})
		}
	}
	return keys
}

// Sweep empties the registry for good, returning the keys not yet expired,
// by id.
func (k *Keys) Sweep(now time.Time) []Key {
	k.mu.Lock()
	live := k.live
	k.live, k.dropped, k.swept = nil, nil, true
	k.mu.Unlock()
	var keys []Key
	for id, key := range live {
		// The close ends every key it keeps, whoever else was ending it.
		finish(&key)
		if key.expires.After(now) {
			keys = append(keys, Key{key.externalID, id})
		}
	}
	slices.SortFunc(keys, func(a, b Key) int { return strings.Compare(a.ID, b.ID) })
	return keys
}
