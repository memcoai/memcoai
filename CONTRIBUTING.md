# Contributing

Thanks for taking the time to contribute. This repository holds the official
Memco SDKs; issues and pull requests are welcome from anyone.

You do **not** need access to Memco's servers to contribute. The whole test
suite runs against an in-process gRPC server, so `make check` passes offline.

## Reporting a bug

[Open an issue](https://github.com/memcoai/memco/issues) and include:

- what you did, what you expected, and what happened instead
- the SDK version (`pip show memco`) and your language runtime version
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
python/client/            generated Python client
go/client/                generated Go client
nodejs/client/            generated Node client
```

The hand-written SDK lives beside it — for Python that is `python/memco/client/`.

If you believe the generated code or the contract itself is wrong, open an issue
describing the problem rather than editing the output. `make provenance` checks
that the checked-in generated code, the dependency floors it declares, and the
contract it claims to come from all agree; if that fails on a clean checkout,
that is worth an issue on its own.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and `make`. Nothing else — uv fetches
the right Python for you.

```bash
git clone https://github.com/memcoai/memco.git
cd memco
make install
make check
```

`make check` runs exactly what CI runs: lint, strict type checking, the test
suite, the provenance check, and a documentation build. If it is green locally
it will be green in CI.

Useful targets:

```bash
make help                   # every target, and which languages are covered
make format                 # apply formatting and safe lint fixes
make -C python test-all     # the Python suite on 3.10 through 3.13
make -C python docs-serve   # build the reference docs and serve them
make -C python help         # every Python-specific target
```

The repository root fans out to each language directory that carries a
`Makefile`, so `make test` covers everything present.

## Making a change

1. **Open an issue first** for anything beyond a small fix, so the approach can
   be agreed before you spend time on it.
2. **Write a test that fails.** For a bug, it should reproduce the bug; for a
   feature, it should specify the behaviour. Confirm it fails for the reason you
   expect before writing the fix.
3. **Keep the change surgical.** Touch only what the change requires. Match the
   surrounding style rather than reformatting adjacent code.
4. **Run `make check`.** Every part must pass.
5. **Open a pull request** describing what changed and why, and linking the
   issue.

### Things that will be asked for in review

- **Public API changes need a strong reason.** These packages are used by
  people who cannot easily change their code; a rename is a breaking change.
- **Every public symbol needs a docstring** with `Args`, `Returns`, `Raises` and
  an example where it helps. The reference documentation is generated from them,
  and CI fails on a broken cross-reference.
- **Errors must be typed.** No raw `grpc.RpcError` may reach a caller.
- **No credential may reach a log, a traceback or a `repr`.**

### Style

Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/) and
type checking by [mypy](https://mypy-lang.org/) in strict mode; both are
configured in `python/pyproject.toml`. `make format` applies what can be applied
automatically. There is no separate style guide to read — if `make check` is
green, the style is right.

Optionally, install the hooks so this runs before each commit:

```bash
pre-commit install
```

## Licence

This repository is licensed under the [MIT licence](LICENSE), © The Memory
Company (Memco). By contributing you agree that your contributions will be
released under the same licence.

`python/LICENSE` is a copy of that file, because a published package has to
carry its own licence text. A test fails if the two drift apart, so update both
together.
