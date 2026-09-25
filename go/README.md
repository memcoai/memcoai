<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/memcoai/memcoai/main/assets/logo-dark.svg">
    <img alt="Memco" src="https://raw.githubusercontent.com/memcoai/memcoai/main/assets/logo.svg" width="320">
  </picture>
</p>

<p align="center">
  Go SDK for <b>Memco Shared Memory</b>.<br>
  <a href="https://memco.ai">memco.ai</a> &middot;
  <a href="https://docs.memco.ai">docs.memco.ai</a>
</p>

<p align="center">
  <a href="https://github.com/memcoai/memcoai/actions/workflows/ci_go.yaml"><img alt="CI (Go)" src="https://github.com/memcoai/memcoai/actions/workflows/ci_go.yaml/badge.svg?branch=main"></a>
  <img alt="Go versions" src="https://img.shields.io/badge/go-1.25%20%7C%201.26%20%7C%201.27-00add8"><br>
  <img alt="Coverage" src="https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen">
  <a href="LICENSE"><img alt="Licence" src="https://img.shields.io/badge/licence-MIT-blue"></a>
</p>

---

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share. Agents normally start every conversation from nothing: they
rediscover the same constraints, repeat the same mistakes, and lose whatever
they worked out the moment the session ends. Shared Memory is where that
knowledge goes instead.

**You need an account and an API key to use this SDK.** Create one at
[memco.ai](https://memco.ai).

## Install

```bash
go get github.com/memcoai/memcoai/go/memcoai
```

Go 1.25 or newer.

```go
import "github.com/memcoai/memcoai/go/memcoai"
```

## Quick start

```go
ctx := context.Background()
client, err := memcoai.NewClient(memcoai.Options{}) // reads MEMCO_API_TOKEN
if err != nil {
	return err
}
defer client.Close(ctx)
if err := client.Connect(ctx); err != nil {
	return err
}

listed, err := client.Memory.ListDomains(ctx)
if err != nil {
	return err
}
result, err := client.Memory.Search(ctx, "how should a client authenticate against the memory API",
	memcoai.SearchParams{Domain: listed.Domains[0].Slug})
if err != nil {
	return err
}
for _, memory := range result.Memories {
	for _, insight := range memory.Insights {
		fmt.Println(insight.Title, insight.Updated)
	}
}
```

Most work happens inside a session, which relates the searches made for one task
so they can be rated together. A `*Session` carries its id into every call made
through it, so nothing later can drop the handle — a search that omits the
session id is still a valid search, but it opens a session of its own, and the
series you were assembling quietly splits in two:

```go
session, err := client.Memory.StartSession(ctx, "coding")
if err != nil {
	return err
}
result, err := session.Search(ctx, "how does gRPC health checking work", memcoai.ScopedSearchParams{})
if err != nil {
	return err
}
var ratings []memcoai.FeedbackRating
for _, memory := range result.Memories {
	for _, insight := range memory.Insights {
		ratings = append(ratings, memcoai.FeedbackRating{Idx: insight.Idx, Relevant: true, Correct: true})
	}
}
if len(ratings) > 0 {
	if _, err := session.ShareFeedback(ctx, ratings); err != nil {
		return err
	}
}
```

Rating a single memory is shorter directly on the result:
`result.Memories[0].Feedback(ctx, memcoai.MemoryFeedback{Relevant: true, Correct: true})`
submits one rating for that memory's own idx, bound to the session it was
found in.

`Close` takes a context: it waits for calls still in flight until the context
ends, then closes the connection regardless, and returns an error only if it
stopped waiting. A client and its sessions are safe for concurrent use; one
client multiplexes every call over one connection.

Runnable programs are in
[`examples/`](https://github.com/memcoai/memcoai/tree/main/go/examples).

## Agents

A session carries the tools a model needs, described in the service's own words
rather than each SDK's:

```go
listed, err := client.Memory.ListDomains(ctx)
// ... pick the entry for the domain you work in, then:
session, err := client.Memory.StartSession(ctx, entry.Slug)

system := memcoai.Briefing(entry, session.Instructions)
toolset := session.Tools()

toolset.ToAnthropic() // []AnthropicTool: {name, description, input_schema}
toolset.ToOpenAI()    // []OpenAITool: {type: "function", function: {...}}

// Hand a tool call straight back, arguments as the model sent them.
text, err := toolset.Call(ctx, "memco_search", json.RawMessage(`{"query": "health checking"}`))
```

With the [Anthropic Go SDK](https://github.com/anthropics/anthropic-sdk-go), a
tool is one struct away:

```go
var tools []anthropic.ToolUnionParam
for _, tool := range toolset.ToAnthropic() {
	tools = append(tools, anthropic.ToolUnionParam{OfTool: &anthropic.ToolParam{
		Name:        tool.Name,
		Description: anthropic.String(tool.Description),
		InputSchema: anthropic.ToolInputSchemaParam{
			Properties: tool.InputSchema.Properties,
			Required:   tool.InputSchema.Required,
		},
	}})
}
```

[`examples/anthropic_agent`](https://github.com/memcoai/memcoai/tree/main/go/examples/anthropic_agent)
is a complete agent loop around it.

Six operations are offered: `memco_search`, `memco_get_memory`,
`memco_create_memory`, `memco_enrich_memory`, `memco_share_feedback` and
`memco_revert_memory`. `ListDomains` and `StartSession` are deliberately absent —
the session has already answered both, and `Briefing` says so, so a model is
never handed a tool whose answer it already has.

`source` is bound rather than exposed, so a model cannot claim a human wrote its
content; `session_id` and `domain` are bound because the session supplies them.

The descriptions and parameter copy come from `tools.json`, the same manifest
the hosted MCP server publishes, compiled into the module by
`scripts/sync_tool_docs.py`. A wording fix belongs upstream, not here.

**Only two failures are reported back to the model as text** — a malformed
request and a not-found handle, the two it can actually do something about.
`Call` returns them as its string, with a nil error. Everything else, a rejected
credential included, is returned as the error for your code to handle.
`IsAgentRecoverable` names that line. `Render` turns any result into the text a
tool returns, if you call the operations yourself.

## Configuration

Options win over the environment, which wins over the defaults. A zero field is
unset.

| Setting             | `Options` field | Environment                         | Default                                                |
| ------------------- | --------------- | ----------------------------------- | ------------------------------------------------------ |
| Credential          | `Token`         | `MEMCO_API_TOKEN`                   | required, unless an API client's credentials are given |
| API client id       | `ClientID`      | `MEMCO_CLIENT_ID`                   | —                                                      |
| API client secret   | `ClientSecret`  | `MEMCO_CLIENT_SECRET`               | —                                                      |
| Issued token's life | `TokenLifetime` | —                                   | the service's                                          |
| Endpoint            | `Host`          | `MEMCO_API_HOST`                    | `grpc.memco.ai:443`                                    |
| TLS                 | `TLS` (`*bool`) | `MEMCO_API_TLS` (`true` or `false`) | TLS on                                                 |
| Deadline            | `Timeout`       | —                                   | 30 seconds                                             |
| Log level           | `LogLevel`      | `MEMCO_LOG`                         | `info`                                                 |
| Logger              | `Logger`        | —                                   | the SDK's own                                          |

The credential is either a Memco API key or a session token issued for your
account; both go in the same header. `MEMCO_API_KEY` is still honoured but
warns.

TLS is on unless you turn it off, with `TLS` pointing at `false` or
`MEMCO_API_TLS=false`, for a plaintext endpoint such as a local development
server. A nil `TLS` leaves the decision to `MEMCO_API_TLS`, and a pointer to
`true` keeps TLS on whatever the environment says; any value there but `true`
or `false` is a `*ConfigError`. A client built without TLS logs a warning as it
is built, before anything is sent, since its credential then crosses the wire
readable. `Plaintext: true` still turns TLS off, as in 0.1, but is deprecated in
favour of `TLS`; setting it beside a `TLS` of `true` is a `*ConfigError`.

A client can authenticate as an API client instead, with the `ClientID` and
`ClientSecret` it was created with. The SDK exchanges them for a token, asking
for `TokenLifetime` (whole seconds) if you set it, and renews the token before
it expires. The secret is never logged or printed, and neither is any token.
Which credential a client holds is decided in this order:

1. A `Token` wins, and the client variables are ignored. Setting it beside
   `ClientID` or `ClientSecret` is an error.
2. `ClientID` and `ClientSecret` come next.
3. With no credential in `Options`, `MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET`
   win over `MEMCO_API_TOKEN` when both are set.
4. Otherwise `MEMCO_API_TOKEN`, as above.

Half a pair, in `Options` or in the environment, is a `*ConfigError`, and so is
`TokenLifetime` without client credentials.

`NewClient` sends nothing. `Connect` makes two calls. It probes the service's
health endpoint, so a bad host, port or TLS setting fails immediately rather
than on your first real call; that probe carries **no credential**, so it cannot
check one. It then calls `ListDomains`, which does — a bad token fails here —
and which reports the input limits the service enforces. The client keeps those
and applies them from then on, so an oversized field is refused locally instead
of costing a round trip.

With client credentials, the second call is the exchange for a token instead,
so bad credentials fail at `Connect` too. An issued token carries no content
role of its own, so the memory operations refuse it. Such a client administers
networks and users, and reaches memory through sessions acting for your users,
both described below.

`Connect` is safe to call again after a failure; the connection is kept, so a
retry is cheap rather than a fresh handshake.

### Against a local development server

A local server usually serves plaintext, so point the client at it and turn TLS
off, either in the environment:

```bash
export MEMCO_API_HOST=localhost:50052 MEMCO_API_TLS=false MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...

cd go/examples && go run ./map_your_users
```

or in code:

```go
plaintext := false
client, err := memcoai.NewClient(memcoai.Options{
	Host: "localhost:50052", TLS: &plaintext, ClientID: "...", ClientSecret: "...",
})
```

Either way the client says so before it sends anything:

```
time=2026-09-25T09:19:28.424+02:00 level=WARNING msg="TLS is off: localhost:50052 is dialled in plaintext, credentials included" logger=memco.client
```

A token issued for the live service is rejected by a local server, so for the
token examples set `MEMCO_API_TOKEN` to one the local server issued.

## Contexts and timeouts

Every call that can block takes a `context.Context`.

- A context with a deadline bounds the call, whether that deadline is shorter
  or longer than `Options.Timeout`. A context without one gets
  `Options.Timeout`, from the call's start.
- That one deadline covers the whole call: waiting for a credential, when none
  still serves and a new one must be minted first, and the request, which gets
  whatever the wait left. A credential past its renewal point but not yet
  expired serves the call at once, so the request has the whole deadline.
- A context that has already ended sends nothing.
- When your context ends a call, the error also satisfies
  `errors.Is(err, context.Canceled)` or `errors.Is(err, context.DeadlineExceeded)`.
  A deadline the *service* reports does not, so the two stay distinguishable.
- `ImportMemories` checks the context between the calls it splits a batch into,
  and stops sending once it has ended.

Reads the service can safely repeat — `ListDomains`, `GetMemory`, `ListTools`
and the health probe — are retried on `UNAVAILABLE`, up to three attempts in
all. Writes and searches are never retried, because a retry the service had
already applied would apply twice.

## Logging

The SDK logs through its own [`log/slog`](https://pkg.go.dev/log/slog) logger,
writing text records to `stderr`, never through `slog.Default()`. Each record
says which part of the SDK spoke in its `logger` attribute:

```
time=2026-09-17T12:00:00.000Z level=DEBUG msg=endpoint logger=memco.config target=grpc.memco.ai:443 tls=true source="the default"
time=2026-09-17T12:00:00.120Z level=INFO msg=connected logger=memco.client target=grpc.memco.ai:443 tls=true
time=2026-09-17T12:00:00.240Z level=DEBUG msg=trimmed logger=memco.memory field=tags from=62 to=50
```

That last record is the only signal that the service's cap dropped some of what
you sent, so `debug` is worth turning on when a call does less than you expect.

**No credential is ever written to a record.** The `DEBUG` lines report where a
credential came from, never what it was. **An error the SDK returns is never
also logged**: it is yours to handle or log.

`MEMCO_LOG` accepts `critical`, `error`, `warning`, `info`, `debug`, or `none`
to silence the SDK. A value it does not recognise warns and falls back to
`info`, because a mistyped environment variable should not stop a program
starting; an `Options.LogLevel` or `SetLevel` value it does not recognise is a
`*ConfigError`, because that is your own code. Either changes the level for the
whole process.

To route one client's records into your own logging, pass `Options.Logger`; its
handler then decides what is kept. A deprecation notice from the service is a
warning-level record (`level=WARNING` on the SDK's own logger) whose `warning`
attribute is `DeprecationWarningName`, so a handler can pick it out; the notice
for `MEMCO_API_KEY` carries `warning=DeprecationWarning`. `none` silences both.

## Operations

Reached as `client.Memory`. A `*Session` has every operation from `Search` on,
without the session and domain parameters.

| Operation                                | What it does                                                    |
| ---------------------------------------- | --------------------------------------------------------------- |
| `ListDomains(ctx)`                       | The domains this credential may name, and the limits in force   |
| `ListTools(ctx)`                         | The tools the service offers, and which this credential may use |
| `StartSession(ctx, domain, opts...)`     | Open a session; `ExternalID` opens one acting for your user     |
| `Search(ctx, query, params)`             | Find existing knowledge                                         |
| `GetMemory(ctx, idx)`                    | Fetch one memory in full                                        |
| `CreateMemory(ctx, params)`              | Save new knowledge                                              |
| `EnrichMemory(ctx, params)`              | Add to a memory a search returned, or open a new one            |
| `ShareFeedback(ctx, sessionID, ratings)` | Rate what a search returned                                     |
| `RevertMemory(ctx, operationID)`         | Undo one of your own writes                                     |
| `ImportMemories(ctx, memories, params)`  | Contribute many at once                                         |

`ImportMemories` splits a batch longer than the service accepts across several
calls and renumbers every outcome back into the slice you passed, so
`outcome.Index` always indexes your own input whatever the service's limit is.

Pass `memcoai.NewMemory` as an enrichment's `MemoryIdx` to open a new memory.

## Networks and users

A client built with an API client's credentials administers your organization:
its memory networks, which scope what the people placed in them can find, and
its external users — your own users, known to Memco by your id for them. The
API client needs the `network-management` and `user-management` scopes.

```go
client, err := memcoai.NewClient(memcoai.Options{}) // reads MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET
if err != nil {
	return err
}
defer client.Close(ctx)
if err := client.Connect(ctx); err != nil {
	return err
}
roots, err := client.Networks.List(ctx, memcoai.ListNetworksParams{ParentID: "root", Domain: "coding"})
if err != nil {
	return err
}
acme, err := client.Networks.Create(ctx, memcoai.CreateNetworkParams{
	Name: "Acme", ParentID: roots.Networks[0].ID, Scope: "customer",
})
if err != nil {
	return err
}
user, err := client.Users.Create(ctx, "customer-42", memcoai.CreateUserParams{Roles: []string{"creator"}, Name: "Ada"})
if err != nil {
	return err
}
_, err = client.Networks.AddMember(ctx, acme.ID, user.ID, memcoai.AddMemberParams{})
```

| Method                                                               | Purpose                                                                    |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `Networks.List`                                                      | One page of networks, filtered by name, scope, owner, domain, parent or id |
| `Networks.Create`, `Update`, `Delete`                                | Manage a network; deleting one takes everything placed in it               |
| `Networks.ListMembers`, `AddMember`, `RemoveMember`                  | Who is placed in a network                                                 |
| `Networks.ListGroups`, `ListGroupMembers`, `AddGroup`, `RemoveGroup` | Identity-provider groups, for enterprise organizations                     |
| `Users.List`, `Get`, `Create`, `Update`, `Delete`                    | Manage external users                                                      |
| `Users.ListKeys`, `CreateKey`, `DeleteKey`                           | An external user's API keys                                                |

An external user has no sign-in of its own, never holds `admin`, and can be
placed only in a customer network. Every `client.Users` method names the user by
your external id; `AddMember` takes Memco's own `user.ID`. A user holds one
network per domain, so placing one already elsewhere is refused with a
`*UserAlreadyAssignedNetworkError` naming that network, unless
`AddMemberParams.Force` moves them. On `Update`, a nil field is left as it is,
and a pointer to `""` clears it; a nil `Roles` leaves the roles, and an empty
one is refused. Pages count from 1. A key's value is returned once, by
`CreateKey`, and never again. Read it with `created.Value()`: printing, logging
or encoding a `CreatedKey`, with fmt, slog, JSON or XML, leaves it out.

## Acting for your users

`StartSession` with the `ExternalID` option opens a session that acts as one of
your external users, so what it finds is what that user may find, and what it
writes is that user's:

```go
session, err := client.Memory.StartSession(ctx, "coding", memcoai.ExternalID("customer-42"))
if err != nil {
	return err
}
defer session.Close(ctx)
result, err := session.Search(ctx, "how should a client authenticate", memcoai.ScopedSearchParams{})
```

Opening one runs in a fixed order. The SDK has the service mint an
impersonation key for the user, under your client's own credential; lists the
domains under that key, so the session learns the limits and any deprecation
notice that apply to the user; and only then starts the session. Every call
made through the session — its tools, and the memories it returns, included —
carries that key, and never your client's credential. The API client needs the
admin grant, and what the session can reach is scoped by the network the user
is placed in.

**Close the session.** `session.Close(ctx)` ends the key on the service. The
service caps how many live keys each user may hold, so a session left open holds
one until its key expires. A session dropped without `Close` is caught
eventually: once it, its tools and the memories it returned are all
unreachable, the client's next call ends its key. `Close` ends it at once.
Closing the client ends any key still live; a key the service cannot end is
logged by its id, never its value, and expires on its own. Ending a key is never
cut short by the caller's context: it gets `Options.Timeout` of its own, so a
cancelled request never strands a live key. After closing, a call through the
session is a `*ConfigError`. A session opened without `ExternalID` holds
nothing, so closing it changes nothing.

**Keys renew themselves.** Once four fifths of a key's lifetime have passed,
one renewal starts beside the calls, which the current key goes on serving: no
call waits for a renewal unless the key has expired. The key it replaces is
ended as soon as no call is still using it, so a long session never fails for
an expired key, and a renewal never revokes one under a call in flight. A
renewal that fails — the service down, or the user at their cap of live keys —
is logged as a warning, and the current key serves on until it expires; only
then does a call fail, with the renewal's error. The next call past the
renewal point tries again. The client's own token renews the same way.

A key is timed on this machine's clock alone, from the seconds the service says
it has left, so the clock need not agree with the service's. A service that
does not send that count yet gives only the key's absolute expiry, which is
read against this machine's clock; one running ahead of the service's by more
than a key's lifetime then fails the session with a `*ConfigError` that names
the clock, rather than holding a key it would read as already expired; that key
is ended at once. It is the one `*ConfigError` returned after a request was
sent.

Each session carries its own key, so sessions for different users run side by
side on one client, from as many goroutines as you like:

```go
var wg sync.WaitGroup
for _, user := range []string{"customer-42", "customer-7"} {
	wg.Go(func() {
		session, err := client.Memory.StartSession(ctx, "coding", memcoai.ExternalID(user))
		if err != nil {
			log.Print(err)
			return
		}
		defer session.Close(ctx)
		// ... search and write as this user
	})
}
wg.Wait()
```

## Errors

Every error the SDK returns implements `memcoai.Error` and is one of these
concrete types. No raw transport error reaches a caller.

```
*ConfigError                  bad settings, a closed client, or a clock far ahead
*APIError                     embedded in every type below
├── *AuthenticationError      UNAUTHENTICATED
├── *PermissionError          PERMISSION_DENIED
├── *InvalidRequestError      INVALID_ARGUMENT — also returned locally
├── *NotFoundError            NOT_FOUND
├── *AlreadyExistsError       ALREADY_EXISTS
├── *PreconditionFailedError  FAILED_PRECONDITION; read Reason and Metadata
│   ├── *SunsetError          a version reached its end of life; read Kind
│   ├── *UserAlreadyAssignedNetworkError
│   │                         already in another network of the domain
│   └── *ExternalUserNeedsCustomerNetworkError
│                             only a customer network takes an external user
├── *ResourceExhaustedError   RESOURCE_EXHAUSTED; read Kind
├── *UnavailableError         UNAVAILABLE
│   └── *UnhealthyError       answered, but not serving
├── *TimeoutError             DEADLINE_EXCEEDED
└── *InternalError            everything else, cancellation included
```

A `*PreconditionFailedError` carries the `Reason` the service named the
refusal by, and its `Metadata`, such as the network a user is already in; a
reason this SDK does not know yet still arrives, on the plain type.

Match with `errors.As`. Each type unwraps to the one it embeds, so an
`errors.As` for `*PreconditionFailedError` also finds a `*SunsetError`, and one
for `*APIError` finds any of them. Test the specific type first — a `switch` of
`errors.As` cases stops at the first match:

```go
var (
	sunset       *memcoai.SunsetError
	precondition *memcoai.PreconditionFailedError
	exhausted    *memcoai.ResourceExhaustedError
)
switch {
case errors.As(err, &sunset):
	log.Printf("upgrade required: %s", sunset.Detail)
case errors.As(err, &precondition):
	log.Printf("precondition unmet: %s", precondition.Detail)
case errors.As(err, &exhausted) && exhausted.Kind == memcoai.ResourceExhaustedRateLimit:
	// back off and retry
}
```

**Some limits are checked locally.** Once `Connect` has run, an over-long query
or an over-large batch is refused before anything is sent, with the same
`*InvalidRequestError` the service would have returned.

**Not every not-found is an error.** A revert reports what it actually removed:

```go
reverted, err := client.Memory.RevertMemory(ctx, created.OperationID)
if err != nil {
	return err
}
if reverted.Outcome == memcoai.RevertOutcomeExpired {
	fmt.Println("outside the revert window")
}
```

## Provenance

Which version of the service contract this module was generated from:

```go
provenance, err := memcoai.ReadProvenance()
provenance.ServerCommit     // "93ec8030bac33afa1bf2aa376d5f5a406098d81a"
provenance.Protos[0].Path   // "memcoai/memory/v1/memory.proto"
```

## Versioning

Versions follow semantic versioning, and `go get
github.com/memcoai/memcoai/go/memcoai@vX.Y.Z` fetches one; `memcoai.Version`
names the release a program was built with. A release appears on GitHub as
`go-vX.Y.Z`, like the other SDKs' releases, and its module is tagged
`go/vX.Y.Z`, the form the go command reads for a module in the repository's
`go/` directory. The generated client is internal, so contract changes reach
you only through this package's own types.

## How this SDK differs from the Python and Node.js ones

The operations, rules and messages are the same. What differs follows from the
language:

- A context replaces the per-call `timeout` argument, and `Close` takes one too.
- Zero values mean unset, so an empty string is never sent as a tag's version or
  a rating's comment.
- There is no `with_session` / `withSession`: `StartSession` returns the bound
  session directly, and fetches the tool catalogue it filters `Tools()` by
  before returning.
- A cancelled call is an `*InternalError` with code `CANCELLED`, and also
  matches `context.Canceled`.
- Errors are returned, never also logged, and log records carry `slog`
  attributes rather than formatted text.
- There is no LangChain adapter; a `Tool` already has the name, description,
  JSON Schema and call a framework needs.
- A session acts for your user through the `ExternalID` option rather than an
  `external_id` argument, and `Session.Close` returns nothing: ending a key is
  best effort, and a failure is a logged warning, not an error. A dropped
  session's key is ended once the session is garbage.
- TLS is a `*bool`, so nil can mean unset; `Plaintext`, from 0.1, is
  deprecated. Unlike in 0.1, where TLS was always on unless `Plaintext` was
  set, `MEMCO_API_TLS=false` now turns it off for a client that sets neither.
  `TokenLifetime` is a `time.Duration`, and zero is unset.

## Contributing

See
[CONTRIBUTING.md](https://github.com/memcoai/memcoai/blob/main/CONTRIBUTING.md#go).

```bash
make -C go install
make -C go test
make -C go docs-serve
```

Everything under `internal/client/` is generated from the service contract and
is replaced wholesale each time it is regenerated, so please do not edit it by
hand.

## Licence

[MIT](LICENSE), © Memco Labs, Inc.
