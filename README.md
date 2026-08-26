# memco

Official SDKs for [Memco Shared Memory](https://memco.ai), a persistent store
your team and its agents share.

| Language | Package | Status |
|---|---|---|
| Python | [`memco`](python/) | v1 — memory operations |
| Go | [`github.com/memcoai/memco/go/memco`](go/) | generated client only |
| Node | [`memco`](nodejs/) | generated client only |

## Layout

```
proto/            the contract, for reference — SDKs do not generate from it
python/  memco/client/**   the SDK          client/**   generated
go/      memco/*.go        the SDK          client/**   generated
nodejs/  src/**            the SDK          client/**   generated
```

Everything under `proto/` and `<lang>/client/` is written by the server
repository's SDK export and is emptied and rewritten on each run. Nothing
outside those paths is ever generated, and nothing inside them should be edited
by hand.

## Development

```bash
make install   # create every development environment
make check     # lint, typecheck, test, provenance, docs — everything CI runs
make help      # every target, and which languages are covered
```

The root `Makefile` holds nothing language-specific: it discovers every
directory carrying a `Makefile` and fans the shared targets out to each. A new
language joins by adding its own, with no change at the root. For anything
outside the shared set, address one directly:

```bash
make -C python docs-serve
make -C python help
```

`scripts/verify_provenance.py` checks that the checked-in generated code, the
dependency floors it declares, and the contract it claims to come from all
agree. CI runs it on every push, and `make check` runs it locally.

## Getting started

See [`python/README.md`](python/README.md) and the runnable
[`python/examples/`](python/examples/).
