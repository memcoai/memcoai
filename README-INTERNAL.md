# Internal notes

Delete this file before the public release.

## Installing before the packages are published

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

**`@memcoai/memco` has no equivalent** — npm cannot install from a subdirectory
of a repository — so build the tarball from a clone and install that:

```bash
git clone -b dev https://github.com/memcoai/memco.git
cd memco/nodejs && npm ci && npm run build && npm pack
# then, from your own project:
npm install /path/to/memco/nodejs/memcoai-memco-0.1.0.tgz
```

The tarball rather than `npm install ./memco/nodejs`: it carries exactly the
files the published package will, while a directory install links the whole
working tree, build output and all.
