package memcoai

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/health/grpc_health_v1"

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
type Options struct {
	// Token is the credential. Empty reads MEMCO_API_TOKEN, then the
	// deprecated MEMCO_API_KEY; a token of only whitespace is refused.
	Token string
	// Host is the endpoint, as host, host:port, [ipv6]:port or a bare IPv6
	// address. Empty reads MEMCO_API_HOST, then uses [DefaultHost]; a host of
	// only whitespace is refused. Without a port, [DefaultPort] is used.
	Host string
	// Plaintext dials without TLS. Only for a local endpoint.
	Plaintext bool
	// Timeout bounds a call whose context has no deadline. Zero uses
	// [DefaultTimeout]; a negative timeout is refused.
	Timeout time.Duration
	// LogLevel sets the SDK logger's level before anything else, as
	// [SetLevel] does, for the whole process. Empty leaves it alone.
	LogLevel string
	// Logger receives this client's log records instead of the SDK's own
	// logger. Its handler decides which levels it keeps.
	Logger *slog.Logger
}

// shown is Options with the token redacted, and none of its methods.
type shown Options

func (o Options) shown() shown {
	if o.Token != "" {
		o.Token = "[redacted]"
	}
	return shown(o)
}

// Format prints the options with any token redacted, whatever the verb.
func (o Options) Format(f fmt.State, verb rune) {
	_, _ = fmt.Fprintf(f, fmt.FormatString(f, verb), o.shown()) // a Formatter has nowhere to report it
}

// LogValue logs the options with any token redacted.
func (o Options) LogValue() slog.Value {
	return slog.AnyValue(o.shown())
}

// Client is a connection to Memco Shared Memory. It is safe for concurrent use,
// and one client serves any number of calls at once.
type Client struct {
	// Memory holds the memory service's operations.
	Memory *MemoryOperations

	config *config.Config
	conn   *grpc.ClientConn
	log    *slog.Logger

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
		Token:     opts.Token,
		Host:      opts.Host,
		Plaintext: opts.Plaintext,
		Timeout:   opts.Timeout,
	}, os.Getenv, logging.Named(log, "config"))
	if err != nil {
		return nil, public(err)
	}
	conn, err := transport.Dial(cfg, "memco-go/"+Version, memory.Service)
	if err != nil {
		return nil, public(err)
	}
	client := &Client{config: cfg, conn: conn, log: logging.Named(log, "client"), drained: make(chan struct{})}
	client.Memory = &MemoryOperations{
		client: client,
		rpc:    memoryv1.NewMemoryServiceClient(conn),
		log:    logging.Named(log, "memory"),
	}
	return client, nil
}

// Connect proves the connection works: it probes the service's health, then
// lists the domains, which proves the credential and teaches the client the
// limits the service enforces. It may be called again after a failure.
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
	err := c.call(ctx, "Check", func(ctx context.Context) error {
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
	// Proves the credential, and teaches the client the service's limits.
	if _, err := c.Memory.ListDomains(ctx); err != nil {
		return err
	}
	c.log.Info("connected", "target", c.config.Target(), "tls", c.config.TLS)
	return nil
}

// Close refuses new calls, waits for the calls in flight, then closes the
// connection. Closing again, or concurrently, is safe.
//
// Parameters:
//   - ctx: bounds the wait. When it ends first, the connection is closed anyway
//     and the calls still in flight fail.
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

// call runs one RPC: nothing is sent on an ended context, a context without a
// deadline gets the client's default, and every failure is typed.
func (c *Client) call(ctx context.Context, rpc string, invoke func(context.Context) error) error {
	if err := c.begin(); err != nil {
		return err
	}
	defer c.end()
	if ctx.Err() != nil {
		return public(fault.FromContext(ctx))
	}
	if _, ok := ctx.Deadline(); !ok {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.config.Timeout)
		defer cancel()
	}
	started := time.Now()
	if err := invoke(ctx); err != nil {
		return public(fault.FromRPC(ctx, err))
	}
	c.log.Debug("rpc ok", "rpc", rpc, "elapsed_ms", time.Since(started).Milliseconds())
	return nil
}
