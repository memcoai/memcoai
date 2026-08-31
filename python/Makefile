# Python SDK tasks.
#
# Implements the target names the root Makefile fans out to, so `make lint` at
# the repository root reaches this file. Run it directly for anything
# Python-specific: `make -C python docs-serve`.

.DEFAULT_GOAL := help
UV ?= uv

# Every `uv run` re-syncs the environment to exactly the extras it names, so a
# target asking for fewer than another evicts that other's tools. Worse, uv
# recreates the environment outright when the interpreter it resolves differs
# from the current one. Asking for the same extras everywhere means any
# recreate restores a complete environment rather than a partial one.
EXTRAS := --extra dev --extra docs
RUN := $(UV) run $(EXTRAS)

.PHONY: help install lint format typecheck test test-all coverage docs docs-serve build clean

help:  ## Show this help
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Create the dev environment (floor version, per .python-version)
	$(UV) sync $(EXTRAS)

lint:  ## Check formatting and lint rules
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format:  ## Apply formatting and safe lint fixes
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

typecheck:  ## Strict type check of the SDK and tests
	$(RUN) mypy

test:  ## Run the test suite
	$(RUN) pytest -q

coverage:  ## Run the suite and report coverage (fails below the floor)
	$(RUN) pytest -q --cov --cov-report=term --cov-report=html --cov-report=xml
	@echo "open python/htmlcov/index.html"

test-all:  ## Run the suite on every supported interpreter
	@for v in 3.10 3.11 3.12 3.13; do \
		echo "--- python $$v ---"; \
		UV_PROJECT_ENVIRONMENT=.venv-$$v $(UV) run --python $$v $(EXTRAS) pytest -q \
			|| exit 1; \
	done

# The doctree cache is written beside the HTML rather than inside it: the
# output directory is published verbatim as `memco-docs-python-<version>.tar.gz`,
# and Sphinx's default `<outdir>/.doctrees` would put megabytes of pickles on
# the documentation site.
docs:  ## Build the reference documentation (warnings are errors)
	$(RUN) sphinx-build -b html -W --keep-going -d docs/_build/doctrees docs docs/_build/html
	@echo "open python/docs/_build/html/index.html"

docs-serve: docs  ## Build the reference and serve it locally
	$(RUN) python -m http.server -d docs/_build/html 8000

build:  ## Build the sdist and the wheel
	rm -rf dist
	$(UV) build

clean:  ## Remove build and cache artefacts
	rm -rf dist docs/_build htmlcov coverage.xml .coverage .venv-3.* \
		.mypy_cache .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
