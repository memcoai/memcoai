package memcoai

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log/slog"
	"reflect"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/proto"

	authv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/auth/v1"
	"github.com/memcoai/memcoai/go/internal/testserver"
)

// issued is the last token request the fake received, failing t if none was.
func issued(t *testing.T, f *fixture) *authv1.IssueTokenRequest {
	t.Helper()
	request, ok := f.server.Tokens.Request("IssueToken").(*authv1.IssueTokenRequest)
	if !ok {
		t.Fatalf("no token was asked for; calls %v", f.server.Calls())
	}
	return request
}

// An issued token has no content role, so a ListDomains under it would only
// be refused: issuing it is what proves the credentials.
func TestConnectingWithClientCredentialsIssuesATokenAndCallsNoMemoryMethod(t *testing.T) {
	f := started(t, asAPIClient)
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if got := f.server.Calls(); !reflect.DeepEqual(got, []string{"Check", "IssueToken"}) {
		t.Fatalf("calls %v", got)
	}
	if !f.logs.Has("msg=connected") {
		t.Fatalf("logs %s", f.logs)
	}
}

func TestTheTokenRequestCarriesTheCredentialsAndNoBearer(t *testing.T) {
	f := started(t, asAPIClient)
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	request := issued(t, f)
	want := &authv1.IssueTokenRequest{GrantType: "client_credentials", ClientId: testClientID, ClientSecret: testClientSecret}
	if !proto.Equal(request, want) {
		t.Fatalf("sent %v", request)
	}
	if got := bearers(f.server.Tokens.Metadata()); !reflect.DeepEqual(got, []string{""}) {
		t.Fatalf("authorization %q", got)
	}
}

func TestATokenLifetimeIsSentAsTTLSeconds(t *testing.T) {
	f := started(t, asAPIClient, func(o *Options) { o.TokenLifetime = 90 * time.Minute })
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if got := issued(t, f).GetTtlSeconds(); got != 5400 {
		t.Fatalf("ttl_seconds %d", got)
	}
}

func TestALifetimeOverTheServicesCapIsTheServicesToRefuse(t *testing.T) {
	f := started(t, asAPIClient, func(o *Options) { o.TokenLifetime = 86401 * time.Second })
	f.server.Tokens.FailNext("IssueToken", testserver.Failure{Code: codes.InvalidArgument, Details: "ttl_seconds must be at most 86400"})
	invalid(t, f.client.Connect(ctx), "ttl_seconds must be at most 86400")
	if got := issued(t, f).GetTtlSeconds(); got != 86401 {
		t.Fatalf("ttl_seconds %d", got)
	}
}

func TestConnectingAgainReusesATokenThatIsNotDue(t *testing.T) {
	f := credentialed(t)
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if got := f.server.Calls(); !reflect.DeepEqual(got, []string{"Check"}) {
		t.Fatalf("calls %v", got)
	}
}

func TestAdministrationAndMemoryCallsCarryTheIssuedToken(t *testing.T) {
	f := credentialed(t)
	if _, err := f.client.Networks.List(ctx, ListNetworksParams{}); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.Memory.ListTools(ctx); err != nil {
		t.Fatal(err)
	}
	for _, got := range [][]string{bearers(f.server.Admin.Metadata()), bearers(f.server.Memory.Metadata())} {
		if !reflect.DeepEqual(got, []string{"Bearer client-token-1"}) {
			t.Fatalf("authorization %q", got)
		}
	}
}

func TestTheTokenIsRenewedAtFourFifthsOfItsLifetime(t *testing.T) {
	f, clock := timed(t)
	list := func() {
		t.Helper()
		if _, err := f.client.Networks.List(ctx, ListNetworksParams{}); err != nil {
			t.Fatal(err)
		}
	}
	list()
	clock.Advance(2879 * time.Second)
	list()
	if calls := f.server.Tokens.Calls(); len(calls) != 0 {
		t.Fatalf("renewed early: %v", calls)
	}
	clock.Advance(time.Second)
	// Due, and still serving while the renewal runs beside the calls.
	list()
	serving(t, "the renewed token", f.server.Admin.Metadata, func() error {
		_, err := f.client.Networks.List(ctx, ListNetworksParams{})
		return err
	}, "Bearer client-token-2")
	if got := bearers(f.server.Admin.Metadata()); !reflect.DeepEqual(got[:3], []string{
		"Bearer client-token-1", "Bearer client-token-1", "Bearer client-token-1",
	}) || len(f.server.Tokens.Calls()) != 1 {
		t.Fatalf("authorization %q; token calls %v", got, f.server.Tokens.Calls())
	}
}

func TestAFailedRenewalIsReturnedTypedAndTheNextCallTriesAgain(t *testing.T) {
	f, clock := timed(t)
	clock.Advance(time.Hour)
	f.server.Tokens.FailNext("IssueToken", testserver.Failure{Code: codes.Unavailable, Details: "token service down"})
	_, err := f.client.Networks.List(ctx, ListNetworksParams{})
	if got := as[*UnavailableError](t, err); got.Detail != "token service down" {
		t.Fatalf("got %+v", got)
	}
	if calls := f.server.Admin.Calls(); len(calls) != 0 {
		t.Fatalf("sent %v without a token", calls)
	}
	if _, err := f.client.Networks.List(ctx, ListNetworksParams{}); err != nil {
		t.Fatal(err)
	}
	if got := bearers(f.server.Admin.Metadata()); !reflect.DeepEqual(got, []string{"Bearer client-token-2"}) {
		t.Fatalf("authorization %q", got)
	}
}

func TestARejectedClientCredentialSurfacesFromConnect(t *testing.T) {
	f := started(t, asAPIClient)
	f.server.Tokens.FailNext("IssueToken", testserver.Failure{Code: codes.Unauthenticated, Details: "invalid client"})
	if got := as[*AuthenticationError](t, f.client.Connect(ctx)); got.Detail != "invalid client" {
		t.Fatalf("got %+v", got)
	}
	if got := f.server.Calls(); !reflect.DeepEqual(got, []string{"Check", "IssueToken"}) {
		t.Fatalf("calls %v", got)
	}
	if f.logs.Has("invalid client") {
		t.Fatalf("a returned error was also logged: %s", f.logs)
	}
}

func TestTheSecretAndTheTokenNeverReachALogOrAnError(t *testing.T) {
	f := started(t, asAPIClient)
	f.server.Tokens.FailNext("IssueToken", testserver.Failure{Code: codes.Unauthenticated, Details: "invalid client"})
	err := f.client.Connect(ctx)
	if err == nil {
		t.Fatal("control: the refusal was not staged")
	}
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.Networks.List(ctx, ListNetworksParams{}); err != nil {
		t.Fatal(err)
	}
	if !f.logs.Has("rpc=IssueToken") {
		t.Fatalf("control: nothing was logged at debug: %s", f.logs)
	}
	options := Options{ClientID: testClientID, ClientSecret: testClientSecret}
	var logged bytes.Buffer
	slog.New(slog.NewTextHandler(&logged, nil)).Info("x", "options", options)
	encoded, jsonErr := json.Marshal(options)
	if jsonErr != nil {
		t.Fatal(jsonErr)
	}
	for _, text := range []string{
		f.logs.String(), err.Error(), fmt.Sprintf("%+v %#v", err, err), logged.String(),
		fmt.Sprintf("%v %+v %#v %s", options, options, options, options), string(encoded),
	} {
		if strings.Contains(text, testClientSecret) || strings.Contains(text, "client-token-") {
			t.Errorf("a credential is visible in %q", text)
		}
	}
}

func TestClientCredentialsInTheEnvironmentWinOverATokenThere(t *testing.T) {
	t.Setenv("MEMCO_API_TOKEN", "env-token")
	t.Setenv("MEMCO_CLIENT_ID", "env-id")
	t.Setenv("MEMCO_CLIENT_SECRET", "env-secret")
	f := started(t, func(o *Options) { o.Token = "" })
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if got := issued(t, f).GetClientId(); got != "env-id" {
		t.Fatalf("client id %q", got)
	}
	if calls := f.server.Memory.Calls(); len(calls) != 0 {
		t.Fatalf("memory calls %v", calls)
	}
}

func TestATokenOptionIgnoresClientCredentialsInTheEnvironment(t *testing.T) {
	t.Setenv("MEMCO_CLIENT_ID", "env-id")
	t.Setenv("MEMCO_CLIENT_SECRET", "env-secret")
	f := started(t)
	if err := f.client.Connect(ctx); err != nil {
		t.Fatal(err)
	}
	if got := f.server.Calls(); !reflect.DeepEqual(got, []string{"Check", "ListDomains"}) {
		t.Fatalf("calls %v", got)
	}
}

const renewalFailed = "could not renew a credential; the current one serves until it expires"

// A token still has a fifth of its life left when it falls due, so a token
// service that is down then leaves it serving rather than failing calls.
func TestAFailedTokenRenewalIsRiddenOutOnTheCurrentTokenUntilItExpires(t *testing.T) {
	f, clock := timed(t)
	clock.Advance(3060 * time.Second) // 0.85 of the hour the token lives
	f.server.Tokens.FailNext("IssueToken", testserver.Failure{Code: codes.Unavailable, Details: "token service down"})
	if _, err := f.client.Networks.List(ctx, ListNetworksParams{}); err != nil {
		t.Fatal(err)
	}
	if got := bearers(f.server.Admin.Metadata()); !reflect.DeepEqual(got, []string{"Bearer client-token-1"}) {
		t.Fatalf("authorization %q", got)
	}
	eventually(t, "the failed renewal to be logged", func() bool { return f.logs.Has(renewalFailed) })
	if strings.Count(f.logs.String(), renewalFailed) != 1 || !f.logs.Has("token service down") {
		t.Fatalf("logs %s", f.logs)
	}
	clock.Advance(540 * time.Second) // 1.0
	f.server.Tokens.FailNext("IssueToken", testserver.Failure{Code: codes.Unavailable, Details: "token service down"})
	_, err := f.client.Networks.List(ctx, ListNetworksParams{})
	if got := as[*UnavailableError](t, err); got.Detail != "token service down" {
		t.Fatalf("got %+v", got)
	}
	if strings.Count(f.logs.String(), renewalFailed) != 1 {
		t.Fatalf("a returned error was also logged: %s", f.logs)
	}
}
