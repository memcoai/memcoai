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

| Setting    | `Options` field | Environment       | Default             |
| ---------- | --------------- | ----------------- | ------------------- |
| Credential | `Token`         | `MEMCO_API_TOKEN` | required            |
| Endpoint   | `Host`          | `MEMCO_API_HOST`  | `grpc.memco.ai:443` |
| TLS        | `Plaintext`     | —                 | TLS on              |
| Deadline   | `Timeout`       | —                 | 30 seconds          |
| Log level  | `LogLevel`      | `MEMCO_LOG`       | `info`              |
| Logger     | `Logger`        | —                 | the SDK's own       |

The credential is either a Memco API key or a session token issued for your
account; both go in the same header. `MEMCO_API_KEY` is still honoured but
warns.

`NewClient` sends nothing. `Connect` makes two calls. It probes the service's
health endpoint, so a bad host, port or TLS setting fails immediately rather
than on your first real call; that probe carries **no credential**, so it cannot
check one. It then calls `ListDomains`, which does — a bad token fails here —
and which reports the input limits the service enforces. The client keeps those
and applies them from then on, so an oversized field is refused locally instead
of costing a round trip.

`Connect` is safe to call again after a failure; the connection is kept, so a
retry is cheap rather than a fresh handshake.

## Contexts and timeouts

Every call that can block takes a `context.Context`.

- A context with a deadline bounds the call, whether that deadline is shorter
  or longer than `Options.Timeout`. A context without one gets
  `Options.Timeout`.
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
| `StartSession(ctx, domain)`              | Open a session                                                  |
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

## Errors

Every error the SDK returns implements `memcoai.Error` and is one of these
concrete types. No raw transport error reaches a caller.

```
*ConfigError                  bad settings, or a closed client
*APIError                     embedded in every type below
├── *AuthenticationError      UNAUTHENTICATED
├── *PermissionError          PERMISSION_DENIED
├── *InvalidRequestError      INVALID_ARGUMENT — also returned locally
├── *NotFoundError            NOT_FOUND
├── *PreconditionFailedError  FAILED_PRECONDITION
│   └── *SunsetError          a version reached its end of life; read Kind
├── *ResourceExhaustedError   RESOURCE_EXHAUSTED; read Kind
├── *UnavailableError         UNAVAILABLE
│   └── *UnhealthyError       answered, but not serving
├── *TimeoutError             DEADLINE_EXCEEDED
└── *InternalError            everything else, cancellation included
```

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
