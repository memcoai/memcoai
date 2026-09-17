package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health/grpc_health_v1"

	"github.com/memcoai/memcoai/go/examples/internal/exampletest"
	"github.com/memcoai/memcoai/go/internal/testserver"
	"github.com/memcoai/memcoai/go/memcoai"
)

func TestMain(m *testing.M) {
	backoff = time.Millisecond
	m.Run()
}

func TestEachConnectFailureIsReported(t *testing.T) {
	cases := map[string]struct {
		stage func(*exampletest.Run)
		want  string
	}{
		"config": {
			func(r *exampletest.Run) { r.Options.Host = " " },
			"configuration problem: the host passed to the client is blank",
		},
		"authentication": {
			func(r *exampletest.Run) {
				r.Server.Memory.FailNext("ListDomains", testserver.Failure{Code: codes.Unauthenticated, Details: "no"})
			},
			"credential rejected; check MEMCO_API_TOKEN is current",
		},
		"api sunset": {
			func(r *exampletest.Run) {
				r.Server.Memory.FailNext("ListDomains", testserver.Failure{Code: codes.FailedPrecondition, Details: "move to v2", Reason: "API_VERSION_SUNSET", Domain: "memco.ai"})
			},
			"API version no longer served: move to v2",
		},
		"client sunset": {
			func(r *exampletest.Run) {
				r.Server.Memory.FailNext("ListDomains", testserver.Failure{Code: codes.FailedPrecondition, Details: "upgrade", Reason: "CLIENT_VERSION_SUNSET", Domain: "memco.ai"})
			},
			"upgrade required: upgrade",
		},
		"precondition": {
			func(r *exampletest.Run) {
				r.Server.Memory.FailNext("ListDomains", testserver.Failure{Code: codes.FailedPrecondition, Details: "billing disabled"})
			},
			"precondition unmet: billing disabled",
		},
		"timeout": {
			func(r *exampletest.Run) {
				r.Server.Health.FailNext(testserver.Failure{Code: codes.DeadlineExceeded, Details: "slow"})
			},
			"timed out reaching the service: DEADLINE_EXCEEDED: slow",
		},
		"unhealthy": {
			func(r *exampletest.Run) { r.Server.Health.SetStatus(grpc_health_v1.HealthCheckResponse_NOT_SERVING) },
			"service is not serving: UNAVAILABLE: ",
		},
		"unavailable": {
			func(r *exampletest.Run) {
				down := testserver.Failure{Code: codes.Unavailable, Details: "down"}
				r.Server.Health.FailNext(down, down, down)
			},
			"unreachable: UNAVAILABLE: down",
		},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			r := exampletest.Start(t)
			c.stage(r)
			client, err := connect(context.Background(), r.Options, r.Out)
			if client != nil || err != nil {
				t.Fatalf("got %v, %v", client, err)
			}
			r.Expect(t, c.want)
		})
	}
}

func TestAConnectFailureItDoesNotModelIsReturned(t *testing.T) {
	r := exampletest.Start(t)
	r.Server.Memory.FailNext("ListDomains", testserver.Failure{Code: codes.PermissionDenied, Details: "no"})
	client, err := connect(context.Background(), r.Options, r.Out)
	var denied *memcoai.PermissionError
	if client != nil || !errors.As(err, &denied) {
		t.Fatalf("got %v, %v", client, err)
	}
	if r.Printed() != "" {
		t.Fatalf("printed %q", r.Printed())
	}
}

func connected(t *testing.T) (*exampletest.Run, *memcoai.Client) {
	t.Helper()
	r := exampletest.Start(t)
	client, err := connect(context.Background(), r.Options, r.Out)
	if client == nil {
		t.Fatalf("not connected: %v\n%s", err, r.Printed())
	}
	t.Cleanup(func() { _ = client.Close(context.Background()) })
	r.Server.Forget()
	return r, client
}

func searches(r *exampletest.Run) int {
	n := 0
	for _, call := range r.Server.Memory.Calls() {
		if call == "Search" {
			n++
		}
	}
	return n
}

func TestEachSearchFailureIsHandledAsItDeserves(t *testing.T) {
	cases := map[string]struct {
		failures []testserver.Failure
		want     []string
		calls    int
	}{
		"invalid": {
			[]testserver.Failure{{Code: codes.InvalidArgument, Details: "query too long"}},
			[]string{"bad request: query too long"}, 1,
		},
		"authentication": {
			[]testserver.Failure{{Code: codes.Unauthenticated}},
			[]string{"credential rejected; check MEMCO_API_TOKEN is current"}, 1,
		},
		"permission": {
			[]testserver.Failure{{Code: codes.PermissionDenied}},
			[]string{"credential lacks permission for this operation"}, 1,
		},
		"not found": {
			[]testserver.Failure{{Code: codes.NotFound}},
			[]string{"nothing there"}, 1,
		},
		"quota": {
			[]testserver.Failure{{Code: codes.ResourceExhausted, Details: "monthly quota reached"}},
			[]string{"usage quota exhausted; retrying will not help"}, 1,
		},
		"rate limit": {
			[]testserver.Failure{{Code: codes.ResourceExhausted, Details: "rate limit"}, {Code: codes.ResourceExhausted, Details: "rate limit"}},
			[]string{"rate limited; retrying in 1ms", "rate limited; retrying in 2ms", "0 memories"}, 3,
		},
		"timeout": {
			[]testserver.Failure{{Code: codes.DeadlineExceeded}},
			[]string{"timed out; give the call a longer deadline"}, 1,
		},
		"unavailable": {
			[]testserver.Failure{{Code: codes.Unavailable}, {Code: codes.Unavailable}, {Code: codes.Unavailable}},
			[]string{"service unavailable; retrying in 1ms", "retrying in 2ms", "retrying in 4ms", "gave up after 3 attempts"}, 3,
		},
		"anything else": {
			[]testserver.Failure{{Code: codes.Internal, Details: "boom"}},
			[]string{"unexpected INTERNAL: boom"}, 1,
		},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			r, client := connected(t)
			r.Server.Memory.FailNext("Search", c.failures...)
			if err := searchWithRetry(context.Background(), client, "q", "coding", r.Out); err != nil {
				t.Fatal(err)
			}
			r.Expect(t, c.want...)
			if got := searches(r); got != c.calls {
				t.Fatalf("%d searches", got)
			}
		})
	}
}

func TestASearchFailureItDoesNotModelIsReturned(t *testing.T) {
	r, client := connected(t)
	if err := client.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	var config *memcoai.ConfigError
	if err := searchWithRetry(context.Background(), client, "q", "coding", r.Out); !errors.As(err, &config) {
		t.Fatalf("got %v", err)
	}
}

func TestTheCallersContextEndingIsReturnedNotRetried(t *testing.T) {
	r, client := connected(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := searchWithRetry(ctx, client, "q", "coding", r.Out); !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	if got := searches(r); got != 0 {
		t.Fatalf("%d searches", got)
	}
}

func TestAWaitIsCutShortByTheContext(t *testing.T) {
	r, client := connected(t)
	backoff = time.Hour
	t.Cleanup(func() { backoff = time.Millisecond })
	r.Server.Memory.FailNext("Search", testserver.Failure{Code: codes.Unavailable})
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	if err := searchWithRetry(ctx, client, "q", "coding", r.Out); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v", err)
	}
}

func TestTheRunEndsWithALocalRefusal(t *testing.T) {
	r := exampletest.Start(t)
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	r.Expect(t, "0 memories", "caught locally, nothing sent: query must not be empty")
	if got := searches(r); got != 1 {
		t.Fatalf("%d searches", got)
	}
}

func TestARunThatCannotConnectEndsQuietly(t *testing.T) {
	r := exampletest.Start(t)
	r.Options.Host = " "
	if err := run(context.Background(), r.Options, r.Out); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(r.Printed(), "configuration problem") {
		t.Fatalf("printed %q", r.Printed())
	}
}
