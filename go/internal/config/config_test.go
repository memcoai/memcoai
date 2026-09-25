package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/warn"
)

const legacyWarning = "MEMCO_API_KEY is deprecated and will be removed in a future release; rename it to MEMCO_API_TOKEN."

type environment map[string]string

func (e environment) get(name string) string { return e[name] }

func recorder(t *testing.T) (*slog.Logger, *bytes.Buffer) {
	t.Helper()
	warn.Reset()
	t.Cleanup(warn.Reset)
	var buffer bytes.Buffer
	return slog.New(slog.NewTextHandler(&buffer, &slog.HandlerOptions{Level: slog.LevelDebug})), &buffer
}

func resolved(t *testing.T, in Input, env environment) (*Config, string) {
	t.Helper()
	log, out := recorder(t)
	config, err := Resolve(in, env.get, log)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	return config, out.String()
}

func refusal(t *testing.T, in Input, env environment) string {
	t.Helper()
	log, _ := recorder(t)
	config, err := Resolve(in, env.get, log)
	var f *fault.Error
	if !errors.As(err, &f) || f.Kind != fault.KindConfig {
		t.Fatalf("got %+v, %v; want a config error", config, err)
	}
	return f.Detail
}

var withToken = environment{TokenEnv: "env-token"}

func TestAnExplicitTokenWins(t *testing.T) {
	config, out := resolved(t, Input{Token: "  arg-token  "}, withToken)
	if config.Credential.Reveal() != "arg-token" {
		t.Fatalf("got %q", config.Credential.Reveal())
	}
	if !strings.Contains(out, `msg="credential taken from the token argument"`) {
		t.Fatalf("got %q", out)
	}
}

func TestTheEnvironmentTokenIsTrimmed(t *testing.T) {
	config, out := resolved(t, Input{}, environment{TokenEnv: "  env-token\n"})
	if config.Credential.Reveal() != "env-token" {
		t.Fatalf("got %q", config.Credential.Reveal())
	}
	if !strings.Contains(out, `msg="credential taken from MEMCO_API_TOKEN"`) {
		t.Fatalf("got %q", out)
	}
}

func TestABlankTokenArgumentIsRefusedRatherThanReplaced(t *testing.T) {
	got := refusal(t, Input{Token: "   "}, withToken)
	if got != "the token passed to the client is blank: pass a real token or set MEMCO_API_TOKEN" {
		t.Fatalf("got %q", got)
	}
}

func TestAMissingTokenIsRefused(t *testing.T) {
	got := refusal(t, Input{}, environment{TokenEnv: "  "})
	if got != "no API token: pass token=... or set MEMCO_API_TOKEN" {
		t.Fatalf("got %q", got)
	}
}

func TestTheLegacyKeyStillWorksAndWarnsOnce(t *testing.T) {
	log, out := recorder(t)
	env := environment{LegacyTokenEnv: " legacy "}
	for range 2 {
		config, err := Resolve(Input{}, env.get, log)
		if err != nil {
			t.Fatal(err)
		}
		if config.Credential.Reveal() != "legacy" {
			t.Fatalf("got %q", config.Credential.Reveal())
		}
	}
	if got := strings.Count(out.String(), legacyWarning); got != 1 {
		t.Fatalf("warned %d times: %q", got, out.String())
	}
	if !strings.Contains(out.String(), "warning=DeprecationWarning") ||
		!strings.Contains(out.String(), `msg="credential taken from MEMCO_API_KEY"`) {
		t.Fatalf("got %q", out.String())
	}
}

func TestTheNewKeyWinsOverTheLegacyOneWithoutAWarning(t *testing.T) {
	config, out := resolved(t, Input{}, environment{TokenEnv: "new", LegacyTokenEnv: "old"})
	if config.Credential.Reveal() != "new" || strings.Contains(out, "deprecated") {
		t.Fatalf("got %q, %q", config.Credential.Reveal(), out)
	}
}

func TestATokenThatCannotBeAHeaderIsRefusedWithoutEchoingIt(t *testing.T) {
	for token, index := range map[string]int{
		"sk-live\u200bsupersecret": 7,
		"sk-live\nsupersecret":     7,
		"sk-livé-supersecret":      6,
	} {
		got := refusal(t, Input{Token: token}, nil)
		want := fmt.Sprintf("the token cannot be sent as an HTTP header: it must be printable ASCII, and the character at index %d is not", index)
		if got != want {
			t.Errorf("%q: got %q", token, got)
		}
		if strings.Contains(got, "sk-live") || strings.Contains(got, "supersecret") {
			t.Errorf("%q: the message echoes the token", token)
		}
	}
	if _, out := resolved(t, Input{Token: "sk-live_AB.cd-09+/=~"}, nil); strings.Contains(out, "sk-live") {
		t.Fatalf("the token reached a record: %q", out)
	}
}

func TestTheEndpointDefaults(t *testing.T) {
	config, out := resolved(t, Input{}, withToken)
	if config.Host != "grpc.memco.ai" || config.Port != 443 || config.Target() != "grpc.memco.ai:443" {
		t.Fatalf("got %+v", config)
	}
	if !config.TLS || config.Timeout != 30*time.Second {
		t.Fatalf("got TLS %v, timeout %v", config.TLS, config.Timeout)
	}
	if !strings.Contains(out, "msg=endpoint target=grpc.memco.ai:443 tls=true source=\"the default\"") {
		t.Fatalf("got %q", out)
	}
}

func TestTheHostComesFromTheArgumentThenTheEnvironment(t *testing.T) {
	config, out := resolved(t, Input{Host: "arg.example:1"}, environment{TokenEnv: "t", HostEnv: "env.example:2"})
	if config.Target() != "arg.example:1" || !strings.Contains(out, `source="the host argument"`) {
		t.Fatalf("got %q, %q", config.Target(), out)
	}
	config, out = resolved(t, Input{}, environment{TokenEnv: "t", HostEnv: " env.example:2 "})
	if config.Target() != "env.example:2" || !strings.Contains(out, "source=MEMCO_API_HOST") {
		t.Fatalf("got %q, %q", config.Target(), out)
	}
	config, _ = resolved(t, Input{}, environment{TokenEnv: "t", HostEnv: "   "})
	if config.Target() != "grpc.memco.ai:443" {
		t.Fatalf("a blank MEMCO_API_HOST is not treated as unset: %q", config.Target())
	}
}

func TestABlankHostArgumentIsRefused(t *testing.T) {
	got := refusal(t, Input{Host: "  "}, withToken)
	if got != "the host passed to the client is blank: pass a real host or set MEMCO_API_HOST" {
		t.Fatalf("got %q", got)
	}
}

func TestHostsAreSplitIntoNameAndPort(t *testing.T) {
	cases := []struct {
		host, name string
		port       int
		target     string
	}{
		{"localhost", "localhost", 443, "localhost:443"},
		{"localhost:50051", "localhost", 50051, "localhost:50051"},
		{" localhost:50051 ", "localhost", 50051, "localhost:50051"},
		{"[::1]", "::1", 443, "[::1]:443"},
		{"[::1]:50051", "::1", 50051, "[::1]:50051"},
		{"2001:db8::1", "2001:db8::1", 443, "[2001:db8::1]:443"},
		{"host:+50051", "host", 50051, "host:50051"},
		{"host: 50051", "host", 50051, "host:50051"},
	}
	for _, c := range cases {
		config, _ := resolved(t, Input{Host: c.host}, withToken)
		if config.Host != c.name || config.Port != c.port || config.Target() != c.target {
			t.Errorf("%q: got %s %d %s", c.host, config.Host, config.Port, config.Target())
		}
	}
}

func TestMalformedHostsAreRefused(t *testing.T) {
	cases := map[string]string{
		"[::1":                      `host "[::1" opens a bracket that is never closed`,
		"[]:443":                    `host "[]:443" has no address inside its brackets`,
		"[::1]x":                    `host "[::1]x" has unexpected text after the closing bracket`,
		":50051":                    `host ":50051" has a port but no hostname`,
		" :443":                     `host ":443" has a port but no hostname`,
		"host:http":                 `host "host:http" has an unparseable port: "http" is not an integer`,
		"host: 5e3":                 `host "host: 5e3" has an unparseable port: " 5e3" is not an integer`,
		"host:":                     `host "host:" has an unparseable port: "" is not an integer`,
		"https://grpc.memco.ai":     `host "https://grpc.memco.ai" has an unparseable port: "//grpc.memco.ai" is not an integer`,
		"host:0":                    `host "host:0" has a port outside the range 1-65535: 0`,
		"host:99999":                `host "host:99999" has a port outside the range 1-65535: 99999`,
		"host:99999999999999999999": `host "host:99999999999999999999" has a port outside the range 1-65535: 99999999999999999999`,
		"[::1]:-1":                  `host "[::1]:-1" has a port outside the range 1-65535: -1`,
	}
	for host, want := range cases {
		if got := refusal(t, Input{Host: host}, withToken); got != want {
			t.Errorf("%q: got %q, want %q", host, got, want)
		}
	}
}

func TestTheTimeoutIsCheckedBeforeTheToken(t *testing.T) {
	if got := refusal(t, Input{Timeout: -time.Second}, nil); got != "timeout must be positive, got -1s" {
		t.Fatalf("got %q", got)
	}
	config, _ := resolved(t, Input{Timeout: 5 * time.Second}, withToken)
	if config.Timeout != 5*time.Second {
		t.Fatalf("got %v", config.Timeout)
	}
}

func TestPlaintextOnlyWhenAsked(t *testing.T) {
	config, out := resolved(t, Input{Plaintext: true}, withToken)
	if config.TLS || !strings.Contains(out, "tls=false") {
		t.Fatalf("got %+v, %q", config, out)
	}
}

func TestTheDialTargetCannotBeReadAsAScheme(t *testing.T) {
	for host, want := range map[string]string{
		"unix:443":         "dns:///unix:443",
		"passthrough:1":    "dns:///passthrough:1",
		"grpc.memco.ai":    "dns:///grpc.memco.ai:443",
		"[fe80::1%eth0]:1": "dns:///%5Bfe80::1%25eth0%5D:1",
	} {
		config, _ := resolved(t, Input{Host: host}, withToken)
		if got := config.DialTarget(); got != want {
			t.Errorf("%q: got %q, want %q", host, got, want)
		}
		conn, err := grpc.NewClient(config.DialTarget(), grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			t.Errorf("%q: grpc.NewClient: %v", host, err)
			continue
		}
		_ = conn.Close()
	}
}

func TestTheTokenIsInNoRendering(t *testing.T) {
	config, _ := resolved(t, Input{Token: "sk-secret-token"}, nil)
	if config.Credential.Reveal() != "sk-secret-token" {
		t.Fatal("control: the transport can no longer read the token")
	}
	var renderings []string
	for _, verb := range []string{"%v", "%+v", "%#v", "%s", "%q", "%x"} {
		renderings = append(renderings,
			fmt.Sprintf(verb, config), fmt.Sprintf(verb, *config),
			fmt.Sprintf(verb, config.Credential), fmt.Sprintf(verb, *config.Credential))
	}
	encoded, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	renderings = append(renderings, string(encoded))
	var buffer bytes.Buffer
	slog.New(slog.NewTextHandler(&buffer, nil)).Info("x", "credential", config.Credential, "config", config)
	slog.New(slog.NewJSONHandler(&buffer, nil)).Info("x", "credential", config.Credential)
	renderings = append(renderings, buffer.String())
	for _, rendered := range renderings {
		if strings.Contains(rendered, "sk-secret-token") || strings.Contains(rendered, "736b2d") {
			t.Errorf("the token is visible in %q", rendered)
		}
	}
}

var withPair = environment{ClientIDEnv: " env-id ", ClientSecretEnv: " env-secret\n"}

func TestClientCredentialsComeFromTheOptions(t *testing.T) {
	config, out := resolved(t, Input{ClientID: " arg-id ", ClientSecret: " arg-secret "}, withPair)
	if config.ClientID != "arg-id" || config.ClientSecret.Reveal() != "arg-secret" || config.Credential != nil {
		t.Fatalf("got %+v", config)
	}
	if !strings.Contains(out, `msg="client credentials taken from the client_id and client_secret arguments"`) {
		t.Fatalf("got %q", out)
	}
}

func TestClientCredentialsInTheEnvironmentWinOverATokenThere(t *testing.T) {
	config, out := resolved(t, Input{}, environment{
		TokenEnv: "env-token", ClientIDEnv: " env-id ", ClientSecretEnv: " env-secret\n",
	})
	if config.ClientID != "env-id" || config.ClientSecret.Reveal() != "env-secret" || config.Credential != nil {
		t.Fatalf("got %+v", config)
	}
	if !strings.Contains(out, `msg="client credentials taken from MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET"`) {
		t.Fatalf("got %q", out)
	}
}

func TestATokenOptionIgnoresClientCredentialsInTheEnvironment(t *testing.T) {
	for name, env := range map[string]environment{
		"a pair":      withPair,
		"half a pair": {ClientIDEnv: "env-id"},
	} {
		config, _ := resolved(t, Input{Token: "arg-token"}, env)
		if config.Credential.Reveal() != "arg-token" || config.ClientSecret != nil {
			t.Errorf("%s: got %+v", name, config)
		}
	}
}

func TestClientCredentialsSkipTheLegacyTokenAndItsWarning(t *testing.T) {
	_, out := resolved(t, Input{}, environment{LegacyTokenEnv: "legacy", ClientIDEnv: "id", ClientSecretEnv: "secret"})
	if strings.Contains(out, LegacyTokenEnv) {
		t.Fatalf("the legacy variable was read: %q", out)
	}
}

func TestABlankPairInTheEnvironmentReadsAsUnset(t *testing.T) {
	config, _ := resolved(t, Input{}, environment{TokenEnv: "env-token", ClientIDEnv: "  ", ClientSecretEnv: ""})
	if config.Credential.Reveal() != "env-token" || config.ClientSecret != nil {
		t.Fatalf("got %+v", config)
	}
}

func TestClientCredentialsThatCannotStandAreRefused(t *testing.T) {
	cases := map[string]struct {
		in  Input
		env environment
	}{
		"pass either token=... or client_id=... with client_secret=..., not both": {
			Input{Token: "t", ClientID: "id", ClientSecret: "secret"}, nil,
		},
		"client credentials need both client_id and client_secret; client_secret is missing": {
			Input{ClientID: "id"}, withToken,
		},
		"client credentials need both client_id and client_secret; client_id is missing": {
			Input{ClientSecret: "secret"}, withToken,
		},
		"the client_id passed to the client is blank":             {Input{ClientID: " ", ClientSecret: "secret"}, nil},
		"the client_secret passed to the client is blank":         {Input{ClientID: "id", ClientSecret: "\t"}, nil},
		"the client_secret cannot be sent: it is not valid UTF-8": {Input{ClientID: "id", ClientSecret: "bad\xff"}, nil},
		"MEMCO_CLIENT_ID or MEMCO_CLIENT_SECRET cannot be sent: it is not valid UTF-8": {
			Input{}, environment{ClientIDEnv: "id", ClientSecretEnv: "bad\xff"},
		},
		"MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET must be set together; MEMCO_CLIENT_SECRET is not": {
			Input{}, environment{TokenEnv: "t", ClientIDEnv: "id"},
		},
		"MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET must be set together; MEMCO_CLIENT_ID is not": {
			Input{}, environment{ClientSecretEnv: "secret"},
		},
	}
	for want, c := range cases {
		if got := refusal(t, c.in, c.env); got != want {
			t.Errorf("got %q, want %q", got, want)
		}
	}
}

func TestATokenLifetimeNeedsClientCredentials(t *testing.T) {
	for _, in := range []Input{{Token: "t", TokenLifetime: time.Hour}, {TokenLifetime: time.Hour}} {
		if got := refusal(t, in, withToken); got != "token_lifetime applies only to client credentials, not a token" {
			t.Errorf("%+v: got %q", in, got)
		}
	}
	config, _ := resolved(t, Input{TokenLifetime: 90 * time.Minute}, withPair)
	if config.TokenLifetime != 90*time.Minute {
		t.Fatalf("got %v", config.TokenLifetime)
	}
}

func TestATokenLifetimeIsAPositiveWholeNumberOfSeconds(t *testing.T) {
	for lifetime, want := range map[time.Duration]string{
		-time.Second:                            "token_lifetime must be a positive whole number of seconds, at most 2147483647s, got -1s",
		1500 * time.Millisecond:                 "token_lifetime must be a positive whole number of seconds, at most 2147483647s, got 1.5s",
		math.MaxInt32*time.Second + time.Second: "token_lifetime must be a positive whole number of seconds, at most 2147483647s, got 596523h14m8s",
	} {
		if got := refusal(t, Input{TokenLifetime: lifetime}, withPair); got != want {
			t.Errorf("%v: got %q", lifetime, got)
		}
	}
	// The service owns the maximum: a day and a second is its to refuse.
	if config, _ := resolved(t, Input{TokenLifetime: 86401 * time.Second}, withPair); config.TokenLifetime != 86401*time.Second {
		t.Fatalf("got %v", config.TokenLifetime)
	}
}

func TestTLSComesFromMemcoAPITLS(t *testing.T) {
	for value, want := range map[string]bool{"true": true, "false": false, " FALSE ": false, "True\n": true, "": true, "  ": true} {
		config, out := resolved(t, Input{}, environment{TokenEnv: "t", TLSEnv: value})
		if config.TLS != want {
			t.Errorf("%q: got TLS %v", value, config.TLS)
		}
		if !strings.Contains(out, fmt.Sprintf("tls=%v", want)) {
			t.Errorf("%q: the endpoint record says %q", value, out)
		}
	}
}

func TestPlaintextWinsOverMemcoAPITLS(t *testing.T) {
	for _, value := range []string{"true", "not a boolean"} {
		config, _ := resolved(t, Input{Plaintext: true}, environment{TokenEnv: "t", TLSEnv: value})
		if config.TLS {
			t.Errorf("%q: TLS stayed on", value)
		}
	}
}

func TestAnUnreadableMemcoAPITLSIsRefused(t *testing.T) {
	for _, value := range []string{"yes", "1", "off", "tru"} {
		want := fmt.Sprintf("MEMCO_API_TLS must be true or false, got %q", value)
		if got := refusal(t, Input{}, environment{TokenEnv: "t", TLSEnv: value}); got != want {
			t.Errorf("got %q, want %q", got, want)
		}
	}
}

func TestTheClientSecretIsInNoRendering(t *testing.T) {
	config, _ := resolved(t, Input{ClientID: "id", ClientSecret: "cs-secret-value"}, nil)
	if config.ClientSecret.Reveal() != "cs-secret-value" {
		t.Fatal("control: the secret can no longer be sent")
	}
	encoded, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	var buffer bytes.Buffer
	slog.New(slog.NewTextHandler(&buffer, nil)).Info("x", "config", config)
	renderings := []string{string(encoded), buffer.String()}
	for _, verb := range []string{"%v", "%+v", "%#v", "%s", "%q", "%x"} {
		renderings = append(renderings, fmt.Sprintf(verb, config), fmt.Sprintf(verb, *config))
	}
	for _, rendered := range renderings {
		if strings.Contains(rendered, "cs-secret-value") || strings.Contains(rendered, "63732d") {
			t.Errorf("the secret is visible in %q", rendered)
		}
	}
}

func yes() *bool { on := true; return &on }
func no() *bool  { off := false; return &off }

func TestTheTLSOptionWinsOverMemcoAPITLS(t *testing.T) {
	cases := []struct {
		in    Input
		value string
		want  bool
	}{
		{Input{TLS: yes()}, "false", true},
		{Input{TLS: no()}, "true", false},
		// The option decides, so a value it overrides is not read at all.
		{Input{TLS: no()}, "not a boolean", false},
		{Input{TLS: yes()}, "not a boolean", true},
		{Input{Plaintext: true, TLS: no()}, "true", false},
	}
	for _, c := range cases {
		c.in.Token = "t"
		config, _ := resolved(t, c.in, environment{TLSEnv: c.value})
		if config.TLS != c.want {
			t.Errorf("%+v with %q: got TLS %v", c.in, c.value, config.TLS)
		}
	}
}

func TestPlaintextBesideTLSIsRefused(t *testing.T) {
	want := "Plaintext and TLS disagree: Plaintext is deprecated, so set TLS alone"
	if got := refusal(t, Input{Token: "t", Plaintext: true, TLS: yes()}, nil); got != want {
		t.Fatalf("got %q", got)
	}
}
