# Go SDK tasks.
#
# Implements the target names the root Makefile fans out to, so `make lint` at
# the repository root reaches this file. Run it directly for anything
# Go-specific: `make -C go docs-serve`.
#
# Two modules live here: the SDK (this directory) and examples/, which has its
# own go.mod so the example's model SDK never becomes a requirement of the SDK.
# Every target that compiles covers both.

.DEFAULT_GOAL := help
GO ?= go

# One target at a time, whatever -j the caller passes: GOFLAGS already decides
# how much of the machine one go command takes, and the race-detector suites
# are heavy.
.NOTPARALLEL:

# A go.work would resolve examples/ through the workspace rather than through
# its replace directive, hiding what a build without one would hit
# (.gitignore keeps one out of the tree for the same reason).
export GOWORK := off

# Tools run at pinned versions through `go run`, so none is ever a go.mod
# requirement. golangci-lint v2.13.2 declares go 1.26.0; naming the toolchain
# exactly avoids GOTOOLCHAIN=+auto's proxy lookup, which fails offline. CI,
# where setup-go installed 1.26 and exported GOTOOLCHAIN=local, passes
# LINT_TOOLCHAIN=local. GOLANGCI_LINT=golangci-lint uses an installed binary of
# the same version instead.
LINT_TOOLCHAIN ?= go1.26.8
GOLANGCI_LINT ?= GOTOOLCHAIN=$(LINT_TOOLCHAIN) $(GO) run github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.13.2
DOC2GO = $(GO) run go.abhg.dev/doc2go@v0.12.2
GOMARKDOC = $(GO) run github.com/princjef/gomarkdoc/cmd/gomarkdoc@v1.1.0

# The floor go.mod declares and every line since: the lines CI's matrix covers.
# Named in full because GOTOOLCHAIN takes nothing shorter; go fetches one that
# is missing.
GO_TOOLCHAINS := go1.25.13 go1.26.8 go1.27.0

# Statements, over the hand-written packages only: the generated client is the
# export's to test, and internal/testserver exists to be tested against. The
# README's coverage badge claims this floor.
COVERAGE_FLOOR := 95
COVERPKG := ./memcoai/...,./internal/admin/...,./internal/config/...,./internal/transport/...,./internal/fault/...,./internal/logging/...,./internal/warn/...,./internal/provenance/...,./internal/memory/...

# The commit the markdown reference's source links name, so a published
# version's line anchors stay right; `main` only where there is no git to ask.
REVISION = $$(git rev-parse HEAD 2>/dev/null || echo main)

.PHONY: help install lint format typecheck test test-all system-test run-examples coverage docs docs-serve build clean

help:  ## Show this help
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Download both modules' dependencies and the pinned tools
	$(GO) mod download
	cd examples && $(GO) mod download
	$(GOLANGCI_LINT) version
	$(DOC2GO) -version
	$(GOMARKDOC) --version

# Formatting drift is reported by `run` through the formatters .golangci.yml
# enables, so there is no separate gofmt step.
lint:  ## Check formatting, lint rules and static analysis (both modules)
	$(GOLANGCI_LINT) config verify
	$(GOLANGCI_LINT) run ./...
	cd examples && $(GOLANGCI_LINT) run ./...

# fmt walks the directory tree rather than the module, so examples/ is covered.
format:  ## Apply gofmt (simplify) and goimports (both modules)
	$(GOLANGCI_LINT) fmt

# -tags systemtest: nothing else hermetic compiles the live suite, so this
# keeps it from rotting unbuilt.
typecheck:  ## go vet over both modules, the live suite included
	$(GO) vet -tags systemtest ./...
	cd examples && $(GO) vet ./...

test:  ## Run both suites against a real in-process gRPC server
	$(GO) test -race ./...
	cd examples && $(GO) test -race ./...

coverage:  ## Run the suites and report coverage (fails below the floor)
	@mkdir -p coverage
	$(GO) test -race -coverpkg=$(COVERPKG) -coverprofile=coverage/coverage.out ./...
	cd examples && $(GO) test -race ./...
	@$(GO) tool cover -html=coverage/coverage.out -o coverage/index.html
	@$(GO) tool cover -func=coverage/coverage.out | awk -v floor=$(COVERAGE_FLOOR) \
		'/^total:/ { sub(/%/, "", $$3); total = $$3 } \
		END { printf "coverage: %s%% of statements (floor %s%%)\n", total, floor; if (total + 0 < floor) exit 1 }'
	@echo "open go/coverage/index.html"

# Not reached by `test`: the suite's files carry //go:build systemtest. The
# memory tests need MEMCO_API_TOKEN, and the administration and impersonation
# tests MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET, plus a reachable service;
# without its credential a test reports itself skipped. MEMCO_API_TLS=false
# reaches a local development server in plaintext. -count=1 because a cached
# pass against a live service proves nothing, and -timeout sits below CI's 45
# minutes so a hang dumps its goroutines instead of being killed silently.
system-test:  ## Run the live suite against the real service
	$(GO) test -tags systemtest -count=1 -timeout 40m -v ./systemtest/...

# The examples that act as an API client. Every other example is run without
# MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET: with no credential in Options the
# pair wins over the token, and an issued token reads no memory of its own.
CLIENT_EXAMPLES := map_your_users act_as_your_user act_as_many_users

# Unlike system-test, this does not skip without a credential: it assumes
# MEMCO_API_TOKEN, MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET, and whatever
# anthropic_agent additionally needs, are already in the environment (see
# examples/README.md).
run-examples:  ## Run every example against the real service
	@cd examples && for pkg in $$($(GO) list -f '{{if eq .Name "main"}}{{.ImportPath}}{{end}}' ./...); do \
		printf '\033[1m--- %s ---\033[0m\n' "$${pkg##*/}"; \
		case " $(CLIENT_EXAMPLES) " in \
			*" $${pkg##*/} "*) $(GO) run "$$pkg" || exit 1 ;; \
			*) env -u MEMCO_CLIENT_ID -u MEMCO_CLIENT_SECRET $(GO) run "$$pkg" || exit 1 ;; \
		esac; \
	done

test-all:  ## Run both suites on every supported toolchain
	@for v in $(GO_TOOLCHAINS); do \
		printf '\033[1m--- %s ---\033[0m\n' "$$v"; \
		GOTOOLCHAIN=$$v $(GO) test -race ./... || exit 1; \
		(cd examples && GOTOOLCHAIN=$$v $(GO) test -race ./...) || exit 1; \
	done

# Only the public package is documented: doc2go leaves internal packages out
# unless -internal is passed (never pass it), and gomarkdoc is pointed at
# memcoai/ alone. Both trees are emptied first, because neither tool removes
# output and the assembler rightly refuses a page the sources no longer
# describe. gomarkdoc runs from memcoai/ so its tree mirrors doc2go's:
# index.md beside index.html. All of its options are passed explicitly,
# because it also reads them from the environment.
docs:  ## Build the reference documentation
	rm -rf docs/_build/html docs/_build/markdown
	$(DOC2GO) -out docs/_build/html -home github.com/memcoai/memcoai/go/memcoai \
		-rel-link-style index -pagefind=false ./memcoai/...
	cp README.md docs/_build/html/README.md
	cd memcoai && $(GOMARKDOC) --format github \
		--output '../docs/_build/markdown/{{.Dir}}/index.md' \
		--repository.url https://github.com/memcoai/memcoai \
		--repository.default-branch "$(REVISION)" \
		--repository.path /go/memcoai ./...
	python3 ../scripts/build_llms_txt.py go
	@echo "open go/docs/_build/html/index.html"

docs-serve: docs  ## Build the reference and serve it locally
	python3 -m http.server -d docs/_build/html 8000

# A Go module ships as source: the proxy zips the tagged tree and a consumer
# compiles it. So "distributable" means go.mod and go.sum are exactly what tidy
# writes, every dependency matches go.sum, and both modules build. CI also zips
# the module the way the proxy would and builds a fresh consumer against it.
build:  ## Prove the module is whole and builds on its own terms
	$(GO) mod tidy -diff
	$(GO) mod verify
	$(GO) build ./...
	cd examples && $(GO) mod tidy -diff && $(GO) build ./...

clean:  ## Remove build and cache artefacts
	rm -rf docs/_build coverage
