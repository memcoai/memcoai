<p align="center">
  <img alt="Memco" src="https://raw.githubusercontent.com/memcoai/memco/main/assets/logo.svg" width="320">
</p>

<p align="center">
  Python SDK for <b>Memco Shared Memory</b>.<br>
  <a href="https://memco.ai">memco.ai</a> &middot;
  <a href="https://docs.memco.ai">docs.memco.ai</a>
</p>

<p align="center">
  <a href="https://github.com/memcoai/memco/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/memcoai/memco/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Python versions" src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3775a9">
  <img alt="Coverage" src="https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen">
  <a href="LICENSE"><img alt="Licence" src="https://img.shields.io/badge/licence-MIT-blue"></a>
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
pip install memco
```

Requires Python 3.10 or newer.

## Quick start

```python
from memco import Memco

with Memco() as client:  # reads MEMCO_API_TOKEN
    for domain in client.memory.list_domains().domains:
        print(domain.slug, "-", domain.summary)

    session = client.memory.start_session("coding")

    result = client.memory.search(
        "how should a client authenticate against the memory API",
        session_id=session.session_id,
    )
    for memory in result.memories:
        for insight in memory.insights:
            print(insight.title, insight.updated)
```

Wherever a session outlives a line or two, bind it once with `with_session`
instead of threading the id through every call. A call that silently drops the
id is still a valid call — it just stops being part of the series that relates
one task's work, which is the kind of mistake an agent makes and nobody notices:

```python
from memco.types import FeedbackRating

with client.memory.with_session("coding") as session:
    result = session.search("how should a client authenticate")
    session.share_feedback(
        feedback=[
            FeedbackRating(idx=result.memories[0].idx, relevant=True, correct=True),
        ]
    )
```

Everything works asynchronously too, with the same method names:

```python
from memco import AsyncMemco

async with AsyncMemco() as client:
    async with client.memory.with_session("coding") as session:
        result = await session.search("how should a client authenticate")
```

Runnable programs covering the common workflows are in
[`examples/`](https://github.com/memcoai/memco/blob/main/python/examples/).

## Agents

Handing these operations to an LLM takes more than the calls: text telling a
model what each tool does and what to pass it, a JSON Schema for the arguments,
results rendered as text it can read, and the handover to whatever is driving
the loop. The SDK supplies all of it, from the session the tools are bound to:

```python
from memco import Memco, agent

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
arrives with every export as `memco/memory/tools.json` — the same copy the hosted
MCP server publishes — and is written into the docstrings the toolset is built
from, so a change of wording reaches you with a release rather than silently.

Three parameters keep their own wording: `tags`, `feedback` and `source` are
typed objects here and XML strings over MCP, so the service's copy would
describe an encoding these schemas reject. The session and its domain are bound,
so there is no `list_domains` or `start_session` tool either — `agent.briefing()`
says so, since the service's copy mentions both.

Every tool is bound to the session it was built from, so nothing a model sends
can change which session a call is recorded under, and arguments are validated
before anything is sent. A malformed request, an invented tool name, and a
handle that resolves to nothing all come back as text the model can act on —
`agent.AGENT_RECOVERABLE` is where that line is drawn. Everything else, a
rejected credential above all, is raised: no wording a model reads will fix it.

[`examples/langchain_agent.py`](https://github.com/memcoai/memco/blob/main/python/examples/langchain_agent.py)
is a complete agent in eighty lines, and defines no helpers of its own.

## Configuration

Arguments win over the environment, which wins over the defaults.

| Setting | Argument | Environment | Default |
|---|---|---|---|
| Credential | `token` | `MEMCO_API_TOKEN` | required |
| Endpoint | `host` | `MEMCO_API_HOST` | `grpc.spark.memco.ai:443` |
| TLS | `tls` | — | `True` |
| Deadline | `timeout` | — | 30 seconds |
| Log level | `log_level` | `MEMCO_LOG` | `info` |

The credential is either a Memco API key or a session token issued for your
account; both go in the same header. `MEMCO_API_KEY` is still honoured but warns.

Constructing a `Memco` makes two calls. It probes the service's health
endpoint, so a bad host, port or TLS setting fails immediately rather than on
your first call; that probe carries no credential, so it cannot check one.
It then calls `list_domains`, which does — a bad token fails here too — and
which reports the input limits the service enforces. The client keeps those and
applies them from then on, so an oversized field is refused locally instead of
costing a round trip.

`AsyncMemco` cannot do any of this in `__init__` — it runs both on `connect()`,
which `async with` calls for you.

## Logging

Everything the SDK logs goes to a logger under `memco` — `memco._sync`,
`memco._channel`, `memco._config` and so on — so configuring that one name
governs all of it, while a single noisy area can still be quietened on its own:

```python
logging.getLogger("memco").setLevel(logging.WARNING)
logging.getLogger("memco._channel").setLevel(logging.ERROR)
```

The SDK configures itself at `INFO` when you import it: a line when a client
connects, a line when it closes, and a rejected credential reported before it is
raised, since a client is often built somewhere the traceback does not reach.

```console
$ python app.py
2026-08-31 10:02:11,604 memco._sync INFO connected to grpc.spark.memco.ai:443 (tls=True)
2026-08-31 10:02:14,318 memco._sync INFO closed connection to grpc.spark.memco.ai:443
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
2026-08-31 10:02:11,417 memco._config DEBUG credential taken from MEMCO_API_TOKEN
2026-08-31 10:02:11,417 memco._config DEBUG endpoint grpc.spark.memco.ai:443 tls=True (host from the default)
2026-08-31 10:02:11,502 memco._sync DEBUG health check on grpc.spark.memco.ai:443 ok in 84ms
2026-08-31 10:02:11,604 memco._sync DEBUG ListDomains ok in 101ms
2026-08-31 10:02:11,604 memco._sync INFO connected to grpc.spark.memco.ai:443 (tls=True)
2026-08-31 10:02:11,731 memco._validate DEBUG tags trimmed from 62 to 50 by the service's cap
2026-08-31 10:02:11,905 memco._sync DEBUG Search ok in 173ms
```

**No credential is ever written to a record**, at any level.

### If your application configures its own logging

The SDK owns its output by default: it attaches a stderr handler to `memco` and
stops that logger propagating, so records go to the SDK's handler and no longer
reach the ones you attached further up, the root logger's included. That is what
makes it work with no setup — and it is the wrong shape for an application with
its own logging, where a redaction filter or log shipper on the root logger would
never see a memco record.

To take the SDK's records back into your own pipeline, set `MEMCO_LOG=none` in
the environment and configure the `memco` logger yourself:

```python
import logging
from memco import Memco  # with MEMCO_LOG=none set

logging.basicConfig(level=logging.INFO)
logging.getLogger("memco").setLevel(logging.INFO)

with Memco() as client:  # records flow through your handlers
    ...
```

```console
$ MEMCO_LOG=none python app.py
INFO:memco._sync:connected to grpc.spark.memco.ai:443 (tls=True)
```

Use the environment variable rather than `log_level="none"` for this: the
variable is applied when `memco` is imported, while the argument is applied
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
| `memory.start_session(domain)` | Open a session so related searches are recorded as one series |
| `memory.search(query, ...)` | Find memories answering a task-based query |
| `memory.get_memory(idx)` | Fetch a memory a search returned only as a reference |
| `memory.create_memory(...)` | Save new knowledge |
| `memory.enrich_memory(...)` | Add to a memory a search returned, or open a new one |
| `memory.share_feedback(...)` | Rate the results of one search |
| `memory.revert_memory(operation_id)` | Undo one of your own writes |
| `memory.import_memories(memories, ...)` | Contribute many memories at once, splitting the batch as the service requires |

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
    ├── MemcoPreconditionFailedError  a precondition is unmet
    │   └── MemcoSunsetError          past its sunset; carries .kind
    ├── MemcoResourceExhaustedError   rate limit or quota; carries .kind
    ├── MemcoUnavailableError         service unreachable
    │   └── MemcoUnhealthyError       reachable, but reporting not-serving
    ├── MemcoTimeoutError             deadline exceeded
    └── MemcoInternalError            everything else
```

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

[`examples/handling_errors.py`](https://github.com/memcoai/memco/blob/main/python/examples/handling_errors.py) works through every
failure mode and what to do about each.

## Provenance

The package records which version of the service contract its generated client
was built from:

```python
from memco import provenance

provenance().server_commit  # the commit this wheel was built from
provenance().protos[0].path  # 'memco/memory/v1/memory.proto'
```

## Contributing

Bug reports and pull requests are welcome, and you do not need access to Memco's
servers to work on this — the test suite runs against an in-process gRPC server,
so everything passes offline. The
[Python section of CONTRIBUTING.md](https://github.com/memcoai/memco/blob/main/CONTRIBUTING.md#python)
covers local setup, the checks, and the test conventions.

```bash
make install                # from the repository root
make check                  # lint, typecheck, test, docs — everything CI runs
make -C python docs-serve   # build the reference and read it locally
```

The generated client under `memco/memory/` is produced from the service
contract and is replaced wholesale when regenerated; everything else in
`memco/` is hand-written.

## Licence

[MIT](https://github.com/memcoai/memco/blob/main/LICENSE) &copy; Memco Labs, Inc.
