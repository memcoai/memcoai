# Contributing

Thanks for taking the time to contribute. This repository holds the official
Memco SDKs; issues and pull requests are welcome from anyone.

You do **not** need access to Memco's servers to contribute. Every test suite
runs against an in-process server, so `make check` passes offline.

Everything down to [Making a change](#making-a-change) applies whichever SDK you
are touching. After that the document splits, and each language repeats the same
seven headings — Prerequisites, Setting up, Running the checks, Layout, Tests,
Documentation, Style — so the two can be read side by side:

- [Python](#python) — published to PyPI as `memco`
- [Node.js](#nodejs) — published to npm as `@memcoai/memco`

## Reporting a bug

[Open an issue](https://github.com/memcoai/memco/issues) and include:

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
proto/                    the service contract
python/memco/memory/      generated Python client, and the tool manifest
go/client/                generated Go client, and the tool manifest
nodejs/client/            generated Node client, and the tool manifest
```

Everything else is hand-written, including the files that sit alongside a
generated tree. Each language's **Layout** section below says which is which.

If you believe the generated code or the contract itself is wrong, open an issue
describing the problem rather than editing the output. `make provenance` checks
that the contract's checksum matches what each descriptor records, that they
agree on one server commit, that the declared dependency floors match what the
generated modules assert at import, and that every language ships the same
non-empty tool manifest. It does not verify the generated code itself byte for
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
| Python | docstrings in `memco/operations.py` and `memco/types.py`, which `memco.agent` reads back at run time |
| Node.js | `nodejs/src/gen/toolCopy.ts` — TypeScript keeps no doc comments at run time, so the copy is compiled in |
| Go | not compiled in; `go/client/tools` publishes the manifest as it ships, markers and all |

Regenerating is a maintainer step, but it concerns you in one way:
`make tool-docs-check` and a pre-commit hook fail if any of those has drifted
from the manifest, so hand-editing the generated copy will not pass.

Three parameters are deliberately left alone, listed as `SDK_SHAPED` in that
script: the manifest describes `tags`, `feedback` and `source` as the MCP server
accepts them — XML strings and bare literals — while the SDKs take their own
`Tag`, `FeedbackRating` and `DataSource` types and hand a model an object schema
built from those. Writing the wire copy onto them would describe an encoding the
schema rejects.

## Development setup

You need `make`, plus the toolchain for whichever SDK you are working on:

| SDK | Prerequisite | Notes |
|---|---|---|
| Python | [uv](https://docs.astral.sh/uv/) | Fetches the interpreters itself; `python/.python-version` pins the supported floor |
| Node.js | a Node runtime | Nothing fetches it for you; `nodejs/.nvmrc` pins **24**, the version to develop against |

You only need both if you are changing both. The root `make` targets fan out to
every language present and skip nothing, so a partial toolchain will fail on the
language you have not installed.

```bash
git clone https://github.com/memcoai/memco.git
cd memco
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
separately, from a tag on `main`, and a version bump in a change would collide
with that.

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
[`memco`](https://pypi.org/project/memco/).

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
make -C python docs         # build the reference; warnings are errors
make -C python docs-serve   # build it and serve it on :8000
make -C python build        # build the sdist and the wheel
```

`make check` from the root runs the same set plus the provenance check.

## Layout

```
python/
  memco/
    __init__.py       the public API: Memco, AsyncMemco, provenance
    types.py          the result types every operation returns
    errors.py         the exception hierarchy
    operations.py     the namespaces reached as client.memory
    _sync.py _aio.py  the clients
    _*.py             internals: config, auth, channel, conversion, validation
    memory/           GENERATED — do not edit
  tests/              the test suite
  examples/           runnable programs, checked by the suite
  docs/               Sphinx sources for the reference
```

Anything under `memco/memory/` comes from the service contract. Everything else
in `memco/` is hand-written, `__init__.py` included.

## Tests

The suite runs against a **real in-process gRPC server**
([`python/tests/fake_server.py`](python/tests/fake_server.py)) on a loopback
socket. Nothing about gRPC is mocked, so interceptors, metadata, status codes and
channel teardown are all exercised for real. It needs no network access and no
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

### The system test

[`python/systemtest/`](python/systemtest/) is the one suite that is **not**
hermetic. It runs the whole command lifecycle — create, search, rate, fetch,
enrich, revert, confirm the memory is gone — against the real service, once per
domain the credential can reach, and CI runs it on every pull request.

**One step writes permanently.** `import_memories` mints no operation id, so the
service offers no way to revert a batch. That step therefore imports a **fixed**
payload carrying no run marker, into **one** domain rather than every domain: an
import is written under an identity derived from its own content, so it lands
once and every run after that is reported `DUPLICATE`. Treat that payload as
knowledge you are publishing, because it stays, and nothing in this repository
can correct it once it has. Everything else the suite writes it reverts before
it finishes.

It is not collected by `make test`: `testpaths` names `tests/` only, so `make -C
python system-test` is the only way to reach it. Without `MEMCO_API_TOKEN` it
reports itself skipped rather than failing, so you can run it, and `make check`,
with no server access at all. In CI a missing credential is a skip only on a
pull request; anywhere else — a push to main, or a release — it fails, because a
publish that silently never reached the service is worse than a red build.

Two more things to know before changing it. A write is accepted
**asynchronously**, so the create returns an operation id rather than a memory
and the memory is not addressable until ingestion has run — every existence
assertion is a poll, not a single call. And the content it writes is real prose
about this SDK on purpose: a write is evaluated on the way in and can be
rejected downstream, and filler would be dropped and look exactly like a broken
search path. Keep it distinct from what the other SDK's suite writes, too — the
two run concurrently against the same organisation, and near-identical content
is deduplicated.

## Documentation

Every public symbol needs a Google-style docstring with `Args`, `Returns`,
`Raises`, and an example where one helps. The reference is generated from those
by Sphinx, and the build runs with `nitpicky` and `-W`, so an unresolved
cross-reference fails CI rather than rotting on the published site.

The operations are the exception: the leading prose and the `Args` descriptions
of everything the tool manifest names are generated, so edit `tools.json`
upstream rather than the docstring. `Returns`, `Raises`, `Example` and any
parameter the manifest does not name — `timeout`, for one — are yours.

Programs in `examples/` are imported by the test suite, so an example naming a
symbol that no longer exists fails the build.

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
[`@memcoai/memco`](https://www.npmjs.com/package/@memcoai/memco).

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
interceptors, metadata, status codes and channel teardown are all exercised for
real. It needs no network access and no API key.

It runs on `node:test` and `node:assert` with **zero test dependencies** — no
runner, no assertion library, no mocking framework. Keep it that way.

The sources are compiled and the tests then run as JavaScript, rather than under
Node's type stripping. That is not a preference: the generated client contains
`export enum DataSource`, which is not erasable syntax, so `node --test` over the
`.ts` sources dies before a single test runs. `--enable-source-maps` puts the
frames back on `src/*.ts`.

Conventions match the Python suite: name a test after the behaviour it pins, not
the function it calls, and assert what is observable.

### The system test

[`nodejs/systemtest/`](nodejs/systemtest/) is the one suite that is **not**
hermetic. It runs the whole command lifecycle — create, search, rate, fetch,
enrich, revert, confirm the memory is gone — against the real service, once per
domain the credential can reach, and CI runs it on every pull request.

**One step writes permanently.** `importMemories` mints no operation id, so the
service offers no way to revert a batch. That step therefore imports a **fixed**
payload carrying no run marker, into **one** domain rather than every domain: an
import is written under an identity derived from its own content, so it lands
once and every run after that is reported `DUPLICATE`. Treat that payload as
knowledge you are publishing, because it stays, and nothing in this repository
can correct it once it has. Everything else the suite writes it reverts before
it finishes.

`npm test` runs the glob `build/js/tests/**`, so it never matches this suite;
`make -C nodejs system-test` is the only way to reach it. Without
`MEMCO_API_TOKEN` it reports itself skipped rather than failing, so you can run
it, and `make check`, with no server access at all. In CI a missing credential
is a skip only on a pull request; anywhere else — a push to main, or a release —
it fails, because a publish that silently never reached the service is worse
than a red build.

Two more things to know before changing it. A write is accepted
**asynchronously**, so the create returns an operation id rather than a memory
and the memory is not addressable until ingestion has run — every existence
assertion is a poll, not a single call. And the content it writes is real prose
about this SDK on purpose: a write is evaluated on the way in and can be
rejected downstream, and filler would be dropped and look exactly like a broken
search path. Keep it distinct from what the other SDK's suite writes, too — the
two run concurrently against the same organisation, and near-identical content
is deduplicated.

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

## Licence

This repository is licensed under the [MIT licence](LICENSE), © Memco Labs, Inc.
By contributing you agree that your contributions will be released under the
same licence.

`python/LICENSE` and `nodejs/LICENSE` are copies of that file, because a
published package has to carry its own licence text. Each language's suite fails
if its copy has drifted from the root, so update all three together.
