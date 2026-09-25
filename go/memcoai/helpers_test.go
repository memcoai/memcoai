package memcoai

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc/metadata"

	"github.com/memcoai/memcoai/go/internal/testserver"
	"github.com/memcoai/memcoai/go/internal/warn"
)

const testToken = "test-token"

// captured is a log destination tests can read while calls write to it.
type captured struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

func (c *captured) Write(p []byte) (int, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.buffer.Write(p)
}

func (c *captured) String() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.buffer.String()
}

func (c *captured) Reset() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.buffer.Reset()
}

func (c *captured) Has(fragment string) bool { return strings.Contains(c.String(), fragment) }

type fixture struct {
	server *testserver.Harness
	client *Client
	logs   *captured
}

// started builds a client against a fresh fake without connecting it.
func started(t *testing.T, edits ...func(*Options)) *fixture {
	t.Helper()
	warn.Reset()
	t.Cleanup(warn.Reset)
	server := testserver.Start(t)
	logs := &captured{}
	options := Options{
		Token:     testToken,
		Host:      server.Address,
		Plaintext: true,
		Timeout:   5 * time.Second,
		Logger:    slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})),
	}
	for _, edit := range edits {
		edit(&options)
	}
	client, err := NewClient(options)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	closeLater(t, client)
	return &fixture{server: server, client: client, logs: logs}
}

// closeLater closes client when the test ends, failing it if that fails.
func closeLater(t *testing.T, client *Client) {
	t.Helper()
	t.Cleanup(func() {
		if err := client.Close(context.Background()); err != nil {
			t.Errorf("Close: %v", err)
		}
	})
}

// connected builds and connects a client, then forgets the connect calls.
func connected(t *testing.T, edits ...func(*Options)) *fixture {
	t.Helper()
	f := started(t, edits...)
	if err := f.client.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	f.server.Forget()
	return f
}

// as asserts err is, or wraps, a T and returns it.
func as[T error](t *testing.T, err error) T {
	t.Helper()
	var target T
	if !errors.As(err, &target) {
		t.Fatalf("got %T %v, want %T", err, err, target)
	}
	return target
}

// expect asserts err is, or wraps, a T.
func expect[T error](t *testing.T, err error) {
	t.Helper()
	_ = as[T](t, err)
}

// invalid asserts a local or remote INVALID_ARGUMENT with exactly detail.
func invalid(t *testing.T, err error, detail string) {
	t.Helper()
	got := as[*InvalidRequestError](t, err)
	if got.Detail != detail {
		t.Fatalf("got %q, want %q", got.Detail, detail)
	}
}

var ctx = context.Background()

const (
	testClientID     = "client-id"
	testClientSecret = "client-secret-value"
)

// asAPIClient swaps the fixture's token for an API client's credentials.
func asAPIClient(o *Options) {
	o.Token, o.ClientID, o.ClientSecret = "", testClientID, testClientSecret
}

// credentialed builds and connects a client holding an API client's
// credentials, then forgets the connect calls.
func credentialed(t *testing.T, edits ...func(*Options)) *fixture {
	t.Helper()
	return connected(t, append([]func(*Options){asAPIClient}, edits...)...)
}

// fakeClock is a clock a test moves by hand.
type fakeClock struct {
	mu sync.Mutex
	at time.Time
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.at
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.at = c.at.Add(d)
}

// timed is credentialed, with the client and the fake admin service both
// reading one fake clock, so a test decides when a credential falls due.
func timed(t *testing.T, edits ...func(*Options)) (*fixture, *fakeClock) {
	t.Helper()
	clock := &fakeClock{at: time.Unix(1_800_000_000, 0)}
	f := started(t, append([]func(*Options){asAPIClient}, edits...)...)
	f.client.now = clock.Now
	f.server.Admin.SetClock(clock.Now)
	if err := f.client.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	f.server.Forget()
	return f, clock
}

// bearers returns the authorization each call carried, in call order.
func bearers(mds []metadata.MD) []string {
	var got []string
	for _, md := range mds {
		got = append(got, strings.Join(md.Get("authorization"), ","))
	}
	return got
}

// eventually waits for check to hold, failing t after a few seconds.
func eventually(t *testing.T, what string, check func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !check() {
		if time.Now().After(deadline) {
			t.Fatalf("gave up waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// serving calls call until the credential it carries is want, as a renewal
// that runs beside the calls lands.
func serving(t *testing.T, what string, mds func() []metadata.MD, call func() error, want string) {
	t.Helper()
	eventually(t, what, func() bool {
		if err := call(); err != nil {
			t.Fatal(err)
		}
		got := bearers(mds())
		return len(got) > 0 && got[len(got)-1] == want
	})
}
