// Package config resolves client settings from arguments and the environment.
package config

import (
	"fmt"
	"log/slog"
	"math/big"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/warn"
)

const (
	DefaultHost    = "grpc.memco.ai"
	DefaultPort    = 443
	DefaultTimeout = 30 * time.Second

	TokenEnv       = "MEMCO_API_TOKEN"
	LegacyTokenEnv = "MEMCO_API_KEY"
	HostEnv        = "MEMCO_API_HOST"
)

// Input is what a caller passed; zero values mean unset.
type Input struct {
	Token     string
	Host      string
	Plaintext bool
	Timeout   time.Duration
}

// Config is the resolved configuration.
type Config struct {
	Credential *Credential
	Host       string
	Port       int
	TLS        bool
	Timeout    time.Duration
}

// Target is host:port, bracketing an IPv6 host.
func (c *Config) Target() string { return net.JoinHostPort(c.Host, strconv.Itoa(c.Port)) }

// DialTarget is Target under the dns scheme, so no host is read as a scheme.
func (c *Config) DialTarget() string {
	return (&url.URL{Scheme: "dns", Path: "/" + c.Target()}).String()
}

const redacted = "[redacted]"

// Credential holds the token and never prints it.
type Credential struct{ token string }

// Reveal returns the token, for the transport alone.
func (c *Credential) Reveal() string { return c.token }

func (Credential) String() string               { return redacted }
func (Credential) GoString() string             { return redacted }
func (Credential) Format(f fmt.State, _ rune)   { _, _ = f.Write([]byte(redacted)) }
func (Credential) LogValue() slog.Value         { return slog.StringValue(redacted) }
func (Credential) MarshalJSON() ([]byte, error) { return []byte(`"` + redacted + `"`), nil }

// Resolve applies arguments, then the environment, then defaults.
func Resolve(in Input, getenv func(string) string, log *slog.Logger) (*Config, error) {
	// Before the credential: a caller who got the timeout wrong should hear
	// about that rather than about a token they left to the environment.
	if in.Timeout < 0 {
		return nil, fault.Config(fmt.Sprintf("timeout must be positive, got %s", in.Timeout))
	}
	timeout := in.Timeout
	if timeout == 0 {
		timeout = DefaultTimeout
	}

	token, err := resolveToken(in.Token, getenv, log)
	if err != nil {
		return nil, err
	}
	if err := checkSendable(token); err != nil {
		return nil, err
	}

	if in.Host != "" && strings.TrimSpace(in.Host) == "" {
		return nil, fault.Config("the host passed to the client is blank: pass a real host or set " + HostEnv)
	}
	fromEnv := strings.TrimSpace(getenv(HostEnv))
	host, source := in.Host, "the host argument"
	switch {
	case host != "":
	case fromEnv != "":
		host, source = fromEnv, HostEnv
	default:
		host, source = DefaultHost, "the default"
	}
	name, port, err := splitHostPort(strings.TrimSpace(host))
	if err != nil {
		return nil, err
	}

	config := &Config{Credential: &Credential{token: token}, Host: name, Port: port, TLS: !in.Plaintext, Timeout: timeout}
	log.Debug("endpoint", "target", config.Target(), "tls", config.TLS, "source", source)
	return config, nil
}

func resolveToken(token string, getenv func(string) string, log *slog.Logger) (string, error) {
	if trimmed := strings.TrimSpace(token); trimmed != "" {
		log.Debug("credential taken from the token argument")
		return trimmed, nil
	}
	if token != "" {
		// A blank argument is a caller bug, not a request for the environment.
		return "", fault.Config("the token passed to the client is blank: pass a real token or set " + TokenEnv)
	}
	if fromEnv := strings.TrimSpace(getenv(TokenEnv)); fromEnv != "" {
		log.Debug("credential taken from " + TokenEnv)
		return fromEnv, nil
	}
	if legacy := strings.TrimSpace(getenv(LegacyTokenEnv)); legacy != "" {
		warn.Once(log, "DeprecationWarning", LegacyTokenEnv+" is deprecated and will be removed in a future release; rename it to "+TokenEnv+".")
		log.Debug("credential taken from " + LegacyTokenEnv)
		return legacy, nil
	}
	return "", fault.Config("no API token: pass token=... or set " + TokenEnv)
}

// checkSendable refuses a token gRPC could not send as a header, naming where
// the offending character is but never the token.
func checkSendable(token string) error {
	index := 0
	for _, r := range token {
		if r < 0x20 || r > 0x7e {
			return fault.Config(fmt.Sprintf(
				"the token cannot be sent as an HTTP header: it must be printable ASCII, and the character at index %d is not", index))
		}
		index++
	}
	return nil
}

var integer = regexp.MustCompile(`^[+-]?[0-9]+$`)

func parsePort(text, host string) (int, error) {
	trimmed := strings.TrimSpace(text)
	if !integer.MatchString(trimmed) {
		return 0, fault.Config(fmt.Sprintf("host %q has an unparseable port: %q is not an integer", host, text))
	}
	// big.Int so a huge port is echoed exactly rather than as an overflow.
	port, _ := new(big.Int).SetString(strings.TrimPrefix(trimmed, "+"), 10)
	if port.Cmp(big.NewInt(1)) < 0 || port.Cmp(big.NewInt(65535)) > 0 {
		return 0, fault.Config(fmt.Sprintf("host %q has a port outside the range 1-65535: %s", host, port))
	}
	return int(port.Int64()), nil
}

func splitHostPort(host string) (string, int, error) {
	if rest, ok := strings.CutPrefix(host, "["); ok {
		name, after, closed := strings.Cut(rest, "]")
		switch {
		case !closed:
			return "", 0, fault.Config(fmt.Sprintf("host %q opens a bracket that is never closed", host))
		case name == "":
			return "", 0, fault.Config(fmt.Sprintf("host %q has no address inside its brackets", host))
		case after == "":
			return name, DefaultPort, nil
		case !strings.HasPrefix(after, ":"):
			return "", 0, fault.Config(fmt.Sprintf("host %q has unexpected text after the closing bracket", host))
		}
		port, err := parsePort(after[1:], host)
		return name, port, err
	}
	// No colon names no port, and more than one is a bare IPv6 literal, which
	// cannot carry one.
	if strings.Count(host, ":") != 1 {
		return host, DefaultPort, nil
	}
	name, text, _ := strings.Cut(host, ":")
	if strings.TrimSpace(name) == "" {
		return "", 0, fault.Config(fmt.Sprintf("host %q has a port but no hostname", host))
	}
	port, err := parsePort(text, host)
	return name, port, err
}
