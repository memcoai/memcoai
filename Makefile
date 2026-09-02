# Development tasks for the Memco SDKs.
#
# This file holds nothing language-specific. Each language directory carries a
# Makefile implementing the target names below, and they are discovered
# automatically — a new language joins by adding its own Makefile, with no
# change here.
#
# For anything outside the shared set, address one language directly:
#
#     make -C python docs-serve
#     make -C python help

.DEFAULT_GOAL := help

# Every directory with a Makefile is a language the fan-out covers.
LANGUAGES := $(patsubst %/Makefile,%,$(wildcard */Makefile))

# Targets every language Makefile is expected to implement, no-op where a
# language has nothing to do for one.
FANOUT := install lint format typecheck test test-all system-test docs build clean

.PHONY: help provenance tool-docs tool-docs-check check $(FANOUT)

define fanout
@for lang in $(LANGUAGES); do \
	printf '\033[1m==> %s: %s\033[0m\n' "$$lang" "$(1)"; \
	$(MAKE) --no-print-directory -C $$lang $(1) || exit 1; \
done
endef

help:  ## Show this help
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@printf '\n  languages: %s\n' "$(LANGUAGES)"
	@printf '  language-specific targets: make -C <language> help\n'

install:  ## Create every development environment
	$(call fanout,install)

lint:  ## Check formatting and lint rules
	$(call fanout,lint)

format:  ## Apply formatting and safe lint fixes
	$(call fanout,format)

typecheck:  ## Strict type check
	$(call fanout,typecheck)

test:  ## Run the test suites
	$(call fanout,test)

test-all:  ## Run the suites on every supported runtime
	$(call fanout,test-all)

# Deliberately absent from `check`: that has to pass offline, with no
# credential. This one needs MEMCO_API_TOKEN and a reachable service, and
# reports itself skipped without them.
system-test:  ## Run the live suites against the real service
	$(call fanout,system-test)

docs:  ## Build the reference documentation
	$(call fanout,docs)

build:  ## Build the distributable artefacts
	$(call fanout,build)

clean:  ## Remove build and cache artefacts
	$(call fanout,clean)

provenance:  ## Verify every generated client matches the contract it claims
	python3 scripts/verify_provenance.py

tool-docs:  ## Write the service's tool copy into the SDK docstrings
	python3 scripts/sync_tool_docs.py

tool-docs-check:  ## Verify the docstrings still carry the manifest's copy
	python3 scripts/sync_tool_docs.py --check

check: lint typecheck test provenance tool-docs-check docs  ## Everything CI runs
