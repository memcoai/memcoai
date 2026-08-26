# Python SDK tasks.
#
# Implements the target names the root Makefile fans out to, so `make lint` at
# the repository root reaches this file. Run it directly for anything
# Python-specific: `make -C python docs-serve`.

.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install lint format typecheck test test-all docs docs-serve build clean

help:  ## Show this help
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Create the dev environment (floor version, per .python-version)
	$(UV) sync --extra dev --extra docs

lint:  ## Check formatting and lint rules
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:  ## Apply formatting and safe lint fixes
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck:  ## Strict type check of the SDK and tests
	$(UV) run mypy

test:  ## Run the test suite
	$(UV) run pytest -q

test-all:  ## Run the suite on every supported interpreter
	@for v in 3.10 3.11 3.12 3.13; do \
		echo "--- python $$v ---"; \
		$(UV) run --python $$v --extra dev pytest -q || exit 1; \
	done

docs:  ## Build the reference documentation (warnings are errors)
	$(UV) run --extra docs sphinx-build -b html -W --keep-going docs docs/_build
	@echo "open python/docs/_build/index.html"

docs-serve: docs  ## Build the reference and serve it locally
	$(UV) run python -m http.server -d docs/_build 8000

build:  ## Build the sdist and the wheel
	rm -rf dist
	$(UV) build

clean:  ## Remove build and cache artefacts
	rm -rf dist docs/_build .mypy_cache .ruff_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
