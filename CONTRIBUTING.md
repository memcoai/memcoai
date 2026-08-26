# Contributing

Thanks for taking the time to contribute. This repository holds the official
Memco SDKs; issues and pull requests are welcome from anyone.

You do **not** need access to Memco's servers to contribute. Every test suite
runs against an in-process server, so `make check` passes offline.

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
python/memco/memory/      generated Python client
go/client/                generated Go client
nodejs/client/            generated Node client
```

Everything else is hand-written — including `python/memco/__init__.py`, which
sits alongside the generated tree.

If you believe the generated code or the contract itself is wrong, open an issue
describing the problem rather than editing the output. `make provenance` checks
that the contract's checksum matches what each descriptor records, that they
agree on one server commit, and that the declared dependency floors match what
the generated modules assert at import. It does not verify the generated code
itself byte for byte. If it fails on a clean checkout, that is worth an issue on
its own.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and `make`. Nothing else — uv fetches
the language runtimes for you.

```bash
git clone https://github.com/memcoai/memco.git
cd memco
make install
make check
```

`make check` runs lint, strict type checking, the test suites, the provenance
check, and a documentation build — the parts of CI that run on every push. CI
additionally runs the suite across every supported runtime, builds and installs
the distributables, and checks the dependency bounds; `make test-all` and
`make build` cover most of that locally.

The repository root fans out to every language directory that carries a
`Makefile`, so `make test` covers everything present. `make help` lists the
targets. For anything language-specific, address one directly:

```bash
make -C python help
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

### What review will ask for

- **A test that fails before the fix.** For a bug, it must reproduce the bug.
- **Errors must be typed.** No raw transport error may reach a caller.
- **No credential may reach a log, a traceback or a `repr`.** This has been a
  real defect here: a dataclass `repr` put an API key into any crash report that
  captured local variables.
- **Public API changes need a strong reason**, and a note in the pull request
  saying what breaks. These packages are used by people who cannot easily change
  their code.
- **Suppressions need a reason.** `# noqa: RULE - why` is fine where the
  alternative would be a fiction; a bare suppression will be questioned.

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

## Documentation

Every public symbol needs a Google-style docstring with `Args`, `Returns`,
`Raises`, and an example where one helps. The reference is generated from those
by Sphinx, and the build runs with `nitpicky` and `-W`, so an unresolved
cross-reference fails CI rather than rotting on the published site.

Programs in `examples/` are imported by the test suite, so an example naming a
symbol that no longer exists fails the build.

## Style

Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/) and
type checking by [mypy](https://mypy-lang.org/) in strict mode, both configured
in [`python/pyproject.toml`](python/pyproject.toml). There is no separate style
guide to read — if `make check` is green, the style is right.

`# type: ignore[code]` and `# noqa: RULE - why` are accepted where the
alternative would be a fiction: grpc ships no type information, so its callback
signatures genuinely are `Any`. Say why in the comment.

## Releasing

Releases are cut from a tag on `main`. The tag is prefixed with the language it
releases, so each SDK in this repository versions independently.

1. Bump `version` in [`python/pyproject.toml`](python/pyproject.toml) and merge
   that to `main`.
2. Tag the merge commit and push the tag:

   ```bash
   git checkout main && git pull
   git tag python-v0.1.0
   git push origin python-v0.1.0
   ```

3. **CI runs, in full.** [`release.yml`](.github/workflows/release.yml) checks
   that the tag names the version the package declares and that the commit is
   contained in `main`, then re-runs every CI job — provenance, lint, strict
   typing, the suite on 3.10 through 3.13, both runtime-compat pin sets, the
   build, the docs, and the live smoke test.
4. **Approve the `pypi` environment.** The wheel and sdist go to PyPI.
5. **Publish the draft.** A draft GitHub release appears carrying three assets —
   the wheel, the sdist, and `memco-docs-python-<version>.tar.gz` — with
   generated notes. Edit them and publish.

Step 4 is the point of no return: a version on PyPI cannot be replaced,
re-uploaded, or meaningfully withdrawn. Everything before it is repeatable, and
the draft in step 5 can be deleted, but the upload cannot be taken back.

**That pause only exists if you configure it.** An environment with no
protection rule does not stop for anyone, so a tag push would publish to PyPI
unattended. Under **Settings → Environments → pypi**:

- add a **required reviewer**, which is what creates the pause;
- set **deployment branches and tags** to the tag pattern `python-v*`, so the
  token is unreachable from any other ref;
- keep `PYPI_API_TOKEN` as an *environment* secret rather than a repository one.
  Only a job naming this environment can then resolve it, and no job in
  `ci.yml` does.

Rejecting the approval ends the run with nothing uploaded, which is how you
rehearse the gate and the CI half of the pipeline. The publish and draft steps
are only ever exercised by a real release.

If the run fails *after* the upload — PyPI has the package but no draft
appeared — do not re-tag. Re-run the failed jobs from the run page: the `dist`
and `docs` artefacts are still attached to it, and re-running `uv publish` over
files PyPI already has is a no-op rather than an error.

A pre-release tag (`python-v0.1.0rc1`) is marked as a pre-release on GitHub
automatically and is skipped by `pip install` unless asked for — but it still
consumes that version on PyPI permanently, so it is a lower-stakes release, not
a free one.

### The documentation tarball

`memco-docs-python-<version>.tar.gz` is the built Sphinx reference, packaged so
it can be dropped into the documentation site without renaming anything:

```bash
tar xzf memco-docs-python-0.1.0.tar.gz -C <docs-site>/public/reference/
# -> public/reference/python/0.1.0/index.html
```

Every link inside it is relative, so it works from any mount path. Go and Node
will reuse the same `<language>/<version>/` shape.

---

## Licence

This repository is licensed under the [MIT licence](LICENSE), © Memco Labs, Inc.
By contributing you agree that your contributions will be released under the
same licence.

`python/LICENSE` is a copy of that file, because a published package has to
carry its own licence text. A test fails if the two drift apart, so update both
together.
