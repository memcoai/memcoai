package transport_test

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math/big"
	"net"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/config"
	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/memory"
	"github.com/memcoai/memcoai/go/internal/testserver"
	"github.com/memcoai/memcoai/go/internal/transport"
)

const token = "test-token"

func dial(t *testing.T, address string, plaintext bool) *grpc.ClientConn {
	t.Helper()
	cfg, err := config.Resolve(
		config.Input{Token: token, Host: address, Plaintext: plaintext, Timeout: 5 * time.Second},
		func(string) string { return "" },
		slog.New(slog.NewTextHandler(io.Discard, nil)),
	)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := transport.Dial(cfg, "memco-go/test", memory.Service)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return conn
}

func connected(t *testing.T) (*testserver.Harness, memoryv1.MemoryServiceClient, *grpc.ClientConn) {
	t.Helper()
	harness := testserver.Start(t)
	conn := dial(t, harness.Address, true)
	return harness, memoryv1.NewMemoryServiceClient(conn), conn
}

func callEvery(ctx context.Context, t *testing.T, client memoryv1.MemoryServiceClient) {
	t.Helper()
	calls := []func() error{
		func() error { _, err := client.ListDomains(ctx, &memoryv1.ListDomainsRequest{}); return err },
		func() error {
			_, err := client.StartSession(ctx, &memoryv1.StartSessionRequest{Domain: "d"})
			return err
		},
		func() error {
			_, err := client.Search(ctx, &memoryv1.SearchRequest{Query: "q", Domain: "d"})
			return err
		},
		func() error { _, err := client.GetMemory(ctx, &memoryv1.GetMemoryRequest{Idx: "i"}); return err },
		func() error { _, err := client.CreateMemory(ctx, &memoryv1.CreateMemoryRequest{}); return err },
		func() error { _, err := client.EnrichMemory(ctx, &memoryv1.EnrichMemoryRequest{}); return err },
		func() error { _, err := client.ShareFeedback(ctx, &memoryv1.ShareFeedbackRequest{}); return err },
		func() error { _, err := client.RevertMemory(ctx, &memoryv1.RevertMemoryRequest{OpId: "o"}); return err },
		func() error { _, err := client.ImportMemories(ctx, &memoryv1.ImportMemoriesRequest{}); return err },
		func() error { _, err := client.ListTools(ctx, &memoryv1.ListToolsRequest{}); return err },
	}
	for _, call := range calls {
		if err := call(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestEveryMemoryCallCarriesExactlyOneCredential(t *testing.T) {
	harness, client, _ := connected(t)
	callEvery(context.Background(), t, client)
	all := harness.Memory.Metadata()
	if len(all) != len(testserver.Methods) {
		t.Fatalf("recorded %d calls", len(all))
	}
	for i, md := range all {
		if got := md.Get(transport.AuthHeader); !reflect.DeepEqual(got, []string{"Bearer " + token}) {
			t.Errorf("%s: authorization %q", harness.Memory.Calls()[i], got)
		}
	}
}

func TestTheHealthProbeCarriesNoCredential(t *testing.T) {
	harness, _, conn := connected(t)
	ctx := metadata.AppendToOutgoingContext(context.Background(), "authorization", "Bearer caller")
	got, err := transport.Health(ctx, conn)
	if err != nil || got != grpc_health_v1.HealthCheckResponse_SERVING {
		t.Fatalf("control: the probe did not run: %v %v", got, err)
	}
	probes := harness.Health.Metadata()
	if len(probes) != 1 || !reflect.DeepEqual(harness.Health.Checked(), []string{""}) {
		t.Fatalf("probes %v checked %v", probes, harness.Health.Checked())
	}
	if values := probes[0].Get(transport.AuthHeader); len(values) != 0 {
		t.Fatalf("the probe carried %q", values)
	}
}

func TestCallerMetadataCannotDisplaceTheCredential(t *testing.T) {
	harness, client, _ := connected(t)
	caller := metadata.MD{"Authorization": []string{"Bearer forged"}, "x-other": []string{"kept"}}
	ctx := metadata.NewOutgoingContext(context.Background(), caller)
	ctx = metadata.AppendToOutgoingContext(ctx, "authorization", "Bearer a", "AUTHORIZATION", "Bearer b", "Authorization", "Bearer c")
	if _, err := client.ListDomains(ctx, &memoryv1.ListDomainsRequest{}); err != nil {
		t.Fatal(err)
	}
	md := harness.Memory.Metadata()[0]
	if got := md.Get(transport.AuthHeader); !reflect.DeepEqual(got, []string{"Bearer " + token}) {
		t.Fatalf("authorization %q", got)
	}
	if got := md.Get("x-other"); !reflect.DeepEqual(got, []string{"kept"}) {
		t.Fatalf("x-other %q", got)
	}
	if got := caller["Authorization"]; !reflect.DeepEqual(got, []string{"Bearer forged"}) {
		t.Fatalf("the caller's metadata was modified: %q", got)
	}
}

func TestTheUserAgentLeadsWithTheSDKs(t *testing.T) {
	harness, client, _ := connected(t)
	callEvery(context.Background(), t, client)
	for i, md := range harness.Memory.Metadata() {
		got := strings.Join(md.Get("user-agent"), " ")
		if !strings.HasPrefix(got, "memco-go/test grpc-go/") {
			t.Errorf("%s: user agent %q", harness.Memory.Calls()[i], got)
		}
	}
}

type jsonName struct {
	Service string `json:"service"`
	Method  string `json:"method"`
}

type jsonConfig struct {
	MethodConfig []struct {
		Name        []jsonName `json:"name"`
		RetryPolicy struct {
			MaxAttempts          int      `json:"maxAttempts"`
			InitialBackoff       string   `json:"initialBackoff"`
			MaxBackoff           string   `json:"maxBackoff"`
			BackoffMultiplier    float64  `json:"backoffMultiplier"`
			RetryableStatusCodes []string `json:"retryableStatusCodes"`
		} `json:"retryPolicy"`
	} `json:"methodConfig"`
}

func TestTheRetryPolicyCoversOnlyTheReadsThatMintNothing(t *testing.T) {
	var parsed jsonConfig
	if err := json.Unmarshal([]byte(transport.ServiceConfig(memory.Service)), &parsed); err != nil {
		t.Fatal(err)
	}
	if len(parsed.MethodConfig) != 1 {
		t.Fatalf("got %+v", parsed)
	}
	only := parsed.MethodConfig[0]
	want := []jsonName{
		{"memcoai.memory.v1.MemoryService", "ListDomains"},
		{"memcoai.memory.v1.MemoryService", "GetMemory"},
		{"memcoai.memory.v1.MemoryService", "ListTools"},
		{"grpc.health.v1.Health", "Check"},
	}
	if !reflect.DeepEqual(only.Name, want) {
		t.Fatalf("names %+v", only.Name)
	}
	policy := only.RetryPolicy
	if policy.MaxAttempts != 3 || policy.InitialBackoff != "0.1s" || policy.MaxBackoff != "1s" ||
		policy.BackoffMultiplier != 2 || !reflect.DeepEqual(policy.RetryableStatusCodes, []string{"UNAVAILABLE"}) {
		t.Fatalf("policy %+v", policy)
	}
	for _, name := range want[:3] {
		if !slices.ContainsFunc(memoryv1.MemoryService_ServiceDesc.Methods, func(m grpc.MethodDesc) bool {
			return m.MethodName == name.Method
		}) {
			t.Errorf("%s is not a method of the contract", name.Method)
		}
	}
	if "/"+want[3].Service+"/"+want[3].Method != grpc_health_v1.Health_Check_FullMethodName {
		t.Errorf("the health entry names %v", want[3])
	}
}

func TestEachServiceAddsItsOwnMethods(t *testing.T) {
	second := transport.Service{Name: "memcoai.users.v1.UserService", Retryable: []string{"GetUser"}}
	var parsed jsonConfig
	if err := json.Unmarshal([]byte(transport.ServiceConfig(memory.Service, second)), &parsed); err != nil {
		t.Fatal(err)
	}
	names := parsed.MethodConfig[0].Name
	if len(names) != 5 || names[3] != (jsonName{"memcoai.users.v1.UserService", "GetUser"}) {
		t.Fatalf("names %+v", names)
	}
}

func TestReconnectBackoffIsShortButDialsMayTakeTheirTime(t *testing.T) {
	params := transport.ConnectParams()
	if params.Backoff.BaseDelay != 200*time.Millisecond || params.Backoff.MaxDelay != 5*time.Second {
		t.Errorf("backoff %+v", params.Backoff)
	}
	if params.Backoff.Multiplier <= 1 || params.MinConnectTimeout != 20*time.Second {
		t.Errorf("params %+v", params)
	}
}

func counted(t *testing.T, name string, call func(memoryv1.MemoryServiceClient) error, failures int) (int, error) {
	t.Helper()
	harness, client, _ := connected(t)
	for range failures {
		harness.Memory.FailNext(name, testserver.Failure{Code: codes.Unavailable, Details: "try again"})
	}
	err := call(client)
	return len(harness.Memory.Calls()), err
}

var ctx = context.Background()

var calls = map[string]func(memoryv1.MemoryServiceClient) error{
	"ListDomains": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.ListDomains(ctx, &memoryv1.ListDomainsRequest{})
		return err
	},
	"GetMemory": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.GetMemory(ctx, &memoryv1.GetMemoryRequest{Idx: "i"})
		return err
	},
	"ListTools": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.ListTools(ctx, &memoryv1.ListToolsRequest{})
		return err
	},
	"Search": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.Search(ctx, &memoryv1.SearchRequest{})
		return err
	},
	"StartSession": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.StartSession(ctx, &memoryv1.StartSessionRequest{})
		return err
	},
	"CreateMemory": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.CreateMemory(ctx, &memoryv1.CreateMemoryRequest{})
		return err
	},
	"EnrichMemory": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.EnrichMemory(ctx, &memoryv1.EnrichMemoryRequest{})
		return err
	},
	"ShareFeedback": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.ShareFeedback(ctx, &memoryv1.ShareFeedbackRequest{})
		return err
	},
	"RevertMemory": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.RevertMemory(ctx, &memoryv1.RevertMemoryRequest{})
		return err
	},
	"ImportMemories": func(c memoryv1.MemoryServiceClient) error {
		_, err := c.ImportMemories(ctx, &memoryv1.ImportMemoriesRequest{})
		return err
	},
}

func TestReadsSurviveABlipAndGiveUpAfterThreeAttempts(t *testing.T) {
	for _, name := range []string{"ListDomains", "GetMemory", "ListTools"} {
		t.Run(name, func(t *testing.T) {
			if n, err := counted(t, name, calls[name], 1); err != nil || n != 2 {
				t.Errorf("one blip: %d calls, %v", n, err)
			}
			n, err := counted(t, name, calls[name], 5)
			if n != 3 || status.Code(err) != codes.Unavailable {
				t.Errorf("persistent: %d calls, %v", n, err)
			}
		})
	}
}

func TestNothingThatMintsIsRetried(t *testing.T) {
	for _, name := range []string{"Search", "StartSession", "CreateMemory", "EnrichMemory", "ShareFeedback", "RevertMemory", "ImportMemories"} {
		t.Run(name, func(t *testing.T) {
			n, err := counted(t, name, calls[name], 1)
			if n != 1 || status.Code(err) != codes.Unavailable {
				t.Errorf("%d calls, %v", n, err)
			}
		})
	}
}

func TestTheHealthProbeIsRetried(t *testing.T) {
	harness, _, conn := connected(t)
	harness.Health.FailNext(testserver.Failure{Code: codes.Unavailable, Details: "starting"})
	if _, err := transport.Health(context.Background(), conn); err != nil {
		t.Fatal(err)
	}
	if got := len(harness.Health.Checked()); got != 2 {
		t.Fatalf("%d probes", got)
	}
}

func faultCode(err error) codes.Code {
	var f *fault.Error
	if !errors.As(err, &f) {
		return codes.OK
	}
	return f.Code
}

func selfSigned(t *testing.T) tls.Certificate {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "127.0.0.1"},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
}

func TestTLSIsTheDefaultAndVerifiesTheServer(t *testing.T) {
	harness := testserver.StartTLS(t, selfSigned(t))
	conn := dial(t, harness.Address, false)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, err := transport.Health(ctx, conn)
	if faultCode(err) != codes.Unavailable {
		t.Fatalf("an untrusted certificate was accepted: %v", err)
	}
	if got := len(harness.Health.Checked()); got != 0 {
		t.Fatalf("the probe reached the server %d times", got)
	}
}

func TestPlaintextOnlyReachesAPlaintextServerWhenAsked(t *testing.T) {
	harness := testserver.Start(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, err := transport.Health(ctx, dial(t, harness.Address, false)); faultCode(err) != codes.Unavailable {
		t.Fatalf("TLS reached a plaintext server: %v", err)
	}
	if _, err := transport.Health(ctx, dial(t, harness.Address, true)); err != nil {
		t.Fatalf("plaintext: %v", err)
	}
}

func TestHealthReportsWhatTheServerSaid(t *testing.T) {
	harness, _, conn := connected(t)
	harness.Health.SetStatus(grpc_health_v1.HealthCheckResponse_NOT_SERVING)
	got, err := transport.Health(context.Background(), conn)
	if err != nil || got != grpc_health_v1.HealthCheckResponse_NOT_SERVING {
		t.Fatalf("got %v, %v", got, err)
	}
}

func TestStatusNamesIncludeOnesThisBuildDoesNotKnow(t *testing.T) {
	cases := map[grpc_health_v1.HealthCheckResponse_ServingStatus]string{
		grpc_health_v1.HealthCheckResponse_UNKNOWN:         "UNKNOWN",
		grpc_health_v1.HealthCheckResponse_SERVING:         "SERVING",
		grpc_health_v1.HealthCheckResponse_NOT_SERVING:     "NOT_SERVING",
		grpc_health_v1.HealthCheckResponse_SERVICE_UNKNOWN: "SERVICE_UNKNOWN",
		7: "UNRECOGNIZED(7)",
	}
	for value, want := range cases {
		if got := transport.StatusName(value); got != want {
			t.Errorf("%d: got %q", value, got)
		}
	}
}
