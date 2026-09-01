# Node.js SDK tasks.
#
# Implements the target names the root Makefile fans out to, so `make lint` at
# the repository root reaches this file. Run it directly for anything
# Node-specific: `make -C nodejs docs-serve`.

.DEFAULT_GOAL := help
NPM ?= npm

# Every multi-step operation is a chained `npm run` script, never a pre/post
# lifecycle hook: .npmrc sets `ignore-scripts=true`, so npm never fires
# `prebuild` or `posttest`, and a step written that way would be skipped in
# silence behind a green build. The scripts chain with `&&` for the same reason.
RUN := $(NPM) run --silent

# The runtimes `test-all` sweeps. `.nvmrc` names the one to develop against and
# `engines.node` names the floor; these are every line still supported upstream,
# and every line CI's matrix covers.
NODE_VERSIONS := 22 24 26

.PHONY: help install lint format typecheck test test-all coverage docs docs-serve build clean

help:  ## Show this help
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install the development toolchain from the lockfile
	$(NPM) ci

lint:  ## Check formatting
	$(RUN) lint

format:  ## Apply formatting
	$(RUN) format

typecheck:  ## Strict type check of the SDK, the generated client and the tests
	$(RUN) typecheck

# Compiled first, then run as JavaScript. Node's type stripping refuses the
# generated client outright: ts-proto emits `export enum DataSource`, which is
# not erasable syntax, so `node --test` over the .ts sources dies before a
# single test runs. --enable-source-maps puts the frames back on src/*.ts.
test:  ## Run the suite against a real in-process gRPC server
	$(RUN) test

# 95% of lines is the floor this SDK holds. Branches sit at 90 because V8
# counts every implicit branch — optional chaining, `??`, default parameters,
# catch — so the denominator holds paths no test can name; it is not a lower
# bar for the code. The generated client is excluded — it is the export's to
# test, not ours; the test files are excluded explicitly because passing
# --test-coverage-exclude at all replaces Node's own default exclusion of them.
#
# The report names src/*.ts, not the compiled build/js/src/*.js: Node reads the
# source maps tsconfig.test.json emits, so the uncovered lines it prints are
# the ones to go and look at.
#
# src/types.ts is excluded for the same reason, from the other direction. It is
# overwhelmingly `interface`, which emits no JavaScript at all, but source-mapped
# coverage still attributes every one of them to an uncovered source line — so
# measuring it caps the whole package around 93% however well it is tested. The
# runtime code it does hold, three enums and their wire readers, is covered at
# 100% by tests/types.test.ts, which still runs; only the denominator changes.
coverage:  ## Run the suite and report coverage (fails below the floor)
	$(RUN) coverage
	@echo "open nodejs/coverage/lcov.info"

# One compile, several runtimes, so a failure is the runtime's and not a
# difference in how the sources were built for it. Note this sweeps the test
# build, not the published one — `build` is what produces dist/esm and dist/cjs,
# and the packed tarball is exercised by CI's install check rather than here.
#
# Nothing here can fetch a runtime; it runs the ones already installed. A
# version that is missing is reported and skipped rather than failing the
# target — but the target fails if that leaves nothing to run, because a sweep
# that silently tested nothing is worse than one that says so. CI's matrix is
# the authority.
test-all:  ## Run the suite on every supported runtime installed locally
	@$(RUN) build:test
	@ran=0; \
	for v in $(NODE_VERSIONS); do \
		bin="$$HOME/.nvm/versions/node/$$(ls -1 "$$HOME/.nvm/versions/node" 2>/dev/null \
			| grep -E "^v$$v\." | sort -V | tail -1)/bin/node"; \
		if [ ! -x "$$bin" ]; then \
			printf '\033[33m--- node %s: not installed, skipped ---\033[0m\n' "$$v"; \
			continue; \
		fi; \
		printf '\033[1m--- node %s ---\033[0m\n' "$$v"; \
		"$$bin" --test --enable-source-maps 'build/js/tests/**/*.test.js' || exit 1; \
		ran=$$((ran + 1)); \
	done; \
	if [ "$$ran" -eq 0 ]; then \
		echo "no supported runtime is installed; install one of: $(NODE_VERSIONS)" >&2; \
		exit 1; \
	fi

# TypeDoc writes nothing but the site into its output directory, so that
# directory is published verbatim as `memco-docs-nodejs-<version>.tar.gz`. It
# keeps no build cache, so nothing has to be routed out of the output directory
# first; `githubPages: false` in typedoc.json suppresses the one extra file it
# would otherwise leave behind. Warnings and unresolved links are errors, so a
# stale {@link} fails the build instead of shipping.
# The markdown pass is a second run over the same entry point, into
# docs/_build/markdown, for scripts/build_llms_txt.py to assemble into the
# llms.txt pair. It runs after the HTML: typedoc.json sets cleanOutputDir, so
# the HTML pass empties the directory the assembler writes into.
#
# The assembler is invoked from here rather than chained into the npm script
# because it is python3, and `npm run docs` should stay runnable by someone who
# has only Node. The interpreter is a prerequisite of the repository root
# already — `make provenance` runs a script in it.
docs:  ## Build the reference documentation (warnings are errors)
	$(RUN) docs
	$(RUN) docs:markdown
	python3 ../scripts/build_llms_txt.py nodejs
	@echo "open nodejs/docs/_build/html/index.html"

# The interpreter this serves with is already a prerequisite of the repository
# root (`make provenance` runs a script in it), so serving the site adds no npm
# dependency.
docs-serve: docs  ## Build the reference and serve it locally
	python3 -m http.server -d docs/_build/html 8000

build:  ## Build the ESM and CommonJS distributables and the npm tarball
	$(RUN) build
	$(NPM) pack

clean:  ## Remove build and cache artefacts
	rm -rf dist build docs/_build coverage *.tgz *.tsbuildinfo
