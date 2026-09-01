<p align="center">
  <img alt="Memco" src="https://raw.githubusercontent.com/memcoai/memco/main/assets/logo.svg" width="320">
</p>

<p align="center">
  Node.js SDK for <b>Memco Shared Memory</b>.<br>
  <a href="https://memco.ai">memco.ai</a> &middot;
  <a href="https://docs.memco.ai">docs.memco.ai</a>
</p>

<p align="center">
  <a href="https://github.com/memcoai/memco/actions/workflows/ci_nodejs.yaml"><img alt="CI (Node.js)" src="https://github.com/memcoai/memco/actions/workflows/ci_nodejs.yaml/badge.svg?branch=main"></a>
  <img alt="Node versions" src="https://img.shields.io/badge/node-22%20%7C%2024%20%7C%2026-5fa04e"><br>
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
npm install @memcoai/memco
```

Node 22 or newer. The package ships both ESM and CommonJS builds with full type
declarations for each, so `import` and `require` both work.

```ts
import { Memco } from '@memcoai/memco'
```

```js
const { Memco } = require('@memcoai/memco')
```

## Quick start

```ts
import { Memco } from '@memcoai/memco'

await using client = await new Memco().connect() // reads MEMCO_API_TOKEN

const { domains } = await client.memory.listDomains()
const result = await client.memory.search(
  'how should a client authenticate against the memory API',
  { domain: domains[0].slug }
)

for (const memory of result.memories) {
  for (const insight of memory.insights) {
    console.log(insight.title, insight.updated)
  }
}
```

Most work happens inside a session, which relates the searches made for one task
so they can be rated together. Bind one, and no later call can drop the handle —
a search that silently omits the session id is still a valid search — it opens a
session of its own, so the series you were assembling quietly splits in two:

```ts
await using client = await new Memco().connect()
await using session = await client.memory.withSession('coding')

const result = await session.search('how does gRPC health checking work')
await session.shareFeedback({
  feedback: result.memories.flatMap(memory =>
    memory.insights.map(insight => ({
      idx: insight.idx,
      relevant: true,
      correct: true
    }))
  )
})
```

`withSession` sends nothing until it is awaited, and awaiting it twice returns
the same scope rather than opening a second session.

If you would rather not use `await using`, call `close()` yourself:

```ts
const client = await new Memco().connect()
try {
  // ...
} finally {
  await client.close()
}
```

Runnable programs are in
[`examples/`](https://github.com/memcoai/memco/tree/main/nodejs/examples).

## Agents

A bound session carries the tools a model needs, described in the service's own
words rather than each SDK's:

```ts
import { briefing } from '@memcoai/memco'
import { createAgent } from 'langchain'

await using client = await new Memco().connect()
const { domains } = await client.memory.listDomains()
const entry = domains.find(domain => domain.slug === 'coding')!

await using session = await client.memory.withSession(entry.slug)

const agent = createAgent({
  model: 'google_genai:gemini-3.7-flash',
  tools: await session.tools().toLangChain(),
  systemPrompt: briefing(entry, session.instructions)
})
```

`toLangChain()` needs LangChain installed; the other two shapes are plain data
and need nothing:

```ts
const toolset = session.tools()

toolset.toAnthropic() // [{ name, description, input_schema }, ...]
toolset.toOpenAI() // [{ type: 'function', function: {...} }, ...]

// Hand a tool call straight back. OpenAI sends its arguments as a JSON string;
// both that and a plain object are accepted.
const text = await toolset.call('memco_search', { query: 'health checking' })
```

Six operations are offered: `memco_search`, `memco_get_memory`,
`memco_create_memory`, `memco_enrich_memory`, `memco_share_feedback` and
`memco_revert_memory`. `listDomains` and `startSession` are deliberately absent —
the session has already answered both, and `briefing()` says so, so a model is
never handed a tool whose answer it already has.

`source` is bound rather than exposed, so a model cannot claim a human wrote its
content; `sessionId` and `domain` are bound because the scope supplies them.

The descriptions and parameter copy come from `tools.json`, the same manifest
the hosted MCP server publishes, compiled into the package at build time by
`scripts/sync_tool_docs.py`. A wording fix belongs upstream, not here.

**Only two failures are reported back to the model as text** — a malformed
request and a not-found handle, the two it can actually do something about.
Everything else, a rejected credential included, is thrown for your code to
handle. `AGENT_RECOVERABLE` names that line.

## Configuration

Arguments win over the environment, which wins over the defaults.

| Setting    | Option     | Environment       | Default                   |
| ---------- | ---------- | ----------------- | ------------------------- |
| Credential | `token`    | `MEMCO_API_TOKEN` | required                  |
| Endpoint   | `host`     | `MEMCO_API_HOST`  | `grpc.spark.memco.ai:443` |
| TLS        | `tls`      | —                 | `true`                    |
| Deadline   | `timeout`  | —                 | 30 seconds                |
| Log level  | `logLevel` | `MEMCO_LOG`       | `info`                    |

The credential is either a Memco API key or a session token issued for your
account; both go in the same header. `MEMCO_API_KEY` is still honoured but
warns.

Constructing a `Memco` sends nothing. `connect()` makes two calls. It probes the
service's health endpoint, so a bad host, port or TLS setting fails immediately
rather than on your first real call; that probe carries **no credential**, so it
cannot check one. It then calls `listDomains`, which does — a bad token fails
here — and which reports the input limits the service enforces. The client keeps
those and applies them from then on, so an oversized field is refused locally
instead of costing a round trip.

`connect()` is safe to call again after a failure; the channel is kept, so a
retry is cheap rather than a fresh handshake.

## Logging

Everything the SDK logs is written to `stderr` under a name beginning `memco.` —
`memco.client`, `memco.config`, `memco.validate` — so a record
says which part of the SDK spoke.

```
2026-08-31T12:00:00.000Z memco.client INFO connected to grpc.spark.memco.ai:443 (tls=true)
2026-08-31T12:00:00.120Z memco.client DEBUG Search ok in 118ms
2026-08-31T12:00:00.120Z memco.validate DEBUG tags trimmed from 62 to 50 by the service's cap
```

That last record is the only signal that the service's cap dropped some of what
you sent, so `debug` is worth turning on when a call does less than you expect.

**No credential is ever written to a record.** The `DEBUG` lines report where a
credential came from, never what it was.

`MEMCO_LOG` accepts `critical`, `error`, `warning`, `info`, `debug`, or `none`
to silence the SDK. A value it does not recognise warns and falls back to
`info`, because a mistyped environment variable should not stop a program
starting; a `logLevel` option it does not recognise throws, because that is your
own code.

If your application configures its own logging, prefer `MEMCO_LOG=none` over
`logLevel: 'none'`: it says the same thing without a library reaching into
process-wide state on the application's behalf.

## Operations

Reached as `client.memory`, or on a bound session without the scope arguments.

| Operation                           | What it does                                                  |
| ----------------------------------- | ------------------------------------------------------------- |
| `listDomains()`                     | The domains this credential may name, and the limits in force |
| `startSession(domain)`              | Open a session                                                |
| `withSession(domain)`               | Open one and bind it — await it for a disposable scope        |
| `search(query, options)`            | Find existing knowledge                                       |
| `getMemory(idx, options)`           | Fetch one memory in full                                      |
| `createMemory(options)`             | Save new knowledge                                            |
| `enrichMemory(options)`             | Add to a memory a search returned, or open a new one          |
| `shareFeedback(options)`            | Rate what a search returned                                   |
| `revertMemory(operationId)`         | Undo one of your own writes                                   |
| `importMemories(memories, options)` | Contribute many at once                                       |

`importMemories` splits a batch longer than the service accepts across several
calls and renumbers every outcome back into the array you passed, so
`outcome.index` always indexes your own input whatever the service's limit is.

## Errors

Every failure is typed. No raw transport error reaches a caller.

```
MemcoError
├── MemcoConfigError                 bad settings, or a closed client
└── MemcoAPIError                    the service reported a failure
    ├── MemcoAuthenticationError     UNAUTHENTICATED
    ├── MemcoPermissionError         PERMISSION_DENIED
    ├── MemcoInvalidRequestError     INVALID_ARGUMENT — also raised locally
    ├── MemcoNotFoundError           NOT_FOUND
    ├── MemcoPreconditionFailedError FAILED_PRECONDITION
    │   └── MemcoSunsetError         a version reached its end of life
    ├── MemcoResourceExhaustedError  RESOURCE_EXHAUSTED — read `.kind`
    ├── MemcoUnavailableError        UNAVAILABLE
    │   └── MemcoUnhealthyError      answered, but not serving
    ├── MemcoTimeoutError            DEADLINE_EXCEEDED
    └── MemcoInternalError           everything else
```

Two subclasses sit under classes you may also want to catch, so order your
checks with the specific one first: `MemcoSunsetError` before
`MemcoPreconditionFailedError`, and `MemcoUnhealthyError` before
`MemcoUnavailableError`.

Prefer `error.name` over `instanceof` if your dependency tree might load both
the ESM and the CommonJS build — two copies of a class are two identities,
whereas `error.name === 'MemcoNotFoundError'` holds either way.

**Some limits are checked locally.** Once `connect()` has run, an over-long
query or an over-large batch is refused before anything is sent, with the same
`MemcoInvalidRequestError` the service would have returned.

**Not every not-found is an error.** A revert reports what it actually removed:

```ts
const reverted = await client.memory.revertMemory(created.operationId!)
if (reverted.outcome === RevertOutcome.EXPIRED) {
  console.log('outside the revert window')
}
```

## Provenance

Which version of the service contract this package was generated from:

```ts
import { provenance } from '@memcoai/memco'

provenance().serverCommit // '93ec8030bac33afa1bf2aa376d5f5a406098d81a'
provenance().protos[0].path // 'memco/memory/v1/memory.proto'
```

## Contributing

See
[CONTRIBUTING.md](https://github.com/memcoai/memco/blob/main/CONTRIBUTING.md#nodejs).

```bash
make -C nodejs install
make -C nodejs test
make -C nodejs docs-serve
```

Everything under `client/` is generated from the service contract and is
replaced wholesale each time it is regenerated, so please do not edit it by
hand.

## Licence

[MIT](LICENSE), © Memco Labs, Inc.
