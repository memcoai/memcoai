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
