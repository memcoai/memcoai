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
  <a href="https://github.com/memcoai/memco/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/memcoai/memco/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <img alt="Python versions" src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3775a9">
  <img alt="Coverage" src="https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen">
  <a href="LICENSE"><img alt="Licence" src="https://img.shields.io/badge/licence-MIT-blue"></a>
</p>

---

## What is Memco Shared Memory?

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share.

Agents normally start every conversation from nothing. They rediscover the same
constraints, repeat the same mistakes, and lose whatever they worked out the
moment the session ends. Shared Memory is where that knowledge goes instead: an
agent searches it before starting work, and writes back what it learned when it
finishes. What one agent establishes, every teammate's agent can find.

It stores the things that are expensive to rediscover — how an internal system
actually behaves, why a decision was made, a bug's root cause, a convention that
is not written down anywhere else. Memory is partitioned into **domains**, each
with its own subject matter and tag vocabulary, and every result carries a
reliability signal built from what readers reported back about it.

**You need an account and an API key to use these SDKs.** Create one at
[memco.ai](https://memco.ai).

## SDKs

| Language | Package | Install |
|---|---|---|
| Python | [`memco`](python/) | `pip install memco` |

The generated gRPC clients for [Go](go/) and [Node](nodejs/) are also published
here and can be used directly against the API.

### Installing before the packages are published

Until `memco` is on PyPI, install it from the **`dev`** branch of this
repository:

```bash
uv add "memco @ git+ssh://git@github.com/memcoai/memco.git@dev#subdirectory=python"
```

```bash
pip install "memco @ git+ssh://git@github.com/memcoai/memco.git@dev#subdirectory=python"
```

Two parts are load-bearing:

- **`@dev`** — the ref is required. `main` does not yet carry the SDK, so an
  install without a ref resolves to the default branch and fails with
  *"has no subdirectory `python`"*.
- **`#subdirectory=python`** — the package root is not the repository root.

`uv add --branch dev "memco @ git+ssh://git@github.com/memcoai/memco.git#subdirectory=python"`
is equivalent and records `branch = "dev"` rather than `rev = "dev"`, which is a
more accurate description of what it tracks.

Use `git+https://` instead if you authenticate with a token.

A branch is resolved once and then cached, so pick up new commits explicitly:

```bash
uv sync --upgrade-package memco
pip install --force-reinstall --no-deps "memco @ git+ssh://git@github.com/memcoai/memco.git@dev#subdirectory=python"
```

To work on the SDK itself, install your clone in editable mode instead — changes
then need no reinstall at all:

```bash
uv pip install -e path/to/memco/python
```

Remove these requirements once the package is published: a `memco @ git+...`
source keeps winning over the released version.

## Quick start

```python
from memco import Memco
with Memco() as client:                       # reads MEMCO_API_TOKEN
    session = client.memory.start_session("coding")

    result = client.memory.search(
        "how should a client authenticate against the memory API",
        session_id=session.session_id,
    )
    for memory in result.memories:
        for insight in memory.insights:
            print(insight.title, insight.updated)
```

See [`python/README.md`](python/README.md) for the full guide and
[`python/examples/`](python/examples/) for runnable programs.

**See more → [docs.memco.ai](https://docs.memco.ai)**

## Repository layout

```
proto/            the service contract, for reference
python/           the Python SDK
  memco/            hand-written SDK
  memco/memory/     generated gRPC client
go/, nodejs/      generated gRPC clients
```

Generated code is replaced wholesale each time it is regenerated, so please do
not edit it by hand — see [CONTRIBUTING.md](CONTRIBUTING.md). For Python that is
`python/memco/memory/`; for Go and Node it is `<language>/client/`.

## Contributing

Bug reports and pull requests are welcome, and you do not need access to Memco's
servers to work on this — the test suites run in-process, so everything passes
offline. [CONTRIBUTING.md](CONTRIBUTING.md) covers which files are generated and
must not be edited, what a change needs before it can be merged, and the
per-language setup (see the [Python](CONTRIBUTING.md#python) section).

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
