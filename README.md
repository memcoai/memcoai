<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img alt="Memco" src="assets/logo.svg" width="360">
  </picture>
</p>

<p align="center">
  Official SDKs for <b>Memco Shared Memory</b>.<br>
  <a href="https://memco.ai">memco.ai</a> &middot;
  <a href="https://docs.memco.ai">docs.memco.ai</a>
</p>

<p align="center">
  <a href="https://github.com/memcoai/memco/actions/workflows/ci_python.yaml"><img alt="CI (Python)" src="https://github.com/memcoai/memco/actions/workflows/ci_python.yaml/badge.svg?branch=main"></a>
  <img alt="Python versions" src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3775a9"><br>
  <a href="https://github.com/memcoai/memco/actions/workflows/ci_nodejs.yaml"><img alt="CI (Node.js)" src="https://github.com/memcoai/memco/actions/workflows/ci_nodejs.yaml/badge.svg?branch=main"></a>
  <img alt="Node versions" src="https://img.shields.io/badge/node-22%20%7C%2024%20%7C%2026-5fa04e"><br>
  <!-- One coverage badge, not one per language: it is a claim about the
       enforced floor, and both floors are 95% - `fail_under` in
       python/pyproject.toml, `--test-coverage-lines=95` in the `coverage`
       script in nodejs/package.json. Split it the day they diverge. -->
  <img alt="Coverage" src="https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen">
  <a href="LICENSE"><img alt="Licence" src="https://img.shields.io/badge/licence-MIT-blue"></a>
</p>

---

## What is Memco Shared Memory?

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share.

Shared Memory is where agents store and retrieve what they learn across
sessions. An agent searches it before starting work, and writes back what it
learned when it finishes. What one agent establishes, every teammate's agent can
find.

It stores the things that are expensive to rediscover — how an internal system
actually behaves, a bug's root cause, a convention that is not written down
anywhere else. Memory is partitioned into **domains**, each
with its own subject matter and tag vocabulary, and every result carries a
reliability signal built from what readers reported back about it.

**You need an account and an API key to use these SDKs.** Create one at
[memco.ai](https://memco.ai).

## SDKs

| Language | Package | Install |
|---|---|---|
| Python | [`memco`](python/) | `pip install memco` |
| Node.js | [`@memcoai/memco`](nodejs/) | `npm install @memcoai/memco` |

The generated gRPC client for [Go](go/) is also published here and can be used
directly against the API.

Each language tree also carries `tools.json`, a byte-identical manifest of the
agent-facing tool definitions — the same copy the hosted MCP server publishes, so
an agent built on an SDK is steered by the service's words rather than each SDK's
own. Go ships an accessor for it at `go/client/tools`; the Python SDK writes it
into the docstrings its `session.tools()` is built from; the Node.js SDK compiles
it into `nodejs/src/gen/toolCopy.ts`. Tool names in it are canonical snake_case
and a tool referenced in the prose is a `${tool:...}` marker, because the three
generators disagree on method casing — resolve the markers to the spelling your
client exposes rather than shipping them to a model.

## Quick start

### Python

```python
from memco import Memco
with Memco() as client:                       # reads MEMCO_API_TOKEN
    session = client.memory.start_session("coding")

    result = session.search("how should a client authenticate against the memory API")
    for memory in result.memories:
        for insight in memory.insights:
            print(insight.title, insight.updated)
```

See [`python/README.md`](python/README.md) for the full guide and
[`python/examples/`](python/examples/) for runnable programs.

### Node.js

```typescript
import { Memco } from '@memcoai/memco'

await using client = await new Memco().connect() // reads MEMCO_API_TOKEN
const session = await client.memory.withSession('coding')

const result = await session.search(
  'how should a client authenticate against the memory API'
)
for (const memory of result.memories) {
  for (const insight of memory.insights) {
    console.log(insight.title, insight.updated)
  }
}
```

See [`nodejs/README.md`](nodejs/README.md) for the full guide and
[`nodejs/examples/`](nodejs/examples/) for runnable programs.

**See more → [docs.memco.ai](https://docs.memco.ai)**

## Repository layout

```
proto/            the service contract, for reference
python/           the Python SDK
  memco/            hand-written SDK
  memco/memory/     generated gRPC client, and the tool manifest
nodejs/           the Node.js SDK
  src/              hand-written SDK
  client/           generated gRPC client, and the tool manifest
go/               generated gRPC client, and the tool manifest
scripts/          repository checks, standard library only
```

Generated code is replaced wholesale each time it is regenerated, so please do
not edit it by hand — see [CONTRIBUTING.md](CONTRIBUTING.md). For Python that is
`python/memco/memory/`; for Go and Node it is `<language>/client/`.

## Contributing

Bug reports and pull requests are welcome, and you do not need access to Memco's
servers to work on this — the test suites run in-process, so everything passes
offline. [CONTRIBUTING.md](CONTRIBUTING.md) covers which files are generated and
must not be edited, what a change needs before it can be merged, and the
per-language setup (the [Python](CONTRIBUTING.md#python) and
[Node.js](CONTRIBUTING.md#nodejs) sections).

```bash
make install   # set up every development environment
make check     # lint, typecheck, test, docs — everything CI runs
make help      # every target
```

## Support

- Documentation — [docs.memco.ai](https://docs.memco.ai)
- Questions about your account, plans or API keys — [memco.ai](https://memco.ai)
- Bugs and feature requests in these SDKs — [open an issue](https://github.com/memcoai/memco/issues)

## Licence

[MIT](LICENSE) &copy; Memco Labs, Inc.
