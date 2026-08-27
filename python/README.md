<p align="center">
  <img alt="Memco" src="https://raw.githubusercontent.com/memcoai/memco/main/assets/logo.svg" width="320">
</p>

<p align="center">
  Python SDK for <b>Memco Shared Memory</b>.<br>
  <a href="https://memco.ai">memco.ai</a> &middot;
  <a href="https://docs.memco.ai">docs.memco.ai</a>
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
    for domain in client.memory.describe_domains().domains:
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
    entry = next(d for d in client.memory.describe_domains().domains if d.slug == "coding")

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

The credential is either a Memco API key or a session token issued for your
account; both go in the same header. `MEMCO_API_KEY` is still honoured but warns.

Constructing a `Memco` makes two calls. It probes the service's health
endpoint, so a bad host, port or TLS setting fails immediately rather than on
your first call; that probe carries no credential, so it cannot check one.
It then calls `describe_domains`, which does — a bad token fails here too — and
which reports the input limits the service enforces. The client keeps those and
applies them from then on, so an oversized field is refused locally instead of
costing a round trip.

`AsyncMemco` cannot do any of this in `__init__` — it runs both on `connect()`,
which `async with` calls for you.

A rejected credential is written to the `memco` logger before it is raised,
since a client is often built somewhere the traceback does not reach:

```python
logging.getLogger("memco").addHandler(logging.StreamHandler())
```

## Operations

The memory operations live on `client.memory`.

| Method | Purpose |
|---|---|
| `memory.describe_domains()` | Which domains this credential may name, and their tag vocabulary |
| `memory.start_session(domain)` | Open a session so related searches are recorded as one series |
| `memory.search(query, ...)` | Find memories answering a task-based query |
| `memory.get_memory(idx)` | Fetch a memory a search returned only as a reference |
| `memory.create_memory(...)` | Save new knowledge |
| `memory.enrich_memory(...)` | Add to a memory a search returned, or open a new one |
| `memory.share_feedback(...)` | Rate the results of one search |
| `memory.revert_memory(operation_id)` | Undo one of your own writes |

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
