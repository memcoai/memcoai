// Package memcoai is the Go SDK for Memco Shared Memory: a persistent,
// searchable memory that a team and its AI agents share.
//
// An agent searches the memory before it starts work and writes back what it
// learned when it finishes, so what one agent establishes every teammate's
// agent can find. Memory is partitioned into domains, each with its own subject
// matter and tag vocabulary, and every result carries a reliability signal
// built from what readers reported back about it.
//
// # Getting started
//
// Create a [Client], prove the connection with [Client.Connect], and open a
// [Session] in a domain:
//
//	client, err := memcoai.NewClient(memcoai.Options{}) // reads MEMCO_API_TOKEN
//	if err != nil {
//		return err
//	}
//	defer client.Close(ctx)
//	if err := client.Connect(ctx); err != nil {
//		return err
//	}
//	session, err := client.Memory.StartSession(ctx, "coding")
//	if err != nil {
//		return err
//	}
//	result, err := session.Search(ctx, "how should a client authenticate", memcoai.ScopedSearchParams{})
//
// # Configuration
//
// [Options] fields fall back to the environment, then to built-in defaults:
//
//	MEMCO_API_TOKEN   the credential; required unless Options.Token is set
//	MEMCO_API_KEY     read when MEMCO_API_TOKEN is unset; deprecated, and warns once
//	MEMCO_API_HOST    the endpoint; defaults to grpc.memco.ai:443
//	MEMCO_LOG         the SDK logger's level: critical, error, warning, info (the
//	                  default), debug, or none to silence it
//
// No credential is ever written to a log record or an error.
//
// # Contexts and timeouts
//
// Every call that can block takes a [context.Context]. A context with a
// deadline bounds the call, whether that deadline is shorter or longer than
// [Options.Timeout]; a context without one gets Options.Timeout. A context that
// has already ended sends nothing. [Client.Close] takes a context too: it waits
// for calls in flight until the context ends, then closes regardless.
//
// # Errors
//
// Every error this package returns implements [Error], and is one of the
// concrete types below. Use [errors.As], and test a subtype before its parent:
// a [*SunsetError] is also a [*PreconditionFailedError], and an
// [*UnhealthyError] is also an [*UnavailableError].
//
//   - [*ConfigError]: a setting or state stopped the call before it was sent.
//   - [*InvalidRequestError]: the request is malformed. The SDK refuses many of
//     these itself, before sending anything.
//   - [*AuthenticationError], [*PermissionError]: the credential was rejected,
//     or may not do what was asked.
//   - [*NotFoundError]: a handle resolves to nothing.
//   - [*PreconditionFailedError], [*SunsetError]: the account, or what the
//     caller uses, is not in a state that allows the call.
//   - [*ResourceExhaustedError]: a rate limit or quota is spent; its Kind says
//     which.
//   - [*UnavailableError], [*UnhealthyError]: the service could not be reached,
//     or is not serving. Reads have already been retried.
//   - [*TimeoutError]: the call ran out of time. A write may have happened.
//   - [*InternalError]: anything else, including a call cancelled through its
//     context.
//
// When a context ends a call — the caller's, or the one [Options.Timeout] adds
// to a context without a deadline — the error also matches [context.Canceled]
// or [context.DeadlineExceeded] with [errors.Is].
//
// # Logging
//
// The SDK logs through its own [log/slog] logger, never [slog.Default]. Its
// level comes from MEMCO_LOG, [Options.LogLevel] or [SetLevel]; none silences
// it, deprecation notices included. [Options.Logger] sends one client's records
// to a logger of the caller's instead. An error the SDK returns is never also
// logged.
//
// # Agents
//
// [Session.Tools] offers a session's operations to a model as tools, in the
// shapes [Toolset.ToAnthropic] and [Toolset.ToOpenAI] return, and
// [Toolset.Call] runs the one a model named. [Briefing] turns what the service
// says about a domain into guidance for a system prompt, and [Render] turns any
// result into text a model can read.
package memcoai
