# memco

Python SDK for [Memco Shared Memory](https://memco.ai) — a persistent store your
team and its agents share.

It wraps the generated gRPC client with connection handling, credential
management, typed results and typed errors. Every method is available
synchronously and asynchronously, and the package is fully typed: `py.typed`
ships, so mypy and pyright see your calls.

## Install

```bash
pip install memco
```

Requires Python 3.10 or newer.

## Quick start

```python
from memco.client import Client

with Client() as client:  # reads MEMCO_API_TOKEN
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
from memco.client import AsyncClient

async with AsyncClient() as client:
    session = await client.memory.start_session("coding")
    result = await client.memory.search("...", session_id=session.session_id)
```

## Configuration

Arguments win over the environment, which wins over the defaults.

| Setting | Argument | Environment | Default |
|---|---|---|---|
| Credential | `token` | `MEMCO_API_TOKEN` | required |
| Endpoint | `host` | `MEMCO_API_HOST` | `grpc.spark.memco.ai:443` |
| TLS | `tls` | — | `True` |
| Deadline | `timeout` | — | 30 seconds |

The credential is either a static Memco API key or a WorkOS JWT; the service
accepts both in the same header. `MEMCO_API_KEY` is still honoured but warns.

Constructing a `Client` probes the service's health endpoint, so a bad host,
port or TLS setting fails immediately rather than on your first call.
`AsyncClient` cannot do this in `__init__` — it probes on `connect()`, which
`async with` calls for you. That probe is
unauthenticated and therefore cannot check the credential — pass
`verify_credentials=True` if you want that too, bearing in mind it spends one of
your per-minute rate-limit tokens each time a client is built.

```python
Client(token="...", host="localhost:50051", tls=False, check_health=False)
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
malformed call costs no round trip and no rate-limit budget.

**Not every "not found" is an error.** `revert_memory` reports a missing,
expired or moderated operation through `RevertResult.outcome` rather than
raising, because each describes caller-visible state rather than a failure.

```python
result = client.memory.revert_memory("create-8fj2k1")
if result.outcome is RevertOutcome.EXPIRED:
    print("outside the revert window")
```

## Provenance

The package records which version of the contract its generated client was
built from:

```python
from memco.client import provenance

provenance().server_commit  # '762721a87ab0d58313d1c4b23d8b344b740f90ab'
provenance().protos[0].path  # 'memco/memory/v1/memory.proto'
```

## Development

The generated client under `client/` is written by the server repository's SDK
export and must not be edited by hand. The SDK itself lives in `memco/client/`.
The two are separate PEP 420 namespace portions merged into one package at build
time.

Tasks run from the repository root, which fans out to every language, or from
here for the Python-specific ones:

```bash
make check                  # from the root: everything CI runs, every language
make -C python test-all     # the suite on 3.10 through 3.13
make -C python docs-serve   # build the reference and serve it locally
make -C python help         # every Python target
```

Tests run against a real in-process gRPC server; nothing is mocked. Examples
live in [`examples/`](examples/), and the reference is generated from the
docstrings into `docs/_build`.
