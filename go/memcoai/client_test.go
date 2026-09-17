package memcoai

import (
	"context"
	"errors"
	"net"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health/grpc_health_v1"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/logging"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

func TestNewClientSendsNothing(t *testing.T) {
	f := started(t)
	time.Sleep(50 * time.Millisecond)
	if len(f.server.Memory.Calls()) != 0 || len(f.server.Health.Checked()) != 0 {
		t.Fatalf("calls %v, probes %v", f.server.Memory.Calls(), f.server.Health.Checked())
	}
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if len(f.server.Memory.Calls()) == 0 {
		t.Fatal("control: Connect recorded nothing either")
	}
}

func TestSettingsAreRefusedAsConfigErrors(t *testing.T) {
	cases := map[string]Options{
		"no API token: pass token=... or set MEMCO_API_TOKEN":                                             {Host: "localhost:1"},
		"timeout must be positive, got -1s":                                                               {Token: "t", Timeout: -time.Second},
		`host "localhost:http" has an unparseable port: "http" is not an integer`:                         {Token: "t", Host: "localhost:http"},
		`"loud" is not a log level; use one of critical, debug, error, info, warning, or none to silence`: {Token: "t", LogLevel: "loud"},
	}
	t.Setenv("MEMCO_API_TOKEN", "")
	t.Setenv("MEMCO_API_KEY", "")
	for want, options := range cases {
		client, err := NewClient(options)
		if got := as[*ConfigError](t, err); got.Message != want || client != nil {
			t.Errorf("got %q, %v; want %q", got.Message, client, want)
		}
	}
}

func TestConnectProbesHealthThenListsDomains(t *testing.T) {
	f := started(t)
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(f.server.Health.Checked(), []string{""}) {
		t.Fatalf("probes %v", f.server.Health.Checked())
	}
	if !reflect.DeepEqual(f.server.Memory.Calls(), []string{"ListDomains"}) {
		t.Fatalf("calls %v", f.server.Memory.Calls())
	}
}

func TestAServiceThatIsNotServingIsUnhealthy(t *testing.T) {
	f := started(t)
	f.server.Health.SetStatus(grpc_health_v1.HealthCheckResponse_NOT_SERVING)
	err := f.client.Connect(ctx)
	got := as[*UnhealthyError](t, err)
	if got.Code != codes.Unavailable || got.Detail != f.server.Address+" reported health status NOT_SERVING" {
		t.Fatalf("got %+v", got)
	}
	if err.Error() != "UNAVAILABLE: "+f.server.Address+" reported health status NOT_SERVING" {
		t.Fatalf("message %q", err.Error())
	}
	if len(f.server.Memory.Calls()) != 0 {
		t.Fatalf("calls %v", f.server.Memory.Calls())
	}
	f.server.Health.SetStatus(7)
	if got := as[*UnhealthyError](t, f.client.Connect(ctx)); !strings.HasSuffix(got.Detail, "UNRECOGNIZED(7)") {
		t.Fatalf("got %q", got.Detail)
	}
}

func TestARejectedCredentialSurfacesFromConnect(t *testing.T) {
	f := started(t)
	f.server.Memory.Fail(&testserver.Failure{Code: codes.Unauthenticated, Details: "invalid credential"})
	err := f.client.Connect(ctx)
	if got := as[*AuthenticationError](t, err); got.Detail != "invalid credential" {
		t.Fatalf("got %+v", got)
	}
	if len(f.server.Health.Checked()) != 1 {
		t.Fatal("the probe did not run first")
	}
}

func TestConnectCanBeRetried(t *testing.T) {
	f := started(t)
	f.server.Memory.FailNext("ListDomains", testserver.Failure{Code: codes.PermissionDenied, Details: "not yet"})
	expect[*PermissionError](t, f.client.Connect(ctx))
	if err := f.client.Connect(ctx); err != nil {
		t.Fatalf("retry: %v", err)
	}
}

func TestTheHealthProbeIsRetriedButNotBeyondThreeAttempts(t *testing.T) {
	f := started(t)
	f.server.Health.FailNext(testserver.Failure{Code: codes.Unavailable, Details: "starting"})
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	f = started(t)
	for range 3 {
		f.server.Health.FailNext(testserver.Failure{Code: codes.Unavailable, Details: "down"})
	}
	if got := as[*UnavailableError](t, f.client.Connect(ctx)); got.Detail != "down" {
		t.Fatalf("got %+v", got)
	}
	if n := len(f.server.Health.Checked()); n != 3 {
		t.Fatalf("%d probes", n)
	}
}

func TestAnUnreachableServiceIsUnavailable(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	_ = listener.Close()
	client, err := NewClient(Options{Token: testToken, Host: address, Plaintext: true, Timeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	closeLater(t, client)
	expect[*UnavailableError](t, client.Connect(ctx))
}

func TestCloseIsIdempotentAndRefusesLaterCalls(t *testing.T) {
	f := connected(t)
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	if err := f.client.Close(ctx); err != nil {
		t.Fatal(err)
	}
	_, err := f.client.Memory.ListDomains(ctx)
	if got := as[*ConfigError](t, err); got.Message != closedMessage {
		t.Fatalf("got %q", got.Message)
	}
	if got := as[*ConfigError](t, f.client.Connect(ctx)); got.Message != closedMessage {
		t.Fatalf("got %q", got.Message)
	}
	if len(f.server.Memory.Calls()) != 0 || len(f.server.Health.Checked()) != 0 {
		t.Fatalf("calls %v, probes %v", f.server.Memory.Calls(), f.server.Health.Checked())
	}
}

func TestCloseWaitsForCallsInFlight(t *testing.T) {
	f := connected(t)
	f.server.Memory.Delay("Search", 300*time.Millisecond)
	done := make(chan error, 1)
	go func() {
		_, err := f.client.Memory.Search(ctx, "q", SearchParams{Domain: "coding"})
		done <- err
	}()
	for len(f.server.Memory.Calls()) == 0 {
		time.Sleep(5 * time.Millisecond)
	}
	var closers sync.WaitGroup
	closed := make(chan struct{}, 2)
	for range 2 {
		closers.Go(func() {
			if err := f.client.Close(ctx); err != nil {
				t.Error(err)
			}
			closed <- struct{}{}
		})
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("the call in flight failed: %v", err)
		}
	case <-closed:
		t.Fatal("Close returned before the call in flight finished")
	}
	closers.Wait()
}

func TestCloseStopsWaitingWhenItsContextEnds(t *testing.T) {
	f := connected(t)
	f.server.Memory.Delay("Search", 5*time.Second)
	done := make(chan error, 1)
	go func() {
		_, err := f.client.Memory.Search(ctx, "q", SearchParams{Domain: "coding"})
		done <- err
	}()
	for len(f.server.Memory.Calls()) == 0 {
		time.Sleep(5 * time.Millisecond)
	}
	short, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()
	err := f.client.Close(short)
	expect[*TimeoutError](t, err)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v", err)
	}
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("the call in flight succeeded on a closed connection")
		}
		expect[Error](t, err)
	case <-time.After(2 * time.Second):
		t.Fatal("the connection was not closed")
	}

	cancelled := connected(t)
	cancelled.server.Memory.Delay("Search", 5*time.Second)
	go func() { _, _ = cancelled.client.Memory.Search(ctx, "q", SearchParams{Domain: "coding"}) }()
	for len(cancelled.server.Memory.Calls()) == 0 {
		time.Sleep(5 * time.Millisecond)
	}
	stop, cancelNow := context.WithCancel(ctx)
	cancelNow()
	err = cancelled.client.Close(stop)
	if got := as[*InternalError](t, err); got.Code != codes.Canceled || !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}

func TestLimitsLearnedAtConnectAreEnforcedLocally(t *testing.T) {
	f := started(t)
	f.server.Memory.Respond("ListDomains", &memoryv1.ListDomainsResponse{Limits: &memoryv1.Limits{MaxQueryCharacters: 3}})
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	f.server.Forget()
	_, err := f.client.Memory.Search(ctx, "four", SearchParams{Domain: "coding"})
	invalid(t, err, "query is 4 characters, which exceeds the limit of 3")
	if len(f.server.Memory.Calls()) != 0 {
		t.Fatalf("calls %v", f.server.Memory.Calls())
	}
}

func TestADeadlineShorterThanTheDefaultWins(t *testing.T) {
	f := connected(t)
	f.server.Memory.Delay("ListDomains", 2*time.Second)
	short, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, err := f.client.Memory.ListDomains(short)
	expect[*TimeoutError](t, err)
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Fatalf("took %v", elapsed)
	}
}

func TestADeadlineLongerThanTheDefaultWins(t *testing.T) {
	f := connected(t, func(o *Options) { o.Timeout = 50 * time.Millisecond })
	f.server.Memory.Delay("ListDomains", 200*time.Millisecond)
	long, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	if _, err := f.client.Memory.ListDomains(long); err != nil {
		t.Fatalf("the default cut a longer deadline short: %v", err)
	}
}

func TestWithoutADeadlineTheDefaultApplies(t *testing.T) {
	f := connected(t, func(o *Options) { o.Timeout = 50 * time.Millisecond })
	f.server.Memory.Delay("ListDomains", 500*time.Millisecond)
	_, err := f.client.Memory.ListDomains(ctx)
	expect[*TimeoutError](t, err)
}

func TestAnEndedContextSendsNothing(t *testing.T) {
	f := connected(t)
	if _, err := f.client.Memory.ListDomains(ctx); err != nil || len(f.server.Memory.Calls()) != 1 {
		t.Fatalf("control: a live context sent nothing: %v", err)
	}
	f.server.Forget()
	expired, cancel := context.WithTimeout(ctx, 0)
	defer cancel()
	<-expired.Done()
	_, err := f.client.Memory.ListDomains(expired)
	expect[*TimeoutError](t, err)
	cancelled, cancelNow := context.WithCancel(ctx)
	cancelNow()
	_, err = f.client.Memory.Search(cancelled, "q", SearchParams{Domain: "d"})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	if len(f.server.Memory.Calls()) != 0 {
		t.Fatalf("calls %v", f.server.Memory.Calls())
	}
}

func TestAVersionPastItsSunsetIsReportedAtConnect(t *testing.T) {
	f := started(t)
	f.server.Memory.Fail(&testserver.Failure{
		Code: codes.FailedPrecondition, Details: "upgrade the SDK",
		Reason: "CLIENT_VERSION_SUNSET", Domain: "memco.ai",
	})
	err := f.client.Connect(ctx)
	if got := as[*SunsetError](t, err); got.Kind != SunsetClientVersion || got.Detail != "upgrade the SDK" {
		t.Fatalf("got %+v", got)
	}
	expect[*PreconditionFailedError](t, err)
}

func TestTheUserAgentNamesThisSDK(t *testing.T) {
	if !regexp.MustCompile(`^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$`).MatchString(Version) {
		t.Fatalf("Version %q is not semver", Version)
	}
	f := connected(t)
	if _, err := f.client.Memory.StartSession(ctx, "coding"); err != nil {
		t.Fatal(err)
	}
	for _, md := range f.server.Memory.Metadata() {
		if got := strings.Join(md.Get("user-agent"), " "); !strings.HasPrefix(got, "memco-go/"+Version+" grpc-go/") {
			t.Fatalf("user agent %q", got)
		}
	}
}

func TestProvenanceIsReadWithoutTheService(t *testing.T) {
	f := started(t)
	got, err := f.client.Provenance()
	if err != nil || got.ServerCommit == "" {
		t.Fatalf("got %+v, %v", got, err)
	}
	if len(f.server.Memory.Calls()) != 0 {
		t.Fatal("provenance called the service")
	}
}

func TestLogLevelSetsTheDedicatedLoggersLevel(t *testing.T) {
	t.Cleanup(func() { _ = logging.SetLevel("info") })
	client, err := NewClient(Options{Token: testToken, LogLevel: "debug"})
	if err != nil {
		t.Fatal(err)
	}
	closeLater(t, client)
	if !logging.Default().Enabled(ctx, -4) {
		t.Fatal("LogLevel did not reach the SDK's logger")
	}
	if err := SetLevel("none"); err != nil {
		t.Fatal(err)
	}
	if logging.Default().Enabled(ctx, logging.LevelCritical) {
		t.Fatal("SetLevel(none) did not silence the SDK's logger")
	}
	expect[*ConfigError](t, SetLevel("loud"))
}

// With nothing in flight there is nothing to wait for, so an ended context is
// no reason to report a failure — on the first Close or any later one.
func TestCloseWithNothingInFlightSucceedsWhateverItsContext(t *testing.T) {
	ended, cancel := context.WithCancel(context.Background())
	cancel()
	for range 100 {
		client, err := NewClient(Options{Token: testToken, Host: "localhost:1", Plaintext: true})
		if err != nil {
			t.Fatal(err)
		}
		if err := client.Close(ended); err != nil {
			t.Fatalf("first close: %v", err)
		}
		if err := client.Close(ended); err != nil {
			t.Fatalf("later close: %v", err)
		}
	}
}
