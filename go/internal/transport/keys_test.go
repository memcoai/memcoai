package transport_test

import (
	"reflect"
	"testing"
	"time"

	"github.com/memcoai/memcoai/go/internal/transport"
)

var t0 = time.Unix(1_700_000_000, 0)

func TestAKeyIsClaimedByOneEndAtATime(t *testing.T) {
	var keys transport.Keys
	keys.Add(transport.Key{ExternalID: "u", ID: "key-1"}, t0.Add(time.Hour), keys.Holder())
	if !keys.Claim("key-1") || keys.Claim("key-1") {
		t.Fatal("a second end claimed key-1")
	}
	keys.Unclaimed("key-1", false)
	if !keys.Claim("key-1") {
		t.Fatal("an unclaimed key could not be claimed again")
	}
	keys.Ended("key-1")
	if keys.Claim("key-1") {
		t.Fatal("a key already ended was claimed again")
	}
}

func TestBeforeAMintExpiredKeysAreForgottenAndTheUsersFailedEndsReturned(t *testing.T) {
	var keys transport.Keys
	holder := keys.Holder()
	keys.Add(transport.Key{ExternalID: "u", ID: "key-1"}, t0.Add(time.Minute), holder)
	keys.Add(transport.Key{ExternalID: "u", ID: "key-2"}, t0.Add(time.Hour), holder)
	keys.Add(transport.Key{ExternalID: "v", ID: "key-3"}, t0.Add(time.Hour), holder)
	for _, id := range []string{"key-1", "key-2", "key-3"} {
		keys.Claim(id)
		keys.Unclaimed(id, true)
	}
	if got, waits := keys.Before("u", t0.Add(time.Minute)); !reflect.DeepEqual(got, []transport.Key{{ExternalID: "u", ID: "key-2"}}) || len(waits) != 0 {
		t.Fatalf("got %v, %d waits", got, len(waits))
	}
	if got := keys.Sweep(t0); !reflect.DeepEqual(got, []transport.Key{{ExternalID: "u", ID: "key-2"}, {ExternalID: "v", ID: "key-3"}}) {
		t.Fatalf("key-1 was kept: %v", got)
	}
}

func TestADroppedHoldersKeysAreReturnedOnce(t *testing.T) {
	var keys transport.Keys
	dropped, kept := keys.Holder(), keys.Holder()
	keys.Add(transport.Key{ExternalID: "u", ID: "key-1"}, t0.Add(time.Hour), dropped)
	keys.Add(transport.Key{ExternalID: "u", ID: "key-2"}, t0.Add(time.Hour), kept)
	if got := keys.Dropped(); got != nil {
		t.Fatalf("control: got %v", got)
	}
	keys.Drop(dropped)
	if got := keys.Dropped(); !reflect.DeepEqual(got, []transport.Key{{ExternalID: "u", ID: "key-1"}}) {
		t.Fatalf("got %v", got)
	}
	if keys.Claim("key-1") {
		t.Fatal("a key handed out to be ended was claimed again")
	}
	if got := keys.Dropped(); got != nil {
		t.Fatalf("returned again: %v", got)
	}
}

func TestTheSweepTakesEveryUnexpiredKeyInOrder(t *testing.T) {
	var keys transport.Keys
	for _, id := range []string{"key-3", "key-1", "key-2"} {
		keys.Add(transport.Key{ExternalID: "u", ID: id}, t0.Add(time.Hour), keys.Holder())
	}
	keys.Add(transport.Key{ExternalID: "u", ID: "key-0"}, t0, keys.Holder())
	got := keys.Sweep(t0)
	want := []transport.Key{{ExternalID: "u", ID: "key-1"}, {ExternalID: "u", ID: "key-2"}, {ExternalID: "u", ID: "key-3"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
	if got := keys.Sweep(t0); got != nil {
		t.Fatalf("swept twice: %v", got)
	}
}

// A mint waits for the user's keys already being ended elsewhere: each frees
// a place under the user's cap.
func TestBeforeAMintTheUsersEndsUnderWayAreWaitedOn(t *testing.T) {
	var keys transport.Keys
	holder := keys.Holder()
	keys.Add(transport.Key{ExternalID: "u", ID: "key-1"}, t0.Add(time.Hour), holder)
	keys.Add(transport.Key{ExternalID: "v", ID: "key-2"}, t0.Add(time.Hour), holder)
	keys.Claim("key-1")
	keys.Claim("key-2")
	pending, waits := keys.Before("u", t0)
	if len(pending) != 0 || len(waits) != 1 {
		t.Fatalf("got %v, %d waits", pending, len(waits))
	}
	select {
	case <-waits[0]:
		t.Fatal("done before the end was")
	default:
	}
	keys.Ended("key-1")
	<-waits[0]
	// An end that failed is not waited on but retried by the mint itself.
	keys.Unclaimed("key-2", true)
	if pending, waits := keys.Before("v", t0); len(pending) != 1 || len(waits) != 0 {
		t.Fatalf("got %v, %d waits", pending, len(waits))
	}
}

func TestTheSweepReleasesWhateverWaitsOnAKey(t *testing.T) {
	var keys transport.Keys
	keys.Add(transport.Key{ExternalID: "u", ID: "key-1"}, t0.Add(time.Hour), keys.Holder())
	keys.Claim("key-1")
	_, waits := keys.Before("u", t0)
	if got := keys.Sweep(t0); len(got) != 1 {
		t.Fatalf("swept %v", got)
	}
	<-waits[0]
}

func TestNothingIsRegisteredOnceSwept(t *testing.T) {
	var keys transport.Keys
	if !keys.Add(transport.Key{ExternalID: "u", ID: "key-1"}, t0.Add(time.Hour), keys.Holder()) {
		t.Fatal("control: a key was refused before the sweep")
	}
	keys.Sweep(t0)
	if keys.Add(transport.Key{ExternalID: "u", ID: "key-2"}, t0.Add(time.Hour), keys.Holder()) {
		t.Fatal("a key was registered after the sweep")
	}
	if got := keys.Sweep(t0); got != nil {
		t.Fatalf("swept %v", got)
	}
}
