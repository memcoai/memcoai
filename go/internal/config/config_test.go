package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
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
