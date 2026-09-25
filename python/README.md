<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/memcoai/memcoai/main/assets/logo-dark.svg">
    <img alt="Memco" src="https://raw.githubusercontent.com/memcoai/memcoai/main/assets/logo.svg" width="320">
  </picture>
</p>

<p align="center">
  Python SDK for <b>Memco Shared Memory</b>.<br>
  <a href="https://memco.ai">memco.ai</a> &middot;
  <a href="https://docs.memco.ai">docs.memco.ai</a>
</p>

<p align="center">
  <a href="https://github.com/memcoai/memcoai/actions/workflows/ci_python.yaml"><img alt="CI (Python)" src="https://github.com/memcoai/memcoai/actions/workflows/ci_python.yaml/badge.svg?branch=main"></a>
  <img alt="Python versions" src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3775a9"><br>
  <img alt="Coverage" src="https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen">
  <a href="https://github.com/memcoai/memcoai/blob/main/LICENSE"><img alt="Licence" src="https://img.shields.io/badge/licence-MIT-blue"></a>
</p>

---

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share. An agent searches it before starting work and writes back what it
learned when it finishes, so what one agent establishes, every teammate's agent
can find.

This package wraps the generated gRPC client with connection handling,
credential management, typed results and typed errors. Every operation is
available synchronously and asynchronously, and the package is fully typed —
`py.typed` ships, so mypy and pyright check your calls.

**You need an account and an API key.** Create one at [memco.ai](https://memco.ai).

## Install

```bash
pip install memcoai
```

Requires Python 3.10 or newer.

## Quick start

```python
from memcoai import Memco

with Memco() as client:  # reads MEMCO_API_TOKEN
    for domain in client.memory.list_domains().domains:
        print(domain.slug, "-", domain.summary)

    session = client.memory.start_session("coding")

    result = session.search("how should a client authenticate against the memory API")
    for memory in result.memories:
        for insight in memory.insights:
            print(insight.title, insight.updated)
```

`start_session` returns a session with every session-bound operation already
applied — `search`, `share_feedback`, and the rest — so nothing above threads
an id through a call by hand. That matters because a call that silently drops
the id is still a valid call — it just stops being part of the series that
relates one task's work, which is the kind of mistake an agent makes and
nobody notices. `with_session` opens the same kind of session as a context
manager, for wherever that scoping reads better, and a single result rates
itself directly with `feedback`:

```python
with client.memory.with_session("coding") as session:
    result = session.search("how should a client authenticate")
    result.memories[0].feedback(relevant=True, correct=True)
```

Rating more than one result at once still goes through `share_feedback`
directly, with a `FeedbackRating` per result:

```python
from memcoai.types import FeedbackRating

session.share_feedback(
    feedback=[
        FeedbackRating(idx=memory.idx, relevant=True, correct=True) for memory in result.memories
    ]
)
```

Everything works asynchronously too, with the same method names:

```python
from memcoai import AsyncMemco

async with AsyncMemco() as client:
    async with client.memory.with_session("coding") as session:
        result = await session.search("how should a client authenticate")
```

Runnable programs covering the common workflows are in
[`examples/`](https://github.com/memcoai/memcoai/blob/main/python/examples/).

## Agents

Handing these operations to an LLM takes more than the calls: text telling a
model what each tool does and what to pass it, a JSON Schema for the arguments,
results rendered as text it can read, and the handover to whatever is driving
the loop. The SDK supplies all of it, from the session the tools are bound to:

```python
from memcoai import Memco, agent

with Memco() as client:
    entry = next(d for d in client.memory.list_domains().domains if d.slug == "coding")

    with client.memory.with_session("coding") as session:
        toolset = session.tools()

        create_agent(  # LangChain
            model,
            tools=toolset.to_langchain(),
            system_prompt=agent.briefing(entry, session.instructions),
        )
```

The same toolset speaks the other shapes, and runs what a model asks for when
you are driving the loop yourself:

```python
response = anthropic.messages.create(tools=toolset.to_anthropic(), ...)
result = toolset.call(block.name, block.input)           # -> text for the model

completion = openai.chat.completions.create(tools=toolset.to_openai(), ...)
result = toolset.call(call.function.name, call.function.arguments)   # JSON text is fine
```

`to_langchain` needs LangChain installed; the other two are plain data and need
nothing. `AsyncMemco`'s session has the same `tools()`, awaitable and described
identically.

The description a model reads for each tool is the service's, not this SDK's. It
arrives with every export as `memcoai/memory/tools.json` — the same copy the hosted
MCP server publishes — and is written into the docstrings the toolset is built
from, so a change of wording reaches you with a release rather than silently.

That includes the fields of each tag and rating a model sends, which are
written into the `Tag` and `FeedbackRating` docstrings. The session and its
domain are bound, so there is no `list_domains` or `start_session` tool
either — `agent.briefing()` says so, since the service's copy mentions both.

Every tool is bound to the session it was built from, so nothing a model sends
can change which session a call is recorded under, and arguments are validated
before anything is sent. A malformed request, an invented tool name, and a
handle that resolves to nothing all come back as text the model can act on —
`agent.AGENT_RECOVERABLE` is where that line is drawn. Everything else, a
rejected credential above all, is raised: no wording a model reads will fix it.

[`examples/langchain_agent.py`](https://github.com/memcoai/memcoai/blob/main/python/examples/langchain_agent.py)
is a complete agent in eighty lines, and defines no helpers of its own.

## Configuration

Arguments win over the environment, which wins over the defaults.

| Setting | Argument | Environment | Default |
|---|---|---|---|
| Credential | `token` | `MEMCO_API_TOKEN` | required, unless an API client's credentials are given |
| API client id | `client_id` | `MEMCO_CLIENT_ID` | — |
| API client secret | `client_secret` | `MEMCO_CLIENT_SECRET` | — |
| Issued token lifetime, in seconds | `token_lifetime` | — | the service's |
| Endpoint | `host` | `MEMCO_API_HOST` | `grpc.memco.ai:443` |
| TLS | `tls` | `MEMCO_API_TLS` (`true` or `false`) | `True` |
| Deadline | `timeout` | — | 30 seconds |
| Log level | `log_level` | `MEMCO_LOG` | `info` |

The credential is either a Memco API key or a session token issued for your
account; both go in the same header. `MEMCO_API_KEY` is still honoured but warns.

The deadline is the whole call's, and every method also takes its own
`timeout`. It starts when the call does and covers everything the call waits
for: a credential still being issued as well as the request, which gets
whatever is left, so a call ends when you said it would, failing with
`MemcoTimeoutError` if it has not finished. Two methods send several requests
and give each of them the deadline in turn: opening a session, and
`import_memories` with a batch above the service's cap per request.

TLS is on unless you turn it off, with `tls=False` or `MEMCO_API_TLS=false`, for
a plaintext endpoint such as a local development server. A client built without
TLS says so at `WARNING`, since its credential then crosses the wire readable.

A client can authenticate as an API client instead, with the `client_id` and
`client_secret` it was created with. The SDK exchanges them for a token, asking
for `token_lifetime` if you set it, and renews the token before it expires. The
two are used together, and which credential a client holds is decided in this
order:

1. A `token` argument wins, and the client variables are ignored. Passing it
   beside `client_id` or `client_secret` is an error.
2. `client_id` and `client_secret` arguments come next.
3. With no credential argument, `MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET` win
   over `MEMCO_API_TOKEN` when both are set.
4. Otherwise `MEMCO_API_TOKEN`, as above.

Half a pair, in arguments or in the environment, raises `MemcoConfigError`, as
does `token_lifetime` without client credentials.

Constructing a `Memco` makes two calls. It probes the service's health
endpoint, so a bad host, port or TLS setting fails immediately rather than on
your first call; that probe carries no credential, so it cannot check one.
It then calls `list_domains`, which does — a bad token fails here too — and
which reports the input limits the service enforces. The client keeps those and
applies them from then on, so an oversized field is refused locally instead of
costing a round trip.

With client credentials, the second call is the exchange for a token instead,
so bad credentials fail at construction too. An issued token carries no content
role of its own, so the memory operations refuse it. Such a client administers
networks and users, and reaches memory through sessions acting for your users,
both described below.

`AsyncMemco` cannot do any of this in `__init__` — it runs both on `connect()`,
which `async with` calls for you.

### Against a local development server

A local server usually serves plaintext, so point the client at it and turn TLS
off, either in the environment:

```bash
export MEMCO_API_HOST=localhost:50052 MEMCO_API_TLS=false MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...

python examples/map_your_users.py
```

or in code:

```python
with Memco(host="localhost:50052", tls=False, client_id="...", client_secret="...") as client:
    ...
```

Either way the client says so before it sends anything:

```text
memcoai._sync WARNING TLS is off: localhost:50052 is dialled in plaintext, credentials included
```

A token issued for the live service is rejected by a local server, so for the
token examples set `MEMCO_API_TOKEN` to one the local server issued.

## Logging

Everything the SDK logs goes to a logger under `memcoai` — `memcoai._sync`,
`memcoai._channel`, `memcoai._config` and so on — so configuring that one name
governs all of it, while a single noisy area can still be quietened on its own:

```python
logging.getLogger("memcoai").setLevel(logging.WARNING)
logging.getLogger("memcoai._channel").setLevel(logging.ERROR)
```

The SDK configures itself at `INFO` when you import it: a line when a client
connects, a line when it closes, and a rejected credential reported before it is
raised, since a client is often built somewhere the traceback does not reach.

```console
$ python app.py
2026-08-31 10:02:11,604 memcoai._sync INFO connected to grpc.memco.ai:443 (tls=True)
2026-08-31 10:02:14,318 memcoai._sync INFO closed connection to grpc.memco.ai:443
```

Set `MEMCO_LOG` to change that level — `debug`, `info`, `warning`, `error`,
`critical`, or `none` to turn it off — or pass `log_level` to either client,
which wins over the variable:

```python
with Memco(log_level="debug") as client:  # or log_level=logging.DEBUG
    ...
```

`debug` adds where your credential and endpoint came from, every RPC with its
outcome and duration, and — the one thing nothing else reveals — when a service
cap silently trimmed a list you passed:

```console
$ MEMCO_LOG=debug python app.py
2026-08-31 10:02:11,417 memcoai._config DEBUG credential taken from MEMCO_API_TOKEN
2026-08-31 10:02:11,417 memcoai._config DEBUG endpoint grpc.memco.ai:443 tls=True (host from the default)
2026-08-31 10:02:11,502 memcoai._sync DEBUG health check on grpc.memco.ai:443 ok in 84ms
2026-08-31 10:02:11,604 memcoai._sync DEBUG ListDomains ok in 101ms
2026-08-31 10:02:11,604 memcoai._sync INFO connected to grpc.memco.ai:443 (tls=True)
2026-08-31 10:02:11,731 memcoai._validate DEBUG tags trimmed from 62 to 50 by the service's cap
2026-08-31 10:02:11,905 memcoai._sync DEBUG Search ok in 173ms
```

**No credential is ever written to a record**, at any level.

### If your application configures its own logging

The SDK owns its output by default: it attaches a stderr handler to `memcoai` and
stops that logger propagating, so records go to the SDK's handler and no longer
reach the ones you attached further up, the root logger's included. That is what
makes it work with no setup — and it is the wrong shape for an application with
its own logging, where a redaction filter or log shipper on the root logger would
never see a memcoai record.

To take the SDK's records back into your own pipeline, set `MEMCO_LOG=none` in
the environment and configure the `memcoai` logger yourself:

```python
import logging
from memcoai import Memco  # with MEMCO_LOG=none set

logging.basicConfig(level=logging.INFO)
logging.getLogger("memcoai").setLevel(logging.INFO)

with Memco() as client:  # records flow through your handlers
    ...
```

```console
$ MEMCO_LOG=none python app.py
INFO:memcoai._sync:connected to grpc.memco.ai:443 (tls=True)
```

Use the environment variable rather than `log_level="none"` for this: the
variable is applied when `memcoai` is imported, while the argument is applied
inside the constructor — after which the level you set is gone and the
`connected` record has already been written.

Both settings are process-wide, because a logger is: two clients asking for
different levels means the last one constructed decides. An unrecognised
`MEMCO_LOG` warns and falls back to the default rather than failing the program;
an unrecognised `log_level` raises `MemcoConfigError`, because an argument is
your own code rather than a stray variable in the environment.

## Operations

The memory operations live on `client.memory`.

| Method | Purpose |
|---|---|
| `memory.list_domains()` | Which domains this credential may name, and their tag vocabulary |
| `memory.start_session(domain)` | Open a session, with every session-bound operation already applied |
| `memory.search(query, ...)` | Find memories answering a task-based query |
| `memory.get_memory(idx)` | Fetch a memory a search returned only as a reference |
| `memory.create_memory(...)` | Save new knowledge |
| `memory.enrich_memory(...)` | Add to a memory a search returned, or open a new one |
| `memory.share_feedback(...)` | Rate the results of one search |
| `memory.search(...).memories[0].feedback(...)` | Rate a single result directly, no `FeedbackRating` needed |
| `memory.revert_memory(operation_id)` | Undo one of your own writes |
| `memory.import_memories(memories, ...)` | Contribute many memories at once, splitting the batch as the service requires |

## Networks and users

A client built with an API client's credentials administers your organization:
its memory networks, which scope what the people placed in them can find, and
its external users — your own users, known to Memco by your id for them. The
API client needs the `network-management` and `user-management` scopes.

```python
from memcoai import Memco

with Memco(client_id="...", client_secret="...") as client:
    root = client.networks.list(parent_id="root", domain="coding").networks[0]
    acme = client.networks.create(name="Acme", parent_id=root.id, scope="customer")

    user = client.users.create("customer-42", roles=["creator"], name="Ada")
    client.networks.add_member(acme.id, user.id)
```

| Method | Purpose |
|---|---|
| `networks.list(...)` | One page of networks, filtered by name, scope, owner, domain, parent or id |
| `networks.create(...)`, `update(...)`, `delete(...)` | Manage a network; deleting one takes everything placed in it |
| `networks.list_members(...)`, `add_member(...)`, `remove_member(...)` | Who is placed in a network |
| `networks.list_groups(...)`, `list_group_members(...)`, `add_group(...)`, `remove_group(...)` | Identity-provider groups, for enterprise organizations |
| `users.list(...)`, `get(...)`, `create(...)`, `update(...)`, `delete(...)` | Manage external users |
| `users.list_keys(...)`, `create_key(...)`, `delete_key(...)` | An external user's API keys |

An external user has no sign-in of its own, never holds `admin`, and can be
placed only in a customer network. Every `client.users` method names the user by
your `external_id`; `add_member` takes Memco's own `user.id`. On `update`, an
argument left as `None` leaves its field as it is, and an empty string clears
it. Pages count from 1. A key's value is returned once, by `create_key`, and
never again.

## Acting for your users

Passing `external_id` to `start_session` or `with_session` opens a session that
acts as one of your external users, so what it finds is what that user may
find, and what it writes is that user's:

```python
with Memco(client_id="...", client_secret="...") as client:
    with client.memory.with_session("coding", external_id="customer-42") as session:
        result = session.search("how should a client authenticate")
```

Opening one runs in a fixed order. The SDK has the service mint an
impersonation key for the user, under your client's token; lists the domains
under that key, so the session learns the limits and any deprecation notice that
apply to the user; and only then starts the session. Every call made through
the session — its tools, and the memories it returns, included — carries that
key, and never your client's token. The API client needs the admin grant, and
what the session can reach is scoped by the network the user is placed in.

**Close the session.** Leaving the `with` block, or calling `session.close()`,
ends the key on the service at once. The service caps how many live keys each
user may hold, so a session left open holds one of them until its key expires.
A session dropped without being closed has its key ended too, but only
eventually: once it, and every tool and memory it returned, has been
garbage-collected, Python issues a `ResourceWarning`, as for an unclosed file,
and the client ends the key at its next call. Closing the client ends any key
still live. A key the service cannot end is logged by its id, never its value;
the next session opened for the same user tries again, and the key expires on
its own regardless. After closing, a call through the session raises
`MemcoConfigError`. A session opened without `external_id` holds nothing, so
closing it changes nothing.

**Keys renew themselves.** A key is replaced once four fifths of its lifetime
have passed, timed by the service's own count of the seconds it has left, so
this host's clock does not come into it (a service that does not send that
count yet has its expiry time read against this host's clock instead). The new
one is minted in the background, and calls go on with the
key held until it arrives, so a slow renewal holds none of them up. The key
replaced is ended as soon as no call is still using it, so a long session never
fails for an expired key, and a renewal never revokes one under a call in
flight. The client's own token renews the same way. If a renewal fails -- the
token service is down, say, or the user already holds as many live keys as the
service allows -- the key or token held stays in use until it expires, a
warning is logged, and the next call tries again.

Each session carries its own key, so sessions for different users run side by
side on one client — from several threads with `Memco`, or concurrently with
`AsyncMemco`:

```python
import asyncio

from memcoai import AsyncMemco


async def answer(client: AsyncMemco, user: str, question: str) -> None:
    async with client.memory.with_session("coding", external_id=user) as session:
        result = await session.search(question)
        ...


async def main() -> None:
    async with AsyncMemco() as client:  # reads MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET
        await asyncio.gather(
            answer(client, "customer-42", "how do I rotate an API key"),
            answer(client, "customer-7", "why was my import rejected"),
        )


asyncio.run(main())
```

## Errors

Every failure is a subclass of `MemcoError`, so no raw `grpc.RpcError` ever
reaches you.

```
MemcoError
├── MemcoConfigError                  bad configuration; no request was sent
└── MemcoAPIError                     the service returned an error status
    ├── MemcoAuthenticationError      credential missing, expired or rejected
    ├── MemcoPermissionError          credential lacks the scope or role
    ├── MemcoInvalidRequestError      malformed request
    ├── MemcoNotFoundError            handle resolved to nothing visible
    ├── MemcoAlreadyExistsError       what it would create already exists
    ├── MemcoPreconditionFailedError  a precondition is unmet
    │   ├── MemcoSunsetError          past its sunset; carries .kind
    │   ├── MemcoUserAlreadyAssignedNetworkError
    │   │                             already in another network of the domain
    │   └── MemcoExternalUserNeedsCustomerNetworkError
    │                                 only a customer network takes an external user
    ├── MemcoResourceExhaustedError   rate limit or quota; carries .kind
    ├── MemcoUnavailableError         service unreachable
    │   └── MemcoUnhealthyError       reachable, but reporting not-serving
    ├── MemcoTimeoutError             deadline exceeded
    └── MemcoInternalError            everything else
```

`MemcoConfigError` means nothing was sent, with one exception: a session key
that this host's clock already reads as expired when it arrives. Only a service
that does not report the seconds a key has left can produce it, with a host
clock well ahead of its own; the key's mint, and the `EndImpersonation` that
ends it at once, have been sent by then. Correct the system clock.

Two behaviours worth knowing:

**Some limits are checked locally.** Oversized fields and missing argument
combinations raise `MemcoInvalidRequestError` before any request is sent, so a
malformed call costs no round trip.

**Not every "not found" is an error.** `revert_memory` reports a missing,
expired or moderated operation through `RevertResult.outcome` rather than
raising, because each describes state you can act on rather than a failure.

```python
result = client.memory.revert_memory("create-8fj2k1")
if result.outcome is RevertOutcome.EXPIRED:
    print("outside the revert window")
```

[`examples/handling_errors.py`](https://github.com/memcoai/memcoai/blob/main/python/examples/handling_errors.py) works through every
failure mode and what to do about each.

## Provenance

The package records which version of the service contract its generated client
was built from:

```python
from memcoai import provenance

provenance().server_commit  # the commit this wheel was built from
[proto.path for proto in provenance().protos]
# ['memcoai/admin/v1/admin.proto', 'memcoai/auth/v1/auth.proto', 'memcoai/memory/v1/memory.proto']
```

## Contributing

Bug reports and pull requests are welcome, and you do not need access to Memco's
servers to work on this — the test suite runs against an in-process gRPC server,
so everything passes offline. The
[Python section of CONTRIBUTING.md](https://github.com/memcoai/memcoai/blob/main/CONTRIBUTING.md#python)
covers local setup, the checks, and the test conventions.

```bash
make install                # from the repository root
make check                  # lint, typecheck, test, docs — everything CI runs
make -C python docs-serve   # build the reference and read it locally
```

The generated clients under `memcoai/memory/`, `memcoai/admin/` and
`memcoai/auth/` are produced from the service contracts and are replaced
wholesale when regenerated; everything else in `memcoai/` is hand-written.

## Licence

[MIT](https://github.com/memcoai/memcoai/blob/main/LICENSE) &copy; Memco Labs, Inc.
