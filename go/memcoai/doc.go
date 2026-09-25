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
//	MEMCO_CLIENT_ID      an API client's id and secret, read as a pair; with no
//	MEMCO_CLIENT_SECRET  credential in Options, both set win over MEMCO_API_TOKEN
//	MEMCO_API_TOKEN      the credential, when no client pair is set
//	MEMCO_API_KEY        read when MEMCO_API_TOKEN is unset; deprecated, and warns once
//	MEMCO_API_HOST       the endpoint; defaults to grpc.memco.ai:443
//	MEMCO_API_TLS        false dials without TLS, for a local server; true or
//	                     unset keeps TLS on, and anything else is refused.
//	                     Options.TLS, when set, wins over it
//	MEMCO_LOG            the SDK logger's level: critical, error, warning, info
//	                     (the default), debug, or none to silence it
//
// A token in Options ignores the client pair in the environment, and a token
// beside client credentials in Options is refused. No credential is ever
// written to a log record or an error. A client without TLS logs a warning as
// it is built.
//
// # Networks and users
//
// An API client's credentials ([Options.ClientID] and [Options.ClientSecret])
// are exchanged for a token the client renews by itself. With them,
// [Client.Networks] places your own users in memory networks, which decide
// what knowledge each can find, and [Client.Users] creates those users, known
// to Memco by your id for them, and their API keys.
//
// # Acting for your users
//
// [MemoryOperations.StartSession] with [ExternalID] opens a session acting as
// one of your users: every call through it carries a short-lived key minted
// for that user, which renews itself and is ended by [Session.Close]. Sessions
// for different users run at once on one client, each under its own key.
//
//	session, err := client.Memory.StartSession(ctx, "coding", memcoai.ExternalID("customer-42"))
//	if err != nil {
//		return err
//	}
//	defer session.Close(ctx)
//
// # Contexts and timeouts
//
// Every call that can block takes a [context.Context]. A context with a
// deadline bounds the call, whether that deadline is shorter or longer than
// [Options.Timeout]; a context without one gets Options.Timeout from the
// call's start. That one deadline covers the whole call: waiting for a
// credential, when none still serves and a new one must be minted first, and
// the request, which gets whatever the wait left. A context that has already
// ended sends nothing. [Client.Close] takes a context too: it waits
// for calls in flight until the context ends, then closes regardless.
//
// # Errors
//
// Every error this package returns implements [Error], and is one of the
// concrete types below. Use [errors.As], and test a subtype before its parent:
// a [*SunsetError] is also a [*PreconditionFailedError], and an
// [*UnhealthyError] is also an [*UnavailableError].
//
//   - [*ConfigError]: a setting or state stopped the call before it was sent,
//     or, in the one case its documentation names, this machine's clock did.
//   - [*InvalidRequestError]: the request is malformed. The SDK refuses many of
//     these itself, before sending anything.
//   - [*AuthenticationError], [*PermissionError]: the credential was rejected,
//     or may not do what was asked.
//   - [*NotFoundError]: a handle resolves to nothing.
//   - [*AlreadyExistsError]: what the call would create already exists.
//   - [*PreconditionFailedError]: the account, or what the caller uses, is not
//     in a state that allows the call. Its subtypes name why:
//     [*SunsetError], [*UserAlreadyAssignedNetworkError] and
//     [*ExternalUserNeedsCustomerNetworkError].
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
