package memcoai

import (
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"runtime"
	"strings"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/status"

	adminv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/admin/v1"
	authv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/auth/v1"
	memoryv1 "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1"
	"github.com/memcoai/memcoai/go/internal/config"
	"github.com/memcoai/memcoai/go/internal/fault"
	"github.com/memcoai/memcoai/go/internal/logging"
	"github.com/memcoai/memcoai/go/internal/memory"
	"github.com/memcoai/memcoai/go/internal/provenance"
	"github.com/memcoai/memcoai/go/internal/transport"
)

const (
	// DefaultHost is the endpoint used when neither Options.Host nor
	// MEMCO_API_HOST names one.
	DefaultHost = "grpc.memco.ai"
	// DefaultPort is the port used when the host names none.
	DefaultPort = 443
	// DefaultTimeout bounds a call whose context has no deadline.
	DefaultTimeout = 30 * time.Second
	// LogEnv names the environment variable that sets the SDK logger's level.
	LogEnv = "MEMCO_LOG"
	// DeprecationWarningName is the value of the "warning" attribute on a
	// deprecation notice's log record, for a handler to filter on.
	DeprecationWarningName = "MemcoDeprecationWarning"
)

const closedMessage = "this client is closed; create a new one to make more calls"

// Options configures a [Client]. The zero value of every field means unset,
// so Options{} reads everything from the environment.
//
// Printing, logging or encoding Options, with fmt, slog, encoding/json or
// encoding/xml, redacts Token and ClientSecret. A verb fmt cannot apply, such
// as %w, prints the fields raw instead; go vet reports that misuse.
type Options struct {
	// Token is the credential: an API key or a session token. Empty reads
	// the environment: a complete MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET pair
	// if both are set, else MEMCO_API_TOKEN, then the deprecated MEMCO_API_KEY.
	// A token of only whitespace is refused, and so is a token beside
	// ClientID or ClientSecret.
	Token string
	// ClientID is an API client's id, set with ClientSecret instead of a
	// Token. The client exchanges the pair for a token, renews it before it
	// expires, and calls no memory method to connect: an issued token has no
	// content role of its own. [Client.Networks] and [Client.Users] need one,
	// as does a session opened with [ExternalID].
	ClientID string
	// ClientSecret is the API client's secret, set with ClientID. It is sent
	// only to exchange for a token, and never logged or printed.
	ClientSecret string
	// TokenLifetime is the lifetime to ask for on each token issued for the
	// client credentials, in whole seconds. Zero takes the service's default;
	// the service owns the maximum, and refuses a longer one. Only valid with
	// client credentials.
	TokenLifetime time.Duration
	// Host is the endpoint, as host, host:port, [ipv6]:port or a bare IPv6
	// address. Empty reads MEMCO_API_HOST, then uses [DefaultHost]; a host of
	// only whitespace is refused. Without a port, [DefaultPort] is used.
	Host string
	// TLS says whether to dial over TLS, with the system's trust store. Nil
	// leaves it to MEMCO_API_TLS: "false" turns TLS off, and "true" or unset
	// keeps it on. A pointer to true keeps TLS on whatever the environment
	// says; a pointer to false turns it off, only for a local endpoint such as
	// a development server. A client without TLS logs a warning as it is
	// built, since its credentials cross the wire readable.
	TLS *bool
	// Plaintext dials without TLS when true, as it did before [Options.TLS]
	// existed. False leaves the choice to TLS. Setting it beside TLS true is
	// refused.
	//
	// Deprecated: Set TLS to a pointer to false instead.
	Plaintext bool
	// Timeout bounds a call whose context has no deadline, from its start:
	// waiting for a credential, if one must be renewed first, and the request
	// share it. Zero uses [DefaultTimeout]; a negative timeout is refused.
	Timeout time.Duration
	// LogLevel sets the SDK logger's level before anything else, as
	// [SetLevel] does, for the whole process. Empty leaves it alone.
	LogLevel string
	// Logger receives this client's log records instead of the SDK's own
	// logger. Its handler decides which levels it keeps.
	Logger *slog.Logger
}

// shown is Options with the credentials redacted, and none of its methods.
type shown Options

func (o Options) shown() shown {
	if o.Token != "" {
		o.Token = "[redacted]"
	}
	if o.ClientSecret != "" {
		o.ClientSecret = "[redacted]"
	}
	return shown(o)
}

// Format prints the options with any token or secret redacted, whatever the
// verb.
func (o Options) Format(f fmt.State, verb rune) {
	_, _ = fmt.Fprintf(f, fmt.FormatString(f, verb), o.shown()) // a Formatter has nowhere to report it
}

// LogValue logs the options with any token or secret redacted.
func (o Options) LogValue() slog.Value {
	return slog.AnyValue(o.shown())
}

// MarshalJSON encodes the options with any token or secret redacted.
func (o Options) MarshalJSON() ([]byte, error) { return json.Marshal(o.shown()) }

// MarshalXML encodes the options with any token or secret redacted.
func (o Options) MarshalXML(e *xml.Encoder, start xml.StartElement) error {
	return e.EncodeElement(o.shown(), start)
}

// Client is a connection to Memco Shared Memory. It is safe for concurrent use,
// and one client serves any number of calls at once.
type Client struct {
	// Memory holds the memory service's operations.
	Memory *MemoryOperations
	// Networks holds the network administration. It needs an API client's
	// credentials; see [Options.ClientID].
	Networks *NetworkOperations
	// Users holds the external-user administration. It needs an API client's
	// credentials; see [Options.ClientID].
	Users *UserOperations

	config *config.Config
	conn   *grpc.ClientConn
	log    *slog.Logger
	admin  adminv1.AdminServiceClient
	tokens authv1.TokenServiceClient
	// credential is the client's own: its token, or the one issued for its
	// client credentials.
	credential *transport.Renewing
	keys       transport.Keys
	// now is the clock credentials are timed against; tests move it.
	now func() time.Time

	mu       sync.Mutex
	closed   bool
	inflight int
	drained  chan struct{} // closed once the client is closed and no call is in flight
	shutdown sync.Once
}

// NewClient resolves the options and prepares a connection, without sending
// anything; [Client.Connect] proves the connection works.
//
// Parameters:
//   - opts: the credential, endpoint, timeout and logging, each falling back to
//     the environment and then to a default; see [Options].
//
// It returns a client that must be closed with [Client.Close].
//
// Errors: [*ConfigError] when no credential is found, a setting is blank or
// malformed, the timeout is negative, or the log level is not one.
func NewClient(opts Options) (*Client, error) {
	if opts.LogLevel != "" {
		if err := logging.SetLevel(opts.LogLevel); err != nil {
			return nil, public(err)
		}
	}
	log := opts.Logger
	if log == nil {
		log = logging.Default()
	}
	cfg, err := config.Resolve(config.Input{
		Token:         opts.Token,
		ClientID:      opts.ClientID,
		ClientSecret:  opts.ClientSecret,
		TokenLifetime: opts.TokenLifetime,
		Host:          opts.Host,
		Plaintext:     opts.Plaintext,
		TLS:           opts.TLS,
		Timeout:       opts.Timeout,
	}, os.Getenv, logging.Named(log, "config"))
	if err != nil {
		return nil, public(err)
	}
	conn, err := transport.Dial(cfg, "memco-go/"+Version, memory.Service)
	if err != nil {
		return nil, public(err)
	}
	client := &Client{
		config:  cfg,
		conn:    conn,
		log:     logging.Named(log, "client"),
		admin:   adminv1.NewAdminServiceClient(conn),
		tokens:  authv1.NewTokenServiceClient(conn),
		now:     time.Now,
		drained: make(chan struct{}),
	}
	if !cfg.TLS {
		// Before anything is sent, and at a level shown by default.
		client.log.Warn("TLS is off: " + cfg.Target() + " is dialled in plaintext, credentials included")
	}
	if cfg.ClientSecret == nil {
		client.credential = transport.Static(cfg.Credential)
	} else {
		// Nothing in the contract revokes an issued token, so it has no end.
		client.credential = transport.NewRenewing(client.clock, client.issue, nil, client.log)
	}
	client.Memory = &MemoryOperations{
		client:     client,
		rpc:        memoryv1.NewMemoryServiceClient(conn),
		log:        logging.Named(log, "memory"),
		known:      &memory.Known{},
		credential: client.credential,
	}
	client.Networks = &NetworkOperations{client: client, rpc: client.admin}
	client.Users = &UserOperations{client: client, rpc: client.admin}
	return client, nil
}

// Connect proves the connection works. It probes the service's health, then
// proves the credential: a token by listing the domains, which also teaches
// the client the limits the service enforces; client credentials by
// exchanging them for a token, calling no memory method, since an issued
// token has no content role of its own. It may be called again after a
// failure, and reuses a token that is not yet due.
//
// Parameters:
//   - ctx: bounds each of the two calls; without a deadline each gets
//     [Options.Timeout].
//
// Errors: [*UnavailableError] when the service cannot be reached;
// [*UnhealthyError] when it answers but is not serving;
// [*AuthenticationError] when the credential is rejected; [*SunsetError] when
// what the client uses has stopped working; [*ConfigError] when the client is
// closed.
func (c *Client) Connect(ctx context.Context) error {
	var serving grpc_health_v1.HealthCheckResponse_ServingStatus
	err := c.call(ctx, "Check", nil, func(ctx context.Context) error {
		var err error
		serving, err = transport.Health(ctx, c.conn)
		return err
	})
	if err != nil {
		return err
	}
	if serving != grpc_health_v1.HealthCheckResponse_SERVING {
		return public(fault.Unhealthy(fmt.Sprintf("%s reported health status %s", c.config.Target(), transport.StatusName(serving))))
	}
	if c.config.ClientSecret != nil {
		if err := c.authenticate(ctx); err != nil {
			return err
		}
	} else if _, err := c.Memory.ListDomains(ctx); err != nil {
		return err
	}
	c.log.Info("connected", "target", c.config.Target(), "tls", c.config.TLS)
	return nil
}

// Close refuses new calls, waits for the calls in flight, ends the keys of
// impersonated sessions left open, then closes the connection. Closing again,
// or concurrently, is safe. A key that cannot be ended is not an error: a
// warning names the keys left, which expire on their own. After Close, closing
// a session does nothing.
//
// Parameters:
//   - ctx: bounds the wait. When it ends first, the connection is closed anyway
//     and the calls still in flight fail. It does not bound ending the keys,
//     which together get [Options.Timeout], so no key is left live because a
//     caller gave up.
//
// Errors: [*TimeoutError] or [*InternalError] when ctx ended before the calls
// in flight finished; either matches ctx's error with [errors.Is].
// [*InternalError] when closing the connection fails.
func (c *Client) Close(ctx context.Context) error {
	c.mu.Lock()
	if !c.closed {
		c.closed = true
		if c.inflight == 0 {
			close(c.drained)
		}
	}
	c.mu.Unlock()
	var stopped error
	select {
	case <-c.drained:
	default:
		// Only now may an ended ctx win: a select picks at random among ready cases.
		select {
		case <-c.drained:
		case <-ctx.Done():
			stopped = public(fault.FromContext(ctx))
		}
	}
	var failed error
	c.shutdown.Do(func() {
		c.endLive(context.WithoutCancel(ctx))
		if err := c.conn.Close(); err != nil {
			failed = public(fault.Internal(err.Error()))
			return
		}
		c.log.Info("closed connection", "target", c.config.Target())
	})
	if stopped != nil {
		return stopped
	}
	return failed
}

// Provenance reports the contract this SDK was generated from, as
// [ReadProvenance] does.
//
// Errors: as [ReadProvenance].
func (c *Client) Provenance() (Provenance, error) { return ReadProvenance() }

// ReadProvenance reports the contract this SDK was generated from, for a bug
// report. It needs no client and sends nothing.
//
// It returns the service build and the contract files, with their checksums.
//
// Errors: [*ConfigError] when the record built into the SDK cannot be read,
// which means the module is broken.
func ReadProvenance() (Provenance, error) {
	record, err := provenance.Read()
	if err != nil {
		return Provenance{}, public(err)
	}
	protos := make([]ProtoRecord, 0, len(record.Protos))
	for _, proto := range record.Protos {
		protos = append(protos, ProtoRecord{Path: proto.Path, SHA256: proto.SHA256})
	}
	return Provenance{ServerCommit: record.ServerCommit, Protos: protos}, nil
}

// SetLevel sets the level of the SDK's own logger, for the whole process. A
// client given [Options.Logger] is unaffected.
//
// Parameters:
//   - level: critical, error, warning, info or debug, or none to silence the
//     logger; case and surrounding space are ignored.
//
// Errors: [*ConfigError] when level names none of these.
func SetLevel(level string) error { return public(logging.SetLevel(level)) }

// begin counts a call in, unless the client is closed.
func (c *Client) begin() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return &ConfigError{Message: closedMessage}
	}
	c.inflight++
	return nil
}

// end counts a call out, and reports the drain once a closed client has none.
func (c *Client) end() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.inflight--
	if c.closed && c.inflight == 0 {
		close(c.drained)
	}
}

// call runs one RPC under credential, or under none when it is nil. Nothing
// is sent on an ended context, a context without a deadline gets the client's
// default, and every failure is typed.
func (c *Client) call(ctx context.Context, rpc string, credential *transport.Renewing, invoke func(context.Context) error) error {
	if err := c.begin(); err != nil {
		return err
	}
	defer c.end()
	if dropped := c.keys.Dropped(); len(dropped) > 0 {
		// Beside this call rather than before it, which they have no part in.
		go c.endKeys(ctx, dropped)
	}
	return c.send(ctx, rpc, credential, invoke)
}

// send is call without the in-flight count, for what runs within another
// call's lease or after the drain.
func (c *Client) send(ctx context.Context, rpc string, credential *transport.Renewing, invoke func(context.Context) error) error {
	if ctx.Err() != nil {
		return public(fault.FromContext(ctx))
	}
	// Before the lease, so waiting on a renewal and the call share one deadline.
	if _, ok := ctx.Deadline(); !ok {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.config.Timeout)
		defer cancel()
	}
	outgoing := transport.Anonymous(ctx)
	if credential != nil {
		held, err := credential.Lease(ctx)
		if err != nil {
			return public(err)
		}
		defer credential.Release(ctx, held)
		outgoing = transport.Bearer(ctx, held.Value)
	}
	started := time.Now()
	if err := invoke(outgoing); err != nil {
		return public(fault.FromRPC(outgoing, err))
	}
	c.log.Debug("rpc ok", "rpc", rpc, "elapsed_ms", time.Since(started).Milliseconds())
	return nil
}

// unary runs one generated client method through call.
func unary[Req, Resp any](ctx context.Context, c *Client, credential *transport.Renewing, rpc string, req Req,
	method func(context.Context, Req, ...grpc.CallOption) (Resp, error)) (Resp, error) {
	var response Resp
	err := c.call(ctx, rpc, credential, func(ctx context.Context) error {
		var err error
		response, err = method(ctx, req)
		return err
	})
	return response, err
}

// authenticate leases the client's own credential and gives it back, which
// issues a token when there is none or it is due.
func (c *Client) authenticate(ctx context.Context) error {
	if err := c.begin(); err != nil {
		return err
	}
	defer c.end()
	held, err := c.credential.Lease(ctx)
	if err != nil {
		return public(err)
	}
	c.credential.Release(ctx, held)
	return nil
}

func (c *Client) clock() time.Time { return c.now() }

// issue exchanges the client credentials for a token. It carries no bearer:
// the credentials in the request are what authenticate it.
func (c *Client) issue(ctx context.Context) (*transport.Minted, error) {
	issued := c.now()
	var response *authv1.IssueTokenResponse
	err := c.send(ctx, "IssueToken", nil, func(ctx context.Context) error {
		var err error
		response, err = c.tokens.IssueToken(ctx, &authv1.IssueTokenRequest{
			GrantType:    "client_credentials",
			ClientId:     c.config.ClientID,
			ClientSecret: c.config.ClientSecret.Reveal(),
			TtlSeconds:   int32(c.config.TokenLifetime / time.Second),
		})
		return err
	})
	if err != nil {
		return nil, err
	}
	// Counted from before the request was sent, so both can only fall early.
	lifetime := time.Duration(response.GetExpiresIn()) * time.Second
	return &transport.Minted{
		Value:   config.Secret(response.GetAccessToken()),
		RenewAt: transport.RenewAt(issued, lifetime),
		Expires: issued.Add(lifetime),
	}, nil
}

// impersonate is the credential of a session acting as externalID, which
// mints its first key on first use. A session dropped unclosed has its key
// queued, for the next call on the client to end.
func (c *Client) impersonate(externalID string) *transport.Renewing {
	holder := c.keys.Holder()
	key := transport.NewRenewing(c.clock,
		func(ctx context.Context) (*transport.Minted, error) { return c.mint(ctx, externalID, holder) },
		func(ctx context.Context, key *transport.Minted) {
			c.endKey(ctx, transport.Key{ExternalID: externalID, ID: key.KeyID})
		},
		c.log)
	// Only queues: a cleanup runs on the runtime's goroutine, where no call
	// belongs.
	runtime.AddCleanup(key, c.keys.Drop, holder)
	return key
}

// mint mints a key acting as externalID, under the client's own credential.
// Each key counts against the user's cap until it is ended or expires, so the
// keys no session will end are ended first.
func (c *Client) mint(ctx context.Context, externalID string, holder uint64) (*transport.Minted, error) {
	pending, ending := c.keys.Before(externalID, c.now())
	c.endKeys(ctx, pending)
	for _, done := range ending {
		<-done // each end has a timeout of its own
	}
	request := &adminv1.ImpersonateExternalUserRequest{ExternalId: externalID}
	var key *adminv1.ImpersonationKey
	// The key is timed from start, for lifetime, on this client's clock.
	var start time.Time
	var lifetime time.Duration
	err := c.call(ctx, "ImpersonateExternalUser", c.credential, func(ctx context.Context) error {
		sent := c.now()
		var err error
		if key, err = c.admin.ImpersonateExternalUser(ctx, request); err != nil {
			return err
		}
		if left := key.GetExpiresIn(); left > 0 {
			// The service's own count of the seconds left, timed like the
			// client token from before the request: this clock alone decides,
			// so a skewed one cannot.
			start, lifetime = sent, time.Duration(left)*time.Second
		} else {
			// A service that sends no count: expires_at is absolute time,
			// converted once, on receipt, onto this clock.
			start = c.now()
			lifetime = time.Unix(key.GetExpiresAt(), 0).Sub(start)
		}
		var refused error
		switch {
		case lifetime <= 0:
			// Held, it would be minted again on every call and never ended.
			refused = fault.Config(fmt.Sprintf("the impersonation key the service issued is already expired "+
				"by this machine's clock, which reads %s against the key's expiry of %s: the clock runs ahead "+
				"of the service's by at least the key's lifetime; synchronise it",
				start.UTC().Format(time.RFC3339), time.Unix(key.GetExpiresAt(), 0).UTC().Format(time.RFC3339)))
		// Inside the call, so a close waiting on it finds the key.
		case c.keys.Add(transport.Key{ExternalID: externalID, ID: key.GetKeyId()}, start.Add(lifetime), holder):
			return nil
		default:
			// A close that stopped waiting has swept already, and would never
			// find the key.
			refused = fault.Config(closedMessage)
		}
		// Live at the service, and held by nobody: ended here, on the
		// connection a close keeps open while it ends what it swept.
		end := &adminv1.EndImpersonationRequest{ExternalId: externalID, KeyId: key.GetKeyId()}
		if _, err := c.admin.EndImpersonation(ctx, end); err != nil && status.Code(err) != codes.NotFound {
			c.log.Warn(endFailed, "key_id", key.GetKeyId(), "error", err.Error())
		}
		return refused
	})
	if err != nil {
		return nil, err
	}
	return &transport.Minted{
		Value:   config.Secret(key.GetValue()),
		RenewAt: transport.RenewAt(start, lifetime),
		Expires: start.Add(lifetime),
		KeyID:   key.GetKeyId(),
	}, nil
}

const endFailed = "could not end impersonation key; it is tried again before this user's next key and when the client closes"

// endKey ends a key its session replaced or closed, unless it is already
// being ended, or was ended, expired or swept.
func (c *Client) endKey(ctx context.Context, key transport.Key) {
	if c.keys.Claim(key.ID) {
		c.endClaimed(ctx, key)
	}
}

// endKeys ends the keys the registry handed over, already claimed, at once.
func (c *Client) endKeys(ctx context.Context, keys []transport.Key) {
	var wg sync.WaitGroup
	for _, key := range keys {
		wg.Go(func() { c.endClaimed(ctx, key) })
	}
	wg.Wait()
}

// endClaimed ends a claimed key. It is not the caller's to cancel, since a key left
// live counts against its user's cap, and it never fails: a key it cannot end
// stays registered, and expires on its own regardless.
func (c *Client) endClaimed(ctx context.Context, key transport.Key) {
	request := &adminv1.EndImpersonationRequest{ExternalId: key.ExternalID, KeyId: key.ID}
	var ended bool
	err := c.call(context.WithoutCancel(ctx), "EndImpersonation", c.credential, func(ctx context.Context) error {
		_, err := c.admin.EndImpersonation(ctx, request)
		// NOT_FOUND: already ended, or expired. Forgotten inside the call, so a
		// close waiting on it never ends the key again.
		if ended = err == nil || status.Code(err) == codes.NotFound; ended {
			c.keys.Ended(key.ID)
		}
		return err
	})
	var closed *ConfigError
	switch {
	case ended:
	case errors.As(err, &closed):
		// The client is closed, and its close ends what is left.
		c.keys.Unclaimed(key.ID, false)
	default:
		c.keys.Unclaimed(key.ID, true)
		// The id revokes nothing, so it is safe to log; the value never is.
		c.log.Warn(endFailed, "key_id", key.ID, "error", err.Error())
	}
}

// endLive ends the keys of sessions left open as the client closes. It stops
// at the first failure: one refusal says the service cannot end them now, and
// asking again for each would only make closing slower.
func (c *Client) endLive(ctx context.Context) {
	ctx, cancel := context.WithTimeout(ctx, c.config.Timeout)
	defer cancel()
	keys := c.keys.Sweep(c.now())
	for at, key := range keys {
		request := &adminv1.EndImpersonationRequest{ExternalId: key.ExternalID, KeyId: key.ID}
		// Sent directly: the client refuses calls now, and none is in flight.
		err := c.send(ctx, "EndImpersonation", c.credential, func(ctx context.Context) error {
			_, err := c.admin.EndImpersonation(ctx, request)
			return err
		})
		var notFound *NotFoundError
		if err != nil && !errors.As(err, &notFound) {
			left := make([]string, 0, len(keys)-at)
			for _, key := range keys[at:] {
				left = append(left, key.ID)
			}
			c.log.Warn("could not end impersonation keys; they expire on their own",
				"key_ids", strings.Join(left, ", "), "error", err.Error())
			return
		}
	}
}
