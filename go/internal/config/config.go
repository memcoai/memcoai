// Package config resolves client settings from arguments and the environment.
package config

import (
	"fmt"
	"log/slog"
	"math"
	"math/big"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/warn"
)

const (
	DefaultHost    = "grpc.memco.ai"
	DefaultPort    = 443
	DefaultTimeout = 30 * time.Second

	TokenEnv        = "MEMCO_API_TOKEN"
	LegacyTokenEnv  = "MEMCO_API_KEY"
	HostEnv         = "MEMCO_API_HOST"
	TLSEnv          = "MEMCO_API_TLS"
	ClientIDEnv     = "MEMCO_CLIENT_ID"
	ClientSecretEnv = "MEMCO_CLIENT_SECRET"
)

// Input is what a caller passed; zero values mean unset.
type Input struct {
	Token         string
	ClientID      string
	ClientSecret  string
	TokenLifetime time.Duration
	Host          string
	Plaintext     bool
	TLS           *bool
	Timeout       time.Duration
}

// Config is the resolved configuration: a token in Credential, or an API
// client's ClientID and ClientSecret.
type Config struct {
	Credential    *Credential
	ClientID      string
	ClientSecret  *Credential
	TokenLifetime time.Duration
	Host          string
	Port          int
	TLS           bool
	Timeout       time.Duration
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

// Secret holds a value that must never be printed.
func Secret(value string) *Credential { return &Credential{token: value} }

// Reveal returns the value, for the wire alone.
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

	config := &Config{Timeout: timeout}
	client, err := resolveClient(in, getenv, log)
	if err != nil {
		return nil, err
	}
	if client == nil {
		token, err := resolveToken(in.Token, getenv, log)
		if err != nil {
			return nil, err
		}
		if err := checkSendable(token); err != nil {
			return nil, err
		}
		// Only an issued token has a lifetime to ask for; accepting one beside
		// a static token would silently ignore it.
		if in.TokenLifetime != 0 {
			return nil, fault.Config("token_lifetime applies only to client credentials, not a token")
		}
		config.Credential = &Credential{token: token}
	} else {
		// No local maximum: the service owns it. The wire carries whole
		// seconds in 32 bits, so anything else would be sent as another value.
		if lifetime := in.TokenLifetime; lifetime < 0 || lifetime%time.Second != 0 || lifetime > math.MaxInt32*time.Second {
			return nil, fault.Config(fmt.Sprintf(
				"token_lifetime must be a positive whole number of seconds, at most %ds, got %s", math.MaxInt32, lifetime))
		}
		config.ClientID, config.ClientSecret, config.TokenLifetime = client.id, &Credential{token: client.secret}, in.TokenLifetime
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
	if config.Host, config.Port, err = splitHostPort(strings.TrimSpace(host)); err != nil {
		return nil, err
	}
	if config.TLS, err = resolveTLS(in, getenv); err != nil {
		return nil, err
	}
	log.Debug("endpoint", "target", config.Target(), "tls", config.TLS, "source", source)
	return config, nil
}

// resolveTLS takes the TLS option, else the deprecated Plaintext, else
// MEMCO_API_TLS, else TLS on. Whether traffic is encrypted is never guessed
// from a typo, nor from two options that disagree.
func resolveTLS(in Input, getenv func(string) string) (bool, error) {
	switch {
	case in.Plaintext && in.TLS != nil && *in.TLS:
		return false, fault.Config("Plaintext and TLS disagree: Plaintext is deprecated, so set TLS alone")
	case in.TLS != nil:
		return *in.TLS, nil
	case in.Plaintext:
		return false, nil
	}
	switch value := strings.ToLower(strings.TrimSpace(getenv(TLSEnv))); value {
	case "", "true":
		return true, nil
	case "false":
		return false, nil
	default:
		return false, fault.Config(fmt.Sprintf("%s must be true or false, got %q", TLSEnv, value))
	}
}

type clientPair struct{ id, secret string }

// resolveClient picks an API client's credentials, or nil for a token. It is
// consulted first, because a complete pair in the environment wins over a
// token there: CI exports both, and a client given no credential of its own
// is the one that needs the pair. A token argument names the caller's
// credential, so it skips the environment's pair.
func resolveClient(in Input, getenv func(string) string, log *slog.Logger) (*clientPair, error) {
	if in.ClientID != "" || in.ClientSecret != "" {
		if in.Token != "" {
			// Two credentials of different kinds: sending either is a guess.
			return nil, fault.Config("pass either token=... or client_id=... with client_secret=..., not both")
		}
		pair := &clientPair{strings.TrimSpace(in.ClientID), strings.TrimSpace(in.ClientSecret)}
		for _, given := range []struct{ name, raw, value string }{
			{"client_id", in.ClientID, pair.id}, {"client_secret", in.ClientSecret, pair.secret},
		} {
			switch {
			case given.raw == "":
				return nil, fault.Config("client credentials need both client_id and client_secret; " + given.name + " is missing")
			case given.value == "":
				return nil, fault.Config("the " + given.name + " passed to the client is blank")
			case !utf8.ValidString(given.value):
				return nil, fault.Config("the " + given.name + " cannot be sent: it is not valid UTF-8")
			}
		}
		log.Debug("client credentials taken from the client_id and client_secret arguments")
		return pair, nil
	}
	if in.Token != "" {
		return nil, nil
	}
	// Blank reads as unset, as for the token: it is what an unset CI secret
	// expands to. Half a pair is refused even beside a token, so a deployment
	// missing one of its secrets fails loudly rather than running as another.
	pair := &clientPair{strings.TrimSpace(getenv(ClientIDEnv)), strings.TrimSpace(getenv(ClientSecretEnv))}
	switch {
	case pair.id == "" && pair.secret == "":
		return nil, nil
	case pair.id == "":
		return nil, fault.Config(ClientIDEnv + " and " + ClientSecretEnv + " must be set together; " + ClientIDEnv + " is not")
	case pair.secret == "":
		return nil, fault.Config(ClientIDEnv + " and " + ClientSecretEnv + " must be set together; " + ClientSecretEnv + " is not")
	case !utf8.ValidString(pair.id) || !utf8.ValidString(pair.secret):
		return nil, fault.Config(ClientIDEnv + " or " + ClientSecretEnv + " cannot be sent: it is not valid UTF-8")
	}
	log.Debug("client credentials taken from " + ClientIDEnv + " and " + ClientSecretEnv)
	return pair, nil
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
