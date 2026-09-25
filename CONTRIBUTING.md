# Contributing

Thanks for taking the time to contribute. This repository holds the official
Memco SDKs; issues and pull requests are welcome from anyone.

You do **not** need access to Memco's servers to contribute. Every test suite
runs against an in-process server, so `make check` passes offline.

Everything down to [Making a change](#making-a-change) applies whichever SDK you
are touching. After that the document splits, and each language repeats the same
seven headings — Prerequisites, Setting up, Running the checks, Layout, Tests,
Documentation, Style — so the three can be read side by side:

- [Python](#python) — published to PyPI as `memcoai`
- [Node.js](#nodejs) — published to npm as `@memco/memcoai`
- [Go](#go) — published through the Go module proxy as `github.com/memcoai/memcoai/go`

## Reporting a bug

[Open an issue](https://github.com/memcoai/memcoai/issues) and include:

- what you did, what you expected, and what happened instead
- the SDK version and your language runtime version
- the full traceback, if there is one
- a minimal snippet that reproduces it, if you can

Please redact your API key and any memory content you would rather not publish —
an issue is world-readable.

If the bug is in the **service** rather than the SDK — wrong search results,
unexpected permissions, quota questions — that is not something this repository
can fix. Contact support through [memco.ai](https://memco.ai) instead.

### Security

Please do not open a public issue for a security vulnerability. Report it
privately through [memco.ai](https://memco.ai).

## Generated code — do not edit

Everything under these paths is produced from the service contract and is
**emptied and rewritten** whenever it is regenerated. Any edit you make there
will be silently discarded:

```
proto/                    the service contracts: memory, admin and auth
python/memcoai/memory/    generated Python client, and the tool manifest
python/memcoai/admin/     generated Python client for administration
python/memcoai/auth/      generated Python client for the token exchange
go/internal/client/       generated Go client, and the tool manifest
nodejs/client/            generated Node client, and the tool manifest
```

Everything else is hand-written, including the files that sit alongside a
generated tree. Each language's **Layout** section below says which is which.

If you believe the generated code or the contract itself is wrong, open an issue
describing the problem rather than editing the output. `make provenance` checks
that the contract's checksum matches what each descriptor records, that they
agree on one server commit, that the declared dependency floors match what the
generated modules assert at import, that `go/go.mod` requires at least what the
Go descriptor declares, and that every language ships the same non-empty tool
manifest. It does not verify the generated code itself byte for
byte. If it fails on a clean checkout, that is worth an issue on
its own.

### The tool manifest

`tools.json` ships in each of those trees, byte-identical, carrying the
agent-facing description of every operation — the same copy the hosted MCP server
publishes. It is generated too, so a wording fix belongs upstream, not here.

No SDK reads it at run time. `scripts/sync_tool_docs.py` compiles the copy into
each language's own source instead, so the description a model reads is
reviewable in a diff rather than loaded from a data file:

| SDK | Where the copy lands |
|---|---|
| Python | docstrings in `memcoai/operations.py` and `memcoai/types.py`, which `memcoai.agent` reads back at run time |
| Node.js | `nodejs/src/gen/toolCopy.ts` — TypeScript keeps no doc comments at run time, so the copy is compiled in |
| Go | `go/internal/memory/toolcopy_gen.go` — Go keeps no doc comments at run time either, so the copy is compiled in |

Regenerating is a maintainer step, but it concerns you in one way:
`make tool-docs-check` and a pre-commit hook fail if any of those has drifted
from the manifest, so hand-editing the generated copy will not pass.

The manifest also describes the fields of each entry a `tags` or `feedback`
list takes — `tags[].version`, `feedback[].idx` — under every tool that takes
one. The script writes each field once, on its type: into the `Tag` and
`FeedbackRating` docstrings for Python, and into `ENTRY_COPY` and `EntryCopies`
for Node.js and Go. It refuses a field described two ways, and a type whose
fields are described only in part.

## Development setup

You need `make`, plus the toolchain for whichever SDK you are working on:

| SDK | Prerequisite | Notes |
|---|---|---|
| Python | [uv](https://docs.astral.sh/uv/) | Fetches the interpreters itself; `python/.python-version` pins the supported floor |
| Node.js | a Node runtime | Nothing fetches it for you; `nodejs/.nvmrc` pins **24**, the version to develop against |
| Go | a Go toolchain ≥ 1.21, and a C compiler | Fetches the toolchain `go/go.mod` names itself; `-race` needs cgo |

You only need more than one if you are changing more than one. The root `make` targets fan out to
every language present and skip nothing, so a partial toolchain will fail on the
language you have not installed.

```bash
git clone https://github.com/memcoai/memcoai.git
cd memcoai
make install
make check
```

`make check` runs lint, strict type checking, the test suites, the provenance
and tool-copy checks, and a documentation build — the parts of CI that run on
every push. CI
additionally runs the suite across every supported runtime, builds and installs
the distributables, and checks the dependency bounds; `make test-all` and
`make build` cover most of that locally.

The repository root fans out to every language directory that carries a
`Makefile`, so `make test` covers everything present. `make help` lists the
targets. For anything language-specific, address one directly:

```bash
make -C python help
make -C nodejs help
make -C go help
```

## Making a change

1. **Open an issue first** for anything beyond a small fix, so the approach can
   be agreed before you spend time on it.
2. **Write a test that fails.** For a bug, it should reproduce the bug; for a
   feature, it should specify the behaviour. Run it and confirm it fails for the
   reason you expect before writing any implementation.
3. **Keep the change surgical.** Touch only what the change requires. Match the
   surrounding style rather than reformatting adjacent code.
4. **Run `make check`.** Every part must pass.
5. **Open a pull request** describing what changed and why, and linking the
   issue.

Do not bump a version or edit a changelog in a pull request. Releases are cut
separately, from a tag on `main` — `python-v<version>`, `nodejs-v<version>` or
`go-v<version>`, each starting that SDK's release pipeline — and a version bump
in a change would collide with that.

### What review will ask for

- **A test that fails before the fix.** For a bug, it must reproduce the bug.
- **Errors must be typed.** No raw transport error may reach a caller.
- **No credential may reach a log, a traceback or a `repr`.** This has been a
  real defect here: a dataclass `repr` put an API key into any crash report that
  captured local variables.
- **Public API changes need a strong reason**, and a note in the pull request
  saying what breaks. These packages are used by people who cannot easily change
  their code.
- **Suppressions need a reason.** One is fine where the alternative would be a
  fiction; a bare suppression will be questioned. Each language's **Style**
  section below gives the spelling its tooling expects.

Optionally, install the hooks so the checks run before each commit:

```bash
pre-commit install
```

---

# Python

The Python SDK is in [`python/`](python/) and is published to PyPI as
[`memcoai`](https://pypi.org/project/memcoai/). A `python-v<version>` tag
releases it.

## Prerequisites

Only [uv](https://docs.astral.sh/uv/). It reads `python/.python-version` and
fetches the interpreter itself, so you do not need a matching Python installed.

That file pins **3.10**, the oldest version the package supports
(`requires-python = ">=3.10"`). Developing against the floor is deliberate: it
catches portability bugs locally rather than in the CI matrix.
`date.fromisoformat` accepting more formats on 3.11+ is one that reached review
this way.

## Setting up

```bash
make -C python install      # or `make install` from the repository root
```

That creates `python/.venv` with the runtime, test and documentation
dependencies. To use it directly rather than through `uv run`:

```bash
source python/.venv/bin/activate
```

If your editor needs an interpreter, point it at `python/.venv/bin/python`. Do
not let it create one at the repository root: the root is not a Python project,
so an interpreter resolved there will be the wrong version and will report
spurious errors on valid 3.10+ syntax — `str | None` annotations and
`@dataclass(slots=True)` are the two that show up first.

## Running the checks

```bash
make -C python lint         # ruff: lint and format check
make -C python format       # apply formatting and safe fixes
make -C python typecheck    # mypy, strict, over the SDK and the tests
make -C python test         # the suite, on the floor version
make -C python test-all     # the suite on 3.10, 3.11, 3.12 and 3.13
make -C python system-test  # the live suite; skipped without a credential
make -C python coverage     # the suite with coverage; fails below the floor
make -C python run-examples # every example, against the real service
make -C python docs         # build the reference; warnings are errors
make -C python docs-serve   # build it and serve it on :8000
make -C python build        # build the sdist and the wheel
make -C python clean        # remove build and cache artefacts
```

`make check` from the root runs the same set plus the provenance check.

## Layout

```
python/
  memcoai/
    __init__.py       the public API: Memco, AsyncMemco, provenance
    types.py          the result types every operation returns
    errors.py         the exception hierarchy
    operations.py     the namespaces reached as client.memory
    administration.py the namespaces reached as client.networks and client.users
    _sync.py _aio.py  the clients
    _*.py             internals: config, auth, channel, conversion, validation
    memory/           GENERATED — do not edit
    admin/ auth/      GENERATED — do not edit
  tests/              the test suite
  examples/           runnable programs, checked by the suite
  docs/               Sphinx sources for the reference
```

Anything under `memcoai/memory/`, `memcoai/admin/` or `memcoai/auth/` comes from
the service contracts. Everything else in `memcoai/` is hand-written,
`__init__.py` included.

## Tests

The suite runs against a **real in-process gRPC server**
([`python/tests/fake_server.py`](python/tests/fake_server.py)) on a loopback
socket. Nothing about gRPC is mocked, so metadata, status codes and channel
teardown are all exercised for real. It needs no network access and no
API key.

Conventions the suite already follows:

- **Name the test after the behaviour it pins**, not the function it calls:
  `test_the_credential_is_withheld_from_the_health_probe`, not `test_auth`.
- **Assert what is observable.** Several tests assert the fake server saw
  **zero** calls, which is how "this is rejected before any request is sent" is
  proven.
- **Regressions go in
  [`tests/test_regressions.py`](python/tests/test_regressions.py)** with a
  comment naming what went wrong.
- **Async tests need no decorator** — `asyncio_mode = "auto"` is set.

[`tests/test_examples.py`](python/tests/test_examples.py) imports every program
in `examples/`, so an example naming a symbol that no longer exists fails the
suite.

### The system test

[`python/systemtest/`](python/systemtest/) is the one suite that is **not**
hermetic. It runs the whole command lifecycle — create, search, rate, fetch,
enrich, revert both writes, confirm the memory is gone — against the real
service, once per domain the credential can reach, and checks that a creator
credential is offered every tool. As an API client, it also administers a
customer network and its external users, and opens memory sessions acting as
those users, two of them at once. CI runs it on every pull request.

It leaves nothing behind. It reverts every write it makes and asserts on the
outcome, and the `written` fixture reverts them again on the way out, so a run
that fails part-way still cleans up. The networks and users it creates are
deleted the same way, by the `customer_networks` and `external_users` fixtures,
and a network's deletion takes the memories its users wrote with it, once they
are ingested. It imports
nothing: an import mints no operation id, so nothing could undo it.

It is not collected by `make test`: `testpaths` names `tests/` only, so
`make -C python system-test` is the only way to reach it. The memory tests need
`MEMCO_API_TOKEN`; the administration and impersonation tests need
`MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET`, for an API client holding the admin
grant and the `network-management` and `user-management` scopes. A test without
its credential reports itself skipped rather than failing, so you can run it,
and `make check`, with no server access at all. In CI a missing credential
is a skip only on a pull request; anywhere else — a push to main, or a release —
it fails, because a publish that silently never reached the service is worse
than a red build.

Against a local development server, which serves plaintext,
`MEMCO_API_TLS=false` turns TLS off:

```bash
MEMCO_API_TOKEN= MEMCO_API_TLS=false MEMCO_API_HOST=localhost:50052 MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=... make -C python system-test
```

`MEMCO_API_TOKEN` is blanked because a token exported for the live service is
rejected by the local server, and a rejected token fails collection rather than
skipping. Set it to a token the local server issued to run the memory tests too.

Two more things to know before changing it. A write is accepted
**asynchronously**, so the create returns an operation id rather than a memory
and the memory is not addressable until ingestion has run — every existence
assertion is a poll, not a single call. And the content it writes is real prose
about this SDK on purpose: a write is evaluated on the way in and can be
rejected downstream, and filler would be dropped and look exactly like a broken
search path. Keep it distinct from what the other SDKs' suites write, too — the
three run concurrently against the same organisation, and near-identical
content is deduplicated.

## Documentation

Every public symbol needs a Google-style docstring with `Args`, `Returns`,
`Raises`, and an example where one helps. The reference is generated from those
by Sphinx, and the build runs with `nitpicky` and `-W`, so an unresolved
cross-reference fails CI rather than rotting on the published site.

The operations are the exception: the leading prose and the `Args` descriptions
of everything the tool manifest names are generated, so edit `tools.json`
upstream rather than the docstring. `Returns`, `Raises`, `Example` and any
parameter the manifest does not name — `timeout`, for one — are yours.

`make docs` builds the reference twice: once as HTML for a person, and once as
markdown for [`scripts/build_llms_txt.py`](scripts/build_llms_txt.py), which
publishes each page's markdown beside its HTML under the same name —
`clients.md` next to `clients.html` — and writes two files an agent starts
from: `llms.txt`, an index naming every page, and `llms-full.txt`, all of them
as one document. So `docs.memco.ai/sdk/python/latest/clients.html` has its text
at `.../clients.md`, and either entry point reaches it.

Nothing there is hand-maintained: the page order is the `toctree`'s, so a page
added to it is picked up. Two things fail that step — a page nothing indexes,
and a link that reaches nothing — and the error names them.

Building it is as far as a pull request goes — `make check` does that already.
Publishing is a release step, done from the release pipeline.

## Style

Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/) and
type checking by [mypy](https://mypy-lang.org/) in strict mode, both configured
in [`python/pyproject.toml`](python/pyproject.toml). There is no separate style
guide to read — if `make check` is green, the style is right.

`# type: ignore[code]` and `# noqa: RULE - why` are accepted where the
alternative would be a fiction: grpc ships no type information, so its callback
signatures genuinely are `Any`. Say why in the comment.

---

# Node.js

The Node.js SDK is in [`nodejs/`](nodejs/) and is published to npm as
[`@memco/memcoai`](https://www.npmjs.com/package/@memco/memcoai). A
`nodejs-v<version>` tag releases it.

## Prerequisites

Node, and `make`. There is no uv here to fetch a runtime for you, so install one
yourself; [nvm](https://github.com/nvm-sh/nvm) is where
`make -C nodejs test-all` looks for the other supported versions.

`nodejs/.nvmrc` pins **24**, the version to develop against. `engines.node`
declares the floor, **>= 22**, and CI sweeps 22, 24 and 26 — every line still
supported upstream, now that 18 and 20 have reached end of life (April 2025 and
April 2026).

Developing against the pin rather than the floor is the opposite of the Python
choice, and deliberate: a newer Node line takes nothing away, so there is no
portability trap to catch locally the way `date.fromisoformat` was. The matrix is
what proves the floor.

## Setting up

```bash
make -C nodejs install      # or `make install` from the repository root
```

That runs `npm ci`, which installs `package-lock.json` exactly. Do not reach for
`npm install` to set up: it is free to rewrite the lockfile, and a lockfile
change nobody asked for will turn up in your diff.

`nodejs/.npmrc` is part of the setup rather than decoration, and one line in it
will affect anything you write: `ignore-scripts=true` is the standing
supply-chain guard, and it means **npm never fires a lifecycle script** —
no `prepare`, no `prebuild`, no `posttest`. Every multi-step operation in
`package.json` chains with `&&` for that reason. A step written as a lifecycle
hook is skipped in silence, behind a green build.

## Running the checks

```bash
make -C nodejs lint         # prettier --check
make -C nodejs format       # apply formatting
make -C nodejs typecheck    # tsc --strict over the SDK, the client and the tests
make -C nodejs test         # the suite, on the version .nvmrc pins
make -C nodejs test-all     # the suite on every supported runtime installed
make -C nodejs system-test  # the live suite; skipped without a credential
make -C nodejs coverage     # the suite with coverage; fails below the floor
make -C nodejs run-examples # every example, against the real service
make -C nodejs docs         # build the reference; warnings are errors
make -C nodejs docs-serve   # build it and serve it on :8000
make -C nodejs build        # build both distributables and the npm tarball
make -C nodejs clean        # remove build and cache artefacts
```

`make check` from the root runs the same set plus the provenance check.

## Layout

```
nodejs/
  src/
    index.ts          the public API
    types.ts          the result types every operation returns
    errors.ts         the exception hierarchy
    administration.ts the namespaces reached as client.networks and client.users
    internal/         internals: wire conversion, resources, provenance
    gen/              GENERATED — the service's tool copy
  client/             GENERATED — do not edit
  tests/              the test suite
  docs/               TypeDoc's output; nothing in it is hand-written
```

Anything under `client/` comes from the service contract, and it is
export-owned in a way that reaches the tooling: `nodejs/.prettierignore` and
`.pre-commit-config.yaml` both exclude it, so a formatting pass cannot produce a
four-thousand-line diff that the next export throws away. `src/gen/` is the Node
counterpart of the generated Python docstrings — `scripts/sync_tool_docs.py`
writes the manifest's copy into `src/gen/toolCopy.ts`, and the same check fails
if you hand-edit it. Everything else in `src/` is hand-written.

The package publishes **both** module formats — `dist/esm/` and `dist/cjs/`,
built from one set of sources by `tsconfig.esm.json` and `tsconfig.cjs.json` and
selected by the `exports` map in `package.json`. A wrong condition, a wrong file
extension or a missing `{"type":"commonjs"}` marker breaks exactly one format
while the other keeps working, which is why CI installs the packed tarball and
loads it both ways rather than trusting the build.

## Tests

The suite runs against a **real in-process gRPC server**
([`nodejs/tests/fakeServer.ts`](nodejs/tests/fakeServer.ts)) on a loopback
socket, exactly as the Python suite does. Nothing about gRPC is mocked, so
metadata, status codes and channel teardown are all exercised for real. It needs
no network access and no API key.

It runs on `node:test` and `node:assert` with **zero test dependencies** — no
runner, no assertion library, no mocking framework. Keep it that way.

The sources are compiled and the tests then run as JavaScript, rather than under
Node's type stripping. That is not a preference: the generated client contains
`export enum DataSource`, which is not erasable syntax, so `node --test` over the
`.ts` sources dies before a single test runs. `--enable-source-maps` puts the
frames back on `src/*.ts`.

Conventions match the Python suite: name a test after the behaviour it pins, not
the function it calls, and assert what is observable.

[`tests/examples.test.ts`](nodejs/tests/examples.test.ts) imports every program
in `examples/`, and asserts that importing one runs nothing, so an example that
stopped resolving fails the suite.

### The system test

[`nodejs/systemtest/`](nodejs/systemtest/) is the one suite that is **not**
hermetic. It runs the whole command lifecycle — create, search, rate, fetch,
enrich, revert both writes, confirm the memory is gone — against the real
service, once per domain the credential can reach, and checks that a creator
credential is offered every tool. As an API client, it also administers a
customer network and its external users, and opens memory sessions acting as
those users, two of them at once. CI runs it on every pull request.

It leaves nothing behind. It reverts every write it makes and asserts on the
outcome, and a `finally` reverts them again, so a run that fails part-way still
cleans up. The networks and users it creates, all named `nodesys-…`, are deleted
the same way, by `Created` in `systemtest/support.ts`, and a network's deletion
takes the memories its users wrote with it, once they are ingested. It imports
nothing: an import mints no operation id, so nothing could undo it.

`npm test` runs the glob `build/js/tests/**`, so it never matches this suite;
`make -C nodejs system-test` is the only way to reach it. The memory tests need
`MEMCO_API_TOKEN`; the administration and impersonation tests need
`MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET`, for an API client holding the admin
grant and the `network-management` and `user-management` scopes. A test without
its credential reports itself skipped rather than failing, so you can run it,
and `make check`, with no server access at all. In CI a missing credential
is a skip only on a pull request; anywhere else — a push to main, or a release —
it fails, because a publish that silently never reached the service is worse
than a red build.

Against a local development server, which serves plaintext,
`MEMCO_API_TLS=false` turns TLS off:

```bash
MEMCO_API_TOKEN= MEMCO_API_TLS=false MEMCO_API_HOST=localhost:50052 MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=... make -C nodejs system-test
```

`MEMCO_API_TOKEN` is blanked because a token exported for the live service is
rejected by the local server, and a rejected token fails the suite rather than
skipping. Set it to a token the local server issued to run the memory tests too.

Two more things to know before changing it. A write is accepted
**asynchronously**, so the create returns an operation id rather than a memory
and the memory is not addressable until ingestion has run — every existence
assertion is a poll, not a single call. And the content it writes is real prose
about this SDK on purpose: a write is evaluated on the way in and can be
rejected downstream, and filler would be dropped and look exactly like a broken
search path. Keep it distinct from what the other SDKs' suites write, too — the
three run concurrently against the same organisation, and near-identical
content is deduplicated.

## Documentation

Every public symbol needs a TSDoc comment. The reference is generated from those
by [TypeDoc](https://typedoc.org/), configured in
[`nodejs/typedoc.json`](nodejs/typedoc.json), and the build treats warnings as
errors: `notDocumented` fails on an undocumented public symbol, `invalidLink` on
an unresolved `{@link}`, and `notExported` when a public signature names a type
that is not itself public — which is what stops a generated type leaking out of
`src/index.ts`. That is Sphinx's `-W` plus `nitpicky`, in TypeDoc's vocabulary.

`make docs` then runs TypeDoc a second time over the same entry point, emitting
markdown for [`scripts/build_llms_txt.py`](scripts/build_llms_txt.py) — the same
assembler the Python SDK uses, and the same output: every page published as
markdown beside its HTML, plus `llms.txt` and `llms-full.txt` at the root. The
second pass is configured in
[`nodejs/typedoc.llms.json`](nodejs/typedoc.llms.json), which inherits
everything that decides *what* is documented, so the two outputs cannot describe
different APIs. A newly exported symbol is picked up from TypeDoc's own module
index.

One difference from Sphinx is worth knowing if you touch the assembler: TypeDoc
emits every heading at its true depth, while sphinx-markdown-builder pins a
rubric — what an `Example:` becomes — to a fixed level wherever it appears. The
assembler repairs that for Python only, and `Reference.rubrics` is where a
generator says whether it needs it. Applying the repair to TypeDoc's output
pushes a section under the sibling before it.

Building it is as far as a pull request goes — `make check` does that already.
Publishing is a release step, done from the release pipeline.

## Style

[Prettier](https://prettier.io/) only, configured in `nodejs/.prettierrc.json`.
There is deliberately no ESLint: `tsc` in strict mode already refuses what a
linter would be asked to catch here, and a second tool with opinions of its own
would mostly need silencing. There is no style guide to read — if `make check` is
green, the style is right.

`// @ts-expect-error - why` is accepted on the same terms `# type: ignore` is in
the Python SDK, and preferred over `@ts-ignore` because it fails once the
underlying problem is fixed. Say why in the comment.

---

# Go

The Go SDK is in [`go/`](go/) and is published through the Go module proxy as
the module `github.com/memcoai/memcoai/go`, whose one importable package is
[`github.com/memcoai/memcoai/go/memcoai`](https://pkg.go.dev/github.com/memcoai/memcoai/go/memcoai).
A `go-v<version>` tag releases it. The go command itself reads only a tag named
`go/v<version>` for a module in `go/`, and the module proxy keeps whatever that
tag first names for good, so the release pipeline creates it once every check
has passed. Never push a `go/v*` tag yourself.

## Prerequisites

A Go toolchain, `make`, and a C compiler: the hermetic suites run with the race
detector, which needs cgo. Any Go from 1.21 on will do. `go/go.mod` declares
**go 1.25.0**, the floor grpc-go v1.83.1 forces, and an older installed
toolchain fetches that one by itself. CI pins **1.26** for its single-version
jobs and sweeps **1.25** and **1.27**; `make -C go test-all` runs both modules'
suites on go1.25.13, go1.26.8 and go1.27.0, fetching each on first use.

golangci-lint v2.13.2 needs a 1.26 toolchain to build, so `make -C go lint`
runs it under `LINT_TOOLCHAIN` (default `go1.26.8`). Pass `LINT_TOOLCHAIN=local`
when the installed toolchain is already 1.26 or later, or
`GOLANGCI_LINT=golangci-lint` to use an installed binary of the same version.
The Makefile sets `GOWORK=off`, so a `go.work` of your own cannot change what is
built.

## Setting up

```bash
make -C go install          # or `make install` from the repository root
```

That downloads both modules' dependencies and the three pinned tools —
golangci-lint, doc2go and gomarkdoc. The Makefile runs each with
`go run tool@version`, so nothing is installed globally and each version is
written in one place. The first run needs network access; after that the module
cache serves them.

## Running the checks

```bash
make -C go lint             # golangci-lint: format check, lint and static analysis
make -C go format           # apply gofmt (simplify) and goimports
make -C go typecheck        # go vet over both modules, the live suite included
make -C go test             # both suites, with the race detector
make -C go test-all         # both suites on 1.25, 1.26 and 1.27
make -C go system-test      # the live suite; skipped without a credential
make -C go coverage         # the suites with coverage; fails below the floor
make -C go run-examples     # every example, against the real service
make -C go docs             # build the reference and its llms.txt pair
make -C go docs-serve       # build it and serve it on :8000
make -C go build            # check both modules are tidy, verify downloads, build
make -C go clean            # remove build and cache artefacts
```

`make check` from the root runs the same set plus the provenance check.

## Layout

```
go/
  memcoai/              the public package
  internal/
    client/             GENERATED — do not edit
    descriptor.go       embeds client/SDK_PROVENANCE.yaml
    config/             credential and endpoint resolution
    transport/          the channel: TLS, auth, retries, health
    fault/              the one internal error type
    logging/ warn/      the SDK's own logger, and once-only warnings
    provenance/         the descriptor's parser
    memory/             the memory service: checks, limits, agent tables
      toolcopy_gen.go   GENERATED — the service's tool copy
    testserver/         the in-process fake service the suites run against
  examples/             runnable programs; a module of its own
  systemtest/           the live suite (build tag systemtest)
  docs/                 doc2go's and gomarkdoc's output; nothing in it is hand-written
```

`memcoai/` exposes what the Python SDK exposes without a leading underscore —
the client, its operations and sessions, the data types, the errors and the
agent tools — and nothing else. Every helper lives under `internal/`, the Go
counterpart of Python's `_*.py` modules, where the go command itself stops a
consumer importing it. Internal packages never import `memcoai`: they work on
the generated wire messages and return `*fault.Error`. What stays unexported in
`memcoai` is what has to work on its public types: copying fields between wire
and public types, mapping a fault to its public error type, the call accounting
behind `Close`, and rendering and dispatching for the agent tools.

`config`, `transport`, `fault`, `logging`, `warn` and `provenance` know nothing
about memory. A second service joins with a package of its own beside
`memory/`, its generated client beside the memory one, and a field on `Client`.

The generated client is internal on purpose: a consumer gets the SDK's own
types, so a contract change reaches them as a reviewed change here rather than
as a different generated struct. CI builds a consumer that imports it and fails
if the go command allows that. `internal/descriptor.go` exists because
`go:embed` cannot reach into a parent directory; it sits above `client/`, so the
provenance descriptor ships as the export wrote it, with no copy to keep in
step.

`examples/` is a module of its own, with a `replace` line pointing at `..`, so
the SDK module never depends on the Anthropic SDK one example uses. One
`go/.golangci.yml` covers both modules, because golangci-lint looks for its
configuration in parent directories.

## Tests

The suites run against a **real in-process gRPC server**
([`go/internal/testserver`](go/internal/testserver/testserver.go)) on a loopback
port, exactly as the Python suite does. Nothing about gRPC is mocked, so
metadata, status codes and channel teardown are all exercised for real. They
need no network access and no API key, and always run with `-race`.

They use the standard library's `testing` package with **no test
dependencies** — no assertion library, no mocking framework, no goroutine-leak
checker. Keep it that way.

Unit tests sit beside the internal package they pin; behaviour tests go through
a real client in `memcoai/`. Conventions match the Python suite: name a test
after the behaviour it pins, not the function it calls, assert what is
observable, and put a regression in
[`memcoai/regressions_test.go`](go/memcoai/regressions_test.go) with a comment
naming what went wrong.

Each program in `examples/` does its work in a `run` function that its own
`main_test.go` drives against the fake service (and, for `anthropic_agent`, a
fake Messages API), and `examples/examples_test.go` checks that none does
anything before `main`, so an example that stopped working fails the suite.

### The system test

[`go/systemtest/`](go/systemtest/) is the one suite that is **not** hermetic. It
runs the whole command lifecycle — create, search, rate, fetch, enrich, revert
both writes, confirm the memory is gone — against the real service, once per
domain the credential can reach, and checks that a creator credential is
offered every tool. As an API client, it also administers a customer network and
its external users, and opens memory sessions acting as those users, two of them
at once; a local proxy captures each session's key so the test can prove the
service refuses it once the session is closed. CI runs it on every pull request.

It leaves nothing behind. It reverts every write it makes and asserts on the
outcome, and `t.Cleanup` reverts them again, so a run that fails part-way still
cleans up. The networks and users it creates, all named `gosys-…`, are deleted
the same way, by cleanups registered before each create, and a network's
deletion takes the memories its users wrote with it, once they are ingested. The
organisation may be shared with the other SDKs' suites running at the same time,
so it only ever touches, asserts on or deletes what its own run created. It
imports nothing: an import mints no operation id, so nothing could undo it. `go test -timeout` kills a test without running its cleanups, so
the suite stops polling two minutes before that deadline and fails in time to
clean up.

Its files carry `//go:build systemtest`, so `go test ./...` never compiles
them; `make -C go system-test` is the only way to reach it, and
`make -C go typecheck` vets it so it cannot rot unbuilt. The memory tests need
`MEMCO_API_TOKEN`; the administration and impersonation tests need
`MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET`, for an API client holding the admin
grant and the `network-management` and `user-management` scopes. A test without
its credential reports itself skipped rather than failing, so you can run it,
and `make check`, with no server access at all. In CI a missing credential
is a skip only on a pull request; anywhere else — a push to main, or a release —
it fails, because a publish that silently never reached the service is worse
than a red build.

Against a local development server, which serves plaintext,
`MEMCO_API_TLS=false` turns TLS off:

```bash
MEMCO_API_TOKEN= MEMCO_API_TLS=false MEMCO_API_HOST=localhost:50052 MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=... make -C go system-test
```

`MEMCO_API_TOKEN` is blanked because a token exported for the live service is
rejected by the local server. Set it to a token the local server issued to run
the memory tests too.

Two more things to know before changing it. A write is accepted
**asynchronously**, so the create returns an operation id rather than a memory
and the memory is not addressable until ingestion has run — every existence
assertion is a poll, pausing 1s, 2s, 4s, 8s and then every 15s, each bounded by
a context deadline. And the content it
writes is real prose about this SDK on purpose: a write is evaluated on the way
in and can be rejected downstream, and filler would be dropped and look exactly
like a broken search path. Keep it distinct from what the other SDKs' suites
write, too — the three run concurrently against the same organisation, and
near-identical content is deduplicated.

## Documentation

Only `memcoai/` is documented, and its doc comments are the reference. Every
exported identifier and exported struct field has one. Every exported function
and method that takes parameters describes each under `Parameters:` and says
what it returns; one that returns an error lists the typed errors under
`Errors:`. Every method of the client, the operations, the session, the
toolset and the tools has an `Example…` function in `memcoai/example_test.go`,
compiled on every test run, as do `NewClient`, `ReadProvenance`, `SetLevel`,
`Render`, `Briefing` and `Memory.Feedback`. Methods an interface defines — `Error`, `Unwrap`,
`String`, `Format`, `LogValue` — need only one line. Link other symbols with
`[Name]`. `memcoai/docs_test.go` checks the comments, the `Parameters:` and
`Errors:` sections and the examples, and staticcheck, which `go/.golangci.yml`
enables in full, checks each comment's form. Internal
packages are not documented: a short comment where the name is not enough, and
none where it is.

`make -C go docs` builds the HTML with [doc2go](https://abhinav.github.io/doc2go/)
and a markdown twin with [gomarkdoc](https://github.com/princjef/gomarkdoc),
from the same package, then runs
[`scripts/build_llms_txt.py`](scripts/build_llms_txt.py) — the assembler the
other SDKs use — to publish every page as markdown beside its HTML, plus
`llms.txt` and `llms-full.txt`. Neither generator is given `internal/`, and a
test fails if an exported value is spelled through an internal package, which
would put that package's name in the reference.

Building it is as far as a pull request goes — `make check` does that already.
Publishing is a release step, done from the release pipeline.

## Style

[`go/.golangci.yml`](go/.golangci.yml) is the whole style guide.
`make -C go format` applies gofmt (with `-s`) and goimports, and
`make -C go lint` runs golangci-lint's standard linters, staticcheck with every
check on, errorlint, misspell, bidichk and nolintlint. The generated client is
excluded; `toolcopy_gen.go`, though generated, is held to the same checks as
everything else. If `make check` is green, the style is right.

A suppression is written `//nolint:<linter> // <reason>`; nolintlint rejects one
that does not name its linter or give a reason.

---

## Licence

This repository is licensed under the [MIT licence](LICENSE), © Memco Labs, Inc.
By contributing you agree that your contributions will be released under the
same licence.

`python/LICENSE`, `nodejs/LICENSE` and `go/LICENSE` are copies of that file,
because a published package has to carry its own licence text. Each language's
suite fails if its copy has drifted from the root, so update all four together.
