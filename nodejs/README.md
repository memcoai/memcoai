<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/memcoai/memcoai/main/assets/logo-dark.svg">
    <img alt="Memco" src="https://raw.githubusercontent.com/memcoai/memcoai/main/assets/logo.svg" width="320">
  </picture>
</p>

<p align="center">
  Node.js SDK for <b>Memco Shared Memory</b>.<br>
  <a href="https://memco.ai">memco.ai</a> &middot;
  <a href="https://docs.memco.ai">docs.memco.ai</a>
</p>

<p align="center">
  <a href="https://github.com/memcoai/memcoai/actions/workflows/ci_nodejs.yaml"><img alt="CI (Node.js)" src="https://github.com/memcoai/memcoai/actions/workflows/ci_nodejs.yaml/badge.svg?branch=main"></a>
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
npm install @memco/memcoai
```

Node 22 or newer. The package ships both ESM and CommonJS builds with full type
declarations for each, so `import` and `require` both work.

```ts
import { Memco } from '@memco/memcoai'
```

```js
const { Memco } = require('@memco/memcoai')
```

## Quick start

```ts
import { Memco } from '@memco/memcoai'

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

Rating a single memory rather than a whole batch is shorter directly on the
result: `await result.memories[0].feedback({ relevant: true, correct: true })`
submits one rating for that memory's own idx, bound to the session it was
found in — no `FeedbackRating` array to build by hand.

`withSession` sends nothing until it is awaited, and awaiting it twice returns
the same session rather than opening a second one.

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
[`examples/`](https://github.com/memcoai/memcoai/tree/main/nodejs/examples).

## Agents

A bound session carries the tools a model needs, described in the service's own
words rather than each SDK's:

```ts
import { briefing } from '@memco/memcoai'
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
content; `sessionId` and `domain` are bound because the session supplies them.

The descriptions and parameter copy come from `tools.json`, the same manifest
the hosted MCP server publishes, compiled into the package at build time by
`scripts/sync_tool_docs.py`. A wording fix belongs upstream, not here.

**Only two failures are reported back to the model as text** — a malformed
request and a not-found handle, the two it can actually do something about.
Everything else, a rejected credential included, is thrown for your code to
handle. `AGENT_RECOVERABLE` names that line.

## Configuration

Arguments win over the environment, which wins over the defaults.

| Setting                           | Option          | Environment                         | Default                                                |
| --------------------------------- | --------------- | ----------------------------------- | ------------------------------------------------------ |
| Credential                        | `token`         | `MEMCO_API_TOKEN`                   | required, unless an API client's credentials are given |
| API client id                     | `clientId`      | `MEMCO_CLIENT_ID`                   | —                                                      |
| API client secret                 | `clientSecret`  | `MEMCO_CLIENT_SECRET`               | —                                                      |
| Issued token lifetime, in seconds | `tokenLifetime` | —                                   | the service's                                          |
| Endpoint                          | `host`          | `MEMCO_API_HOST`                    | `grpc.memco.ai:443`                                    |
| TLS                               | `tls`           | `MEMCO_API_TLS` (`true` or `false`) | `true`                                                 |
| Deadline                          | `timeout`       | —                                   | 30 seconds                                             |
| Log level                         | `logLevel`      | `MEMCO_LOG`                         | `info`                                                 |

The credential is either a Memco API key or a session token issued for your
account; both go in the same header. `MEMCO_API_KEY` is still honoured but
warns.

The `timeout` — the client's, or one passed to a single call — is one deadline
for the whole call. If the call first has to wait for a credential, the
client's first token or a new key once the current one has expired, that wait
counts against it and the RPC has what is left. `startSession` and
`importMemories`, which make several RPCs, apply it to each of them.

TLS is on unless you turn it off, with `tls: false` or `MEMCO_API_TLS=false`,
for a plaintext endpoint such as a local development server. Any other value of
`MEMCO_API_TLS` is refused rather than guessed at. A client built without TLS
says so at `WARNING` as it is constructed, since its credential then crosses
the wire readable.

A client can authenticate as an API client instead, with the `clientId` and
`clientSecret` it was created with. The SDK exchanges them for a token, asking
for `tokenLifetime` if you set it, and renews the token before it expires. The
two are used together, and which credential a client holds is decided in this
order:

1. A `token` option wins, and the client variables are ignored. Passing it
   beside `clientId` or `clientSecret` is an error.
2. `clientId` and `clientSecret` options come next.
3. With no credential option, `MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET` win
   over `MEMCO_API_TOKEN` when both are set.
4. Otherwise `MEMCO_API_TOKEN`, as above.

Half a pair, in options or in the environment, throws `MemcoConfigError`, as
does a blank option, or `tokenLifetime` without client credentials. A blank
environment variable reads as unset.

Constructing a `Memco` sends nothing. `connect()` makes two calls. It probes the
service's health endpoint, so a bad host, port or TLS setting fails immediately
rather than on your first real call; that probe carries **no credential**, so it
cannot check one. It then calls `listDomains`, which does — a bad token fails
here — and which reports the input limits the service enforces. The client keeps
those and applies them from then on, so an oversized field is refused locally
instead of costing a round trip.

With client credentials, the second call is the exchange for a token instead,
so bad credentials fail in `connect()` too. An issued token carries no content
role of its own, so the service refuses memory calls made with it. Such a
client administers networks and users, and reaches memory through sessions
acting for your users, both described below.

`connect()` is safe to call again after a failure; the channel is kept, so a
retry is cheap rather than a fresh handshake.

### Against a local development server

A local server usually serves plaintext, so point the client at it and turn TLS
off, either in the environment:

```bash
export MEMCO_API_HOST=localhost:50052 MEMCO_API_TLS=false MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...

node build/js/examples/map_your_users.js
```

or in code:

```ts
const client = await new Memco({
  host: 'localhost:50052',
  tls: false,
  clientId: '...',
  clientSecret: '...'
}).connect()
```

Either way the client says so before it sends anything:

```
2026-09-25T10:02:11.417Z memco.client WARNING TLS is off: localhost:50052 is dialled in plaintext, credentials included
```

A token issued for the live service is rejected by a local server, so for the
token examples set `MEMCO_API_TOKEN` to one the local server issued.

## Logging

Everything the SDK logs is written to `stderr` under a name beginning `memco.` —
`memco.client`, `memco.config`, `memco.validate` — so a record
says which part of the SDK spoke.

```
2026-08-31T12:00:00.000Z memco.client INFO connected to grpc.memco.ai:443 (tls=true)
2026-08-31T12:00:00.120Z memco.client DEBUG Search ok in 118ms
2026-08-31T12:00:00.120Z memco.validate DEBUG tags trimmed from 62 to 50 by the service's cap
```

That last record is the only signal that the service's cap dropped some of what
you sent, so `debug` is worth turning on when a call does less than you expect.

**No credential is ever written to a record** — not a token, not a client
secret, and not the key a session acting for one of your users holds. The
`DEBUG` lines report where a credential came from, never what it was, and a key
is named by its id alone.

`MEMCO_LOG` accepts `critical`, `error`, `warning`, `info`, `debug`, or `none`
to silence the SDK. A value it does not recognise warns and falls back to
`info`, because a mistyped environment variable should not stop a program
starting; a `logLevel` option it does not recognise throws, because that is your
own code.

If your application configures its own logging, prefer `MEMCO_LOG=none` over
`logLevel: 'none'`: it says the same thing without a library reaching into
process-wide state on the application's behalf.

## Operations

Reached as `client.memory`, or on a session without the session/domain arguments.

| Operation                           | What it does                                                                                                                      |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `listDomains()`                     | The domains this credential may name, and the limits in force                                                                     |
| `startSession(domain, options)`     | Open a session and return it right away — the same bound operations `withSession` yields; `externalId` acts for one of your users |
| `withSession(domain, options)`      | Open one lazily — await it once for the same disposable session, however many times it's referenced                               |
| `search(query, options)`            | Find existing knowledge                                                                                                           |
| `getMemory(idx, options)`           | Fetch one memory in full                                                                                                          |
| `createMemory(options)`             | Save new knowledge                                                                                                                |
| `enrichMemory(options)`             | Add to a memory a search returned, or open a new one                                                                              |
| `shareFeedback(options)`            | Rate what a search returned                                                                                                       |
| `revertMemory(operationId)`         | Undo one of your own writes                                                                                                       |
| `importMemories(memories, options)` | Contribute many at once                                                                                                           |

`importMemories` splits a batch longer than the service accepts across several
calls and renumbers every outcome back into the array you passed, so
`outcome.index` always indexes your own input whatever the service's limit is.

## Networks and users

A client built with an API client's credentials administers your organization:
its memory networks, which scope what the people placed in them can find, and
its external users — your own users, known to Memco by your id for them. The
API client needs the `network-management` and `user-management` scopes.

```ts
import { Memco } from '@memco/memcoai'

await using client = await new Memco({ clientId, clientSecret }).connect()

const [root] = (
  await client.networks.list({ parentId: 'root', domain: 'coding' })
).networks
const acme = await client.networks.create({
  name: 'Acme',
  parentId: root.id,
  scope: 'customer'
})

const user = await client.users.create('customer-42', {
  roles: ['creator'],
  name: 'Ada'
})
await client.networks.addMember(acme.id, { userId: user.id })
```

| Method                                                                                   | Purpose                                                                    |
| ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `networks.list(options)`                                                                 | One page of networks, filtered by name, scope, owner, domain, parent or id |
| `networks.create(options)`, `update(id, options)`, `delete(id)`                          | Manage a network; deleting one takes everything placed in it               |
| `networks.listMembers(id)`, `addMember(id, options)`, `removeMember(...)`                | Who is placed in a network                                                 |
| `networks.listGroups(...)`, `listGroupMembers(...)`, `addGroup(...)`, `removeGroup(...)` | Identity-provider groups, for enterprise organizations                     |
| `users.list(options)`, `get(externalId)`, `create(...)`, `update(...)`, `delete(...)`    | Manage external users                                                      |
| `users.listKeys(externalId)`, `createKey(...)`, `deleteKey(...)`                         | An external user's API keys                                                |

As everywhere in this SDK, at most the first argument is positional — the
network, group or user a method acts on — and everything else, required or
not, goes in one options object: `addMember(networkId, { userId, force })`,
`deleteKey(externalId, { keyId })`.

An external user has no sign-in of its own, never holds `admin`, and can be
placed only in a customer network. Every `client.users` method names the user
by your `externalId`; `addMember` takes Memco's own `user.id`. A user is placed
in one network per domain: placing them in a second is refused with
`MemcoUserAlreadyAssignedNetworkError`, naming the network they are in, unless
you pass `force: true`, which moves them and reports where from in
`movedFrom`. On `update`, a field left out stays as it is, and an empty string
clears it. Pages count from 1. A key's value is returned once, by `createKey`,
and never again — and never by `console.log` or `JSON.stringify` of the result
either: read `created.value` where you mean to.

## Acting for your users

Passing `externalId` to `startSession` or `withSession` opens a session that
acts as one of your external users, so what it finds is what that user may
find, and what it writes is that user's:

```ts
await using client = await new Memco({ clientId, clientSecret }).connect()
await using session = await client.memory.withSession('coding', {
  externalId: 'customer-42'
})

const result = await session.search('how should a client authenticate')
```

Opening one runs in a fixed order. The SDK has the service mint an
impersonation key for the user, under your client's token; lists the domains
under that key, so the session learns the limits and any deprecation notice that
apply to the user; and only then starts the session. Every call made through
the session — its tools, and the memories it returns, included — carries that
key, and never your client's token. The API client needs the admin grant, and
what the session can reach is scoped by the network the user is placed in.

**Close the session.** Leaving the `await using` block, or calling
`session.close()`, ends the key on the service. The service caps how many live
keys each user may hold, so a session left open holds one of them until its key
expires. A session dropped without closing has its key ended eventually — once
the session has been garbage-collected, by the next call the client makes — but
closing ends it at once, and is the only way that says when. After closing, a
call through the session throws `MemcoConfigError`. A session opened without
`externalId` holds nothing, so closing it changes nothing.

If the service cannot end a key when its session closes, a warning names the
key by its id, never its value, and the client tries again before it next opens
a session for that user, and when it closes. Closing the client ends every key
still live; one it cannot end is logged the same way and expires on its own.

**Keys renew themselves.** A key is replaced once four fifths of its lifetime
have passed. The lifetime is the one the service reports for the key, counted
from before it was asked for on this machine's monotonic clock, so a skewed
wall clock cannot move it; with a service too old to report it, the key's
expiry time is read against the wall clock instead, and a key that arrives
already expired by it is refused with a `MemcoConfigError` naming the clock.
The key a renewal replaces is ended as soon as no call is still using
it, so a long session never fails for an expired key, and a renewal never
revokes one under a call in flight. The renewal runs in the background: the
calls that find the key due go on under it, since it has not expired, and the
calls after the renewal lands carry the new one. If the renewal fails — the
user already holds as many live keys as the service allows, say — the failure
is logged once at `WARNING`, naming the key by its id, and the next call past
the renewal point tries again. Only a call with no key to use — the first, or
one after the key has expired — waits for a new one, and that wait counts
against its own `timeout`; calls waiting together share one attempt and its
failure. The
client's own token renews the same way.

Each session carries its own key, so sessions for different users run side by
side on one client:

```ts
async function answer(user: string, question: string): Promise<void> {
  await using session = await client.memory.withSession('coding', {
    externalId: user
  })
  const result = await session.search(question)
  // ...
}

await Promise.all([
  answer('customer-42', 'how do I rotate an API key'),
  answer('customer-7', 'why was my import rejected')
])
```

## Errors

Every failure is typed. No raw transport error reaches a caller.

```
MemcoError
├── MemcoConfigError                 bad settings, or a closed client or session
└── MemcoAPIError                    the service reported a failure
    ├── MemcoAuthenticationError     UNAUTHENTICATED
    ├── MemcoPermissionError         PERMISSION_DENIED
    ├── MemcoInvalidRequestError     INVALID_ARGUMENT — also raised locally
    ├── MemcoNotFoundError           NOT_FOUND
    ├── MemcoAlreadyExistsError      ALREADY_EXISTS
    ├── MemcoPreconditionFailedError FAILED_PRECONDITION — read `.metadata`
    │   ├── MemcoSunsetError         a version reached its end of life
    │   ├── MemcoUserAlreadyAssignedNetworkError
    │   │                            already in another network of the domain
    │   └── MemcoExternalUserNeedsCustomerNetworkError
    │                                only a customer network takes an external user
    ├── MemcoResourceExhaustedError  RESOURCE_EXHAUSTED — read `.kind`
    ├── MemcoUnavailableError        UNAVAILABLE
    │   └── MemcoUnhealthyError      answered, but not serving
    ├── MemcoTimeoutError            DEADLINE_EXCEEDED
    └── MemcoInternalError           everything else
```

Some subclasses sit under classes you may also want to catch, so order your
checks with the specific one first: `MemcoSunsetError` and the two placement
refusals before `MemcoPreconditionFailedError`, and `MemcoUnhealthyError`
before `MemcoUnavailableError`.

A precondition failure carries the detail the service attached as
`error.metadata`, a frozen object that is empty when there is none. The two
placement refusals also read it for you: `currentNetworkId` and
`currentNetworkName` on `MemcoUserAlreadyAssignedNetworkError`, and
`requiredNetworkScope` on `MemcoExternalUserNeedsCustomerNetworkError`. Which
of them a refusal is comes from the reason the service names, never from its
message.

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
import { provenance } from '@memco/memcoai'

provenance().serverCommit // '93ec8030bac33afa1bf2aa376d5f5a406098d81a'
provenance().protos.map(proto => proto.path)
// ['memcoai/admin/v1/admin.proto', 'memcoai/auth/v1/auth.proto', 'memcoai/memory/v1/memory.proto']
```

## Contributing

See
[CONTRIBUTING.md](https://github.com/memcoai/memcoai/blob/main/CONTRIBUTING.md#nodejs).

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
