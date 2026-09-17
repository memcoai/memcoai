// Handling_errors shows every failure the SDK returns, and what to do about
// each.
//
// No raw gRPC error ever reaches you: every error implements memcoai.Error and
// is one of a small set of concrete types, so you can be as coarse or as
// precise as you like with errors.As.
//
// Run it with:
//
//	export MEMCO_API_TOKEN=...
//	go run ./handling_errors
package main

import (
	"context"
	"errors"
	"log"
	"os"
	"os/signal"
	"time"

	"github.com/memcoai/memcoai/go/memcoai"
)

// backoff is the first pause before retrying a search; each retry doubles it.
var backoff = time.Second

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	err := run(ctx, memcoai.Options{}, log.New(os.Stdout, "", 0))
	stop()
	if err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context, opts memcoai.Options, out *log.Logger) (err error) {
	client, err := connect(ctx, opts, out)
	if client == nil {
		return err
	}
	defer func() { err = errors.Join(err, client.Close(ctx)) }()

	if err := searchWithRetry(ctx, client, "how does authentication work", "coding", out); err != nil {
		return err
	}
	return refusedLocally(ctx, client, out)
}

// refusedLocally shows a malformed request refused before anything is sent.
// Field length limits belong to the service, and are enforced this way once
// Connect has learned them.
func refusedLocally(ctx context.Context, client *memcoai.Client, out *log.Logger) error {
	_, err := client.Memory.Search(ctx, "", memcoai.SearchParams{Domain: "coding"})
	var invalid *memcoai.InvalidRequestError
	if errors.As(err, &invalid) {
		out.Printf("caught locally, nothing sent: %s", invalid.Detail)
		return nil
	}
	return err
}

// connect returns a connected client, or nil once it has said why it could not
// connect. A failure it does not model is returned.
func connect(ctx context.Context, opts memcoai.Options, out *log.Logger) (*memcoai.Client, error) {
	client, err := memcoai.NewClient(opts)
	if err != nil {
		return nil, explainConnect(err, out)
	}
	if err := client.Connect(ctx); err != nil {
		// Connect can be retried on the same client; this example gives up
		// instead, so it closes the client.
		return nil, errors.Join(explainConnect(err, out), client.Close(ctx))
	}
	return client, nil
}

// explainConnect says what stopped a client connecting, and returns nil, or
// returns err when this example does not model it.
func explainConnect(err error, out *log.Logger) error {
	// The order of these cases is load-bearing and nothing enforces it:
	// errors.As finds a *PreconditionFailedError inside a *SunsetError, and a
	// *UnavailableError inside an *UnhealthyError, so testing the general type
	// first would make the specific one unreachable. Specific before general,
	// every time.
	var (
		config       *memcoai.ConfigError
		rejected     *memcoai.AuthenticationError
		sunset       *memcoai.SunsetError
		precondition *memcoai.PreconditionFailedError
		timeout      *memcoai.TimeoutError
		unhealthy    *memcoai.UnhealthyError
		unavailable  *memcoai.UnavailableError
	)
	switch {
	case errors.As(err, &config):
		// Nothing was sent: no token, or an unusable host or port.
		out.Printf("configuration problem: %s", config.Message)
	case errors.As(err, &rejected):
		// Connect lists domains to learn the service's limits, and that call
		// carries the credential, so a stale token fails here rather than on
		// the first search. This is the likeliest way for connecting to fail.
		out.Print("credential rejected; check MEMCO_API_TOKEN is current")
	case errors.As(err, &sunset) && sunset.Kind == memcoai.SunsetAPIVersion:
		// Past its sunset date and no longer served: nothing to retry and
		// nothing to reconfigure. Long before this, the service sends a
		// deprecation notice, logged as a warning, carrying the date this
		// happens. Here it is the API version this build speaks that stopped
		// being served, so upgrading the module is not the whole answer.
		out.Printf("API version no longer served: %s", sunset.Detail)
	case errors.As(err, &sunset):
		out.Printf("upgrade required: %s", sunset.Detail)
	case errors.As(err, &precondition):
		// Same status code, unrelated cause: billing, account state, terms.
		out.Printf("precondition unmet: %s", precondition.Detail)
	case errors.As(err, &timeout):
		// Neither an *UnavailableError nor a *ConfigError, so it needs its own
		// case even when connecting.
		out.Printf("timed out reaching the service: %s", timeout.Error())
	case errors.As(err, &unhealthy):
		// The service answered and said it is not ready. The endpoint, TLS and
		// credential are all fine; the backend is not taking traffic.
		out.Printf("service is not serving: %s", unhealthy.Error())
	case errors.As(err, &unavailable):
		// Could not reach it at all.
		out.Printf("unreachable: %s", unavailable.Error())
	default:
		// Not something this example models. A switch swallows whatever it
		// does not match, so passing it on has to be said out loud.
		return err
	}
	return nil
}

// searchWithRetry searches, handling each failure the way that failure
// deserves. A failure it does not model is returned.
func searchWithRetry(ctx context.Context, client *memcoai.Client, query, domain string, out *log.Logger) error {
	for attempt := range 3 {
		err := search(ctx, client, query, domain, out)
		if err == nil {
			return nil
		}
		retry, err := explainSearch(ctx, err, out)
		if retry == "" {
			return err
		}
		wait := backoff << attempt
		out.Printf("%s; retrying in %s", retry, wait)
		if err := pause(ctx, wait); err != nil {
			return err
		}
	}
	out.Print("gave up after 3 attempts")
	return nil
}

// search runs one search and says how many memories came back.
func search(ctx context.Context, client *memcoai.Client, query, domain string, out *log.Logger) error {
	// A deadline on the context bounds one call; without one, the client's
	// Options.Timeout applies.
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	result, err := client.Memory.Search(ctx, query, memcoai.SearchParams{Domain: domain})
	if err != nil {
		return err
	}
	out.Printf("%d memories", len(result.Memories))
	return nil
}

// explainSearch deals with a failed search. It returns why the search is worth
// retrying, or "" when it is not: then nil once it has said what went wrong,
// or err when this example does not model it.
func explainSearch(ctx context.Context, err error, out *log.Logger) (string, error) {
	// Ordered like explainConnect, and for the same reason: every modelled
	// type is tested before *APIError, which all of them embed.
	var (
		invalid   *memcoai.InvalidRequestError
		rejected  *memcoai.AuthenticationError
		denied    *memcoai.PermissionError
		notFound  *memcoai.NotFoundError
		exhausted *memcoai.ResourceExhaustedError
		timeout   *memcoai.TimeoutError
		down      *memcoai.UnavailableError
		api       *memcoai.APIError
	)
	switch {
	case ctx.Err() != nil:
		// The caller's own context ended the call; errors.Is(err,
		// context.Canceled) holds. Stop rather than retry.
		return "", err
	case errors.As(err, &invalid):
		// Malformed request. Retrying is pointless: fix the call. Some of
		// these are raised locally, before anything is sent.
		out.Printf("bad request: %s", invalid.Detail)
	case errors.As(err, &rejected):
		// Missing, expired or revoked credential. The service returns one
		// indistinguishable message for all of them by design.
		out.Print("credential rejected; check MEMCO_API_TOKEN is current")
	case errors.As(err, &denied):
		// Authenticated, but this credential lacks the scope or role.
		out.Print("credential lacks permission for this operation")
	case errors.As(err, &notFound):
		out.Print("nothing there")
	case errors.As(err, &exhausted) && exhausted.Kind == memcoai.ResourceExhaustedQuota:
		// Rate limit and usage quota share a status code, so the SDK infers
		// which from the message. A spent quota will not clear on this
		// timescale; a rate limit is worth waiting out.
		out.Print("usage quota exhausted; retrying will not help")
	case errors.As(err, &exhausted):
		return "rate limited", nil
	case errors.As(err, &timeout):
		out.Print("timed out; give the call a longer deadline")
	case errors.As(err, &down):
		return "service unavailable", nil
	case errors.As(err, &api):
		// Anything the service reported that is not modelled above.
		out.Printf("unexpected %s", api.Error())
	default:
		// A *ConfigError: the client is closed.
		return "", err
	}
	return "", nil
}

// pause waits for d, or until ctx ends.
func pause(ctx context.Context, d time.Duration) error {
	select {
	case <-time.After(d):
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
