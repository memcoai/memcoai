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

Everything works asynchronously too, with the same method names:

```python
from memco import AsyncMemco

async with AsyncMemco() as client:
    session = await client.memory.start_session("coding")
    result = await client.memory.search("...", session_id=session.session_id)
```

Runnable programs covering the common workflows are in
[`examples/`](https://github.com/memcoai/memco/blob/main/python/examples/).

**See more → [docs.memco.ai](https://docs.memco.ai)**

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

Constructing a `Memco` probes the service's health endpoint, so a bad host,
port or TLS setting fails immediately rather than on your first call.
`AsyncMemco` cannot do this in `__init__` — it probes on `connect()`, which
`async with` calls for you.

That probe does not carry your credential, so it cannot check it. Pass
`verify_credentials=True` if you want that too, bearing in mind it makes an
additional request every time a client is built.

```python
Memco(token="...", host="localhost:50051", tls=False, check_health=False)
```

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
