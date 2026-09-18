# Go SDK for Memco Shared Memory — implementation plan

> **Executor, first action:** copy this file verbatim to `plans/go-sdk.md` in the repository (create `plans/`). Everything below stands alone; it cites only committed files. Leave all work uncommitted — the user reviews and commits.

## Context

`memcoai/memcoai` publishes hand-written SDKs in Python (`python/memcoai`, PyPI `memcoai`) and Node (`nodejs/src`, npm `@memco/memcoai`) that wrap gRPC clients generated from `proto/memcoai/memory/v1/memory.proto`. `go/` holds only generated code (`go/client/memcoai/memory/v1/*.pb.go`, `go/client/tools/{tools.go,tools.json}`, `go/client/SDK_PROVENANCE.yaml`), no `go.mod`, no SDK. The user wants the Go SDK built out to full parity with Python/Node: credential/endpoint resolution, connection verification, typed errors, service-reported limits and deprecations, sessions, the agent toolset, examples, system test, CI, release and reference docs.

The SDK's job (AGENTS.md): everything a caller can observe is ours; anything only the service decides stays the service's — never hardcode a service-owned value.

## Decisions already settled with the user

| Topic | Decision |
|---|---|
| Scope | Everything in one plan: library, agent toolset, examples, system test, CI **and** release, docs |
| Generated client | **Internal**: `git mv go/client go/internal/client` during development (no stop; the user fixes the exporter afterwards) |
| Public vs internal | `go/memcoai` exposes only what Python exposes without a leading underscore (`__init__`, `types`, `errors`, `operations`, `agent`): client, options, errors, operations/session, data types, agent toolset. **Every helper lives under `go/internal/`** (Python's `_*.py`), working on wire types or plain values. The only non-exported code in `go/memcoai` is the type-bound glue Go's import rules force there: field-by-field wire↔public mapping and internal-fault→public-error mapping |
| Second service | A user-management service is coming. Everything not memory-specific (config, transport/auth/retries, fault translation, logging, warnings, provenance) is service-agnostic under `go/internal/`; memory-specific helpers live in `go/internal/memory/`. The future service adds `go/internal/usermanagement/`, its generated client under `go/internal/client/`, a `Client` field beside `Memory`, and its own retry list |
| Doc comments | Internal code: very short or none when the name says it. Exported functions and methods: Python-docstring depth — every parameter described, what is returned, which typed errors, and an `Example…` function (see "Doc comment convention"). Only `go/memcoai` is documented; internal packages are excluded from the docs build |
| Context | Every call that can block takes a `context.Context` and honours cancellation: all RPCs, `Connect`, `Close` (drain wait), `ImportMemories` (checked between groups), `StartSession`'s concurrent catalog fetch, `Toolset.Call`, polling in the system test, examples (`signal.NotifyContext`) |
| Errors | **Every error is typed.** Nothing crosses an API boundary as `errors.New`, `fmt.Errorf` or a bare `ctx.Err()`: internal packages return `*fault.Error`, the public API returns the concrete types under "Public API" (a caller's own context ending becomes `*TimeoutError`/`*InternalError` whose `Unwrap` is the context error). **An error is either handled or returned, never logged and returned** (Go practice) — so the Node/Python `credential rejected by …` and `… failed in Nms` records are not ported |
| Logging | `log/slog` through a **dedicated SDK logger** (its own handler on stderr, never `slog.Default()`), level from `MEMCO_LOG`; `Options.LogLevel` and `SetLevel` change it; `Options.Logger` replaces it per client; `none` silences everything, deprecation notices included |
| Module | `go/go.mod`, `module github.com/memcoai/memcoai/go`; SDK package `go/memcoai` → `import "github.com/memcoai/memcoai/go/memcoai"` |
| Release tags | `go-vX.Y.Z` triggers the release, matching `python-v*`/`nodejs-v*`; after every check passes, the publish job creates `go/vX.Y.Z` (the only form the Go toolchain resolves for a module in `go/`) on the same commit. *(Changed 2026-09-17 at the user's request; the plan originally triggered on `go/v*`.)* |
| Agent example | `github.com/anthropics/anthropic-sdk-go` in a **nested** module `go/examples` (SDK module gets no dependency). No LangChain in Go |
| Docs | doc2go (HTML) + gomarkdoc (markdown) run via pinned `go run`; Go target in `scripts/build_llms_txt.py`; `memco-docs-go-<v>.tar.gz` on release |
| Parity rule | Where Python and Node diverge, Go follows **Node** unless a Go-specific reason is listed under "Go adaptations" |
| Lint / format / static analysis | A checked-in **golangci-lint v2** config, `go/.golangci.yml` (formatters gofmt `simplify` + goimports; linters standard set + errorlint, nolintlint, misspell, bidichk; staticcheck `all`), driven by `make -C go lint` / `format`; pinned `golangci-lint v2.13.2` |

User preferences that bind execution: tests first (all of them), confirm each fails for the expected reason, then implement path by path; `git mv` for moves, never editing a moved file; never commit; run heavy make targets one at a time (the machine has `GOFLAGS=-p=8`); don't start long-running processes; no `gh` CLI.

## Upstream (server-repo export) changes the user will make afterwards

Report these to the user when done (they asked to be told):

> **Status, 2026-09-17:** items 1, 2 and 6 landed in the export during implementation (the export now writes `go/internal/client/`, `go_package` names it, and `tools.json` describes `tags`, `feedback` and `source` in the gRPC shape). With 6 in, the user chose to drop `SDK_SHAPED` and the hand-written copy in all three SDKs; each SDK still describes the fields of a `Tag` and a `FeedbackRating` itself, since the manifest describes only the lists.

1. Export destination for Go: `go/client/` → `go/internal/client/` (all of it: `memcoai/memory/v1/*.pb.go`, `tools/tools.go`, `tools/tools.json`, `SDK_PROVENANCE.yaml`). In `sdk-export.sh` this includes the "public Go differs from tested Go" `diff -r` path and the Go copy loop.
2. `proto/memcoai/memory/v1/memory.proto`: `option go_package = "github.com/memcoai/memcoai/go/internal/client/memcoai/memory/v1;memoryv1";` (plus its comment). This changes the contract checksum, so all three `SDK_PROVENANCE.yaml` files are regenerated together (the in-repo build keeps using protoc's `M` flag).
3. The export must treat as hand-written (never empty or overwrite): everything under `go/` except `go/internal/client/` — in particular `go/internal/*.go`, every other `go/internal/<pkg>/` (`config`, `transport`, `fault`, `logging`, `warn`, `provenance`, `memory`, `testserver`), `go/go.mod`, `go/go.sum`, `go/.golangci.yml`, `go/memcoai/`, `go/examples/`, `go/systemtest/`. The coming user-management client should be exported beside the memory one, under `go/internal/client/memcoai/<service>/v1`.
4. `go/internal/client/tools/tools.go`'s package comment speaks of "the method names this SDK exposes"; now that the package is internal and the SDK's agent layer uses generated `memco_*` copy, that accessor is unused by the SDK — the export may keep or drop it (nothing here imports it; `tools.json` beside it is still required by `scripts/verify_provenance.py` and the Go tool-copy test).
5. Until 1–2 land, a fresh export would recreate `go/client/`; the stale `go_package` string embedded in the moved `memory.pb.go` descriptor is harmless at runtime (import paths come from directories).
6. `tools.json` publishes the MCP server's wire-shaped copy for parameters the gRPC contract types differently: `tags` (search, create_memory, enrich_memory, import_memories `memories[].tags`) describes XML strings, `share_feedback.feedback` describes `<feedback …>` XML, and `source` (create_memory, enrich_memory) describes the literals `'user'`/`'agent'` — but the contract takes `repeated Tag`, `repeated FeedbackRating` and the `DataSource` enum. The export should publish gRPC-shaped copy for these (e.g. from the `Tag`/`FeedbackRating`/`DataSource` comments in `memory.proto`); then `SDK_SHAPED` in `scripts/sync_tool_docs.py` and each SDK's hand-written copy (Go `reshaped`, Node `RESHAPED`, Python docstrings) can go. Until then no SDK hands that copy to a model.

---

## Step 0 — the move (pure rename)

```
mkdir -p go/internal
git mv go/client go/internal/client
git status   # must show only renames (R100), no content changes
```

The user approved doing this during development; no stop is needed — they will fix the exporter afterwards. No file under `go/internal/client/` is ever edited, so the renames stay pure (R100) in the uncommitted diff alongside everything else.

## Verified facts the design rests on (do not re-derive)

- grpc-go v1.83.1 (module cache `~/go/pkg/mod/google.golang.org/grpc@v1.83.1`):
  - dns resolver fetches TXT service config by default (`internal/envconfig`: `EnableTXTServiceConfig` defaults true; `internal/resolver/dns/dns_resolver.go:144`). `grpc.WithDisableServiceConfig()` + `grpc.WithDefaultServiceConfig(json)` therefore reproduces Python/Node's `grpc.service_config` *override*. `service_config.go` parses `retryPolicy` in `methodConfig`. Retries need no env var.
  - `grpc.NewClient` runs `url.Parse(target)` and honours any registered scheme (`dns`, `unix`, `passthrough`), so a host named `unix` with port 443 would dial a unix socket. Always build the target as `(&url.URL{Scheme: "dns", Path: "/" + hostport}).String()` (also survives `[fe80::1%eth0]:443`).
  - Exhausted retries return `fmt.Errorf("max retries exhausted: failed after %d attempts: %w", …)` (`stream.go:~768`); `status.FromError` would then report that whole string as the message. The error translator must `errors.As(err, &interface{ GRPCStatus() *status.Status })` to read the server's own status/details.
  - `grpc.WithConnectParams` copies `MinConnectTimeout`; zero makes the dial deadline ≈ the 200 ms backoff (`clientconn.go:~1334`). Always set `MinConnectTimeout: 20 * time.Second` and start from `backoff.DefaultConfig` with `BaseDelay: 200ms`, `MaxDelay: 5s`.
  - A proto marshal failure (invalid UTF-8 in a string field) surfaces from `Invoke` as `codes.Internal "grpc: error while marshaling: … string field contains invalid UTF-8"`. The SDK must catch it before sending.
  - `codes.Code.String()` is CamelCase (`InvalidArgument`, `Canceled`); the SDK needs its own canonical table (`INVALID_ARGUMENT`, `CANCELLED`, … `UNRECOGNIZED(n)`).
  - `grpc.WithUserAgent(s)` → server sees `s + " grpc-go/1.83.1"`. Health client: `google.golang.org/grpc/health/grpc_health_v1` (same module).
  - `google.golang.org/genproto/googleapis/rpc v0.0.0-20260526163538-3dc84a4a5aaa` is already required by grpc v1.83.1; `errdetails.ErrorInfo` lives there — making it a direct require adds nothing to the build graph.
- `go:embed` only refuses `..` and paths crossing a `go.mod`. A file `go/internal/descriptor.go` (package `internal`) **can** embed `client/SDK_PROVENANCE.yaml` even though `client/` holds other packages (probed with Go 1.25.13). So provenance needs no checked-in copy.
- Go's `internal` rule is by import path, so the nested module `github.com/memcoai/memcoai/go/examples` may import `github.com/memcoai/memcoai/go/internal/testserver`.
- Local module cache has grpc v1.83.1, protobuf v1.36.12, genproto rpc, toolchains go1.25.13 / go1.26.8 / go1.27.0. It does **not** have anthropic-sdk-go (latest v1.73.0), doc2go (v0.12.2), gomarkdoc (v1.1.0) or golangci-lint v2.13.2 as a module (a v2.13.2 binary is installed at `/snap/bin/golangci-lint`) — those need network.
- `fmt` prints unexported string fields of a struct raw under `%v/%+v`; credentials must never be a plain string field of any struct a caller can print.
- golangci-lint v2.13.2 (latest; `/snap/bin/golangci-lint` locally is the same version) declares `go 1.26.0`; `actions/setup-go` exports `GOTOOLCHAIN=local`, so CI's single-version jobs pin **Go 1.26** and the matrix covers 1.25 and 1.27 (Node's shape: pin 24, matrix 22/26). Locally, name the toolchain exactly (`GOTOOLCHAIN=go1.26.8`) — `+auto` asks the proxy for the toolchain list and fails offline.
- golangci-lint v2 config probed in a scratch module (Go 1.25.13): `version: "2"`, `run.build-tags`, `linters.default: standard` (errcheck, govet, ineffassign, staticcheck, unused), `linters.settings.staticcheck.checks`, `linters.settings.nolintlint.require-explanation/require-specific`, `linters.exclusions.generated` + `paths`, `formatters.enable: [gofmt, goimports]`, `formatters.settings.gofmt.simplify`, `formatters.settings.goimports.local-prefixes`, `formatters.exclusions.paths` — `golangci-lint config verify` accepts it; `run` flags gofmt/goimports issues and skips excluded paths (and, unless `generated: disable`, `// Code generated … DO NOT EDIT.` files); `golangci-lint fmt --diff` prints the fixes. It finds `.golangci.yml` by walking up from the working directory, so `go/examples` (nested module) uses `go/.golangci.yml`.
- `golangci/golangci-lint-action` `v9` → `ba0d7d2ec06a0ea1cb5fa41b2e4a3ab91d21278a` (not used: CI runs `make lint` so the version pin lives in one place).
- `actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16` is `v6` / `v6.5.0` (re-confirm: `git ls-remote https://github.com/actions/setup-go refs/tags/v6 refs/tags/v6.5.0`). Do not use `go-version-file: go/go.mod` (it would install exactly 1.25.0).
- doc2go v0.12.2 flags: `-out`, `-home`, `-rel-link-style plain|directory|index`, `-pagefind[=auto|true|false]`, `-embed`, `-internal`, `-tags`; assets under `<out>/_/`; with `-home <pkg>` that package is `<out>/index.html`. No warnings-as-errors mode.
- gomarkdoc v1.1.0 flags: `--output` (template over `{{.Dir}}`), `--format github`, `--repository.url|default-branch|path` (setting all three skips go-git detection), `--tags`. Pages open with `<!-- Code generated by gomarkdoc. DO NOT EDIT -->`, symbols get `<a name="Type.Method">` anchors (same ids as doc2go), and **every link destination is angle-bracketed** (`[Client](<#Client>)`), which `build_llms_txt.py`'s `LINK` regex must be taught to read. It binds flags to env vars (`OUTPUT`, `FORMAT`, `CHECK`): always pass every option explicitly.
- A subdirectory module's zip gets the repo-root LICENSE only when the subdirectory has none; once `go/LICENSE` exists it is what ships, so a test guards it.
- proxy.golang.org cannot fetch a private repository: the repo must be public before the first `go/v*` tag (release_nodejs.yaml comments say it is not yet).

---

## Layout

```
go/
  go.mod  go.sum  LICENSE (copy of root)  README.md  Makefile  .golangci.yml
  memcoai/                    PUBLIC — package memcoai (the only documented package)
    doc.go version.go
    client.go                 Client, NewClient, Options, Connect, Close, Provenance, ReadProvenance, SetLevel, constants
    errors.go                 public error types + unexported fault→error mapping
    memory.go session.go      MemoryOperations, Session, *Params
    types.go                  data types, enums, Memory.Feedback
    convert.go                unexported wire↔public field mapping (the only glue kept here)
    agent.go                  Toolset, Tool, AnthropicTool, OpenAITool, Render, Briefing, IsAgentRecoverable
    example_test.go docs_test.go and behaviour tests
  internal/                   HELPERS — never documented, never importable by users
    client/                   GENERATED (moved in step 0) — never edit; later also the user-management client
    descriptor.go             package internal: //go:embed client/SDK_PROVENANCE.yaml → var Descriptor []byte
    config/                   shared: Input → Config (token, host/port/target, TLS, timeout), credential redaction
    transport/                shared: Dial(cfg, logger, userAgent, services...), auth interceptor, service-config JSON from each service's retry list, Health(ctx)
    fault/                    shared: neutral *fault.Error (kind, code, detail, sunset/exhaustion kind, cause); FromRPC(ctx, err); canonical code names
    logging/                  shared: the dedicated slog logger (stderr handler, LevelVar, MEMCO_LOG), Parse/SetLevel, Named(logger, area)
    warn/                     shared: once-per-process warnings (deprecation notice, legacy env var) through a given logger
    provenance/               shared: strict SDK_PROVENANCE.yaml parser + cached Read()
    memory/                   memory service only, on memoryv1 wire types:
      service.go                service name + retryable methods (for transport)
      limits.go validate.go requests.go   Known caps; checks; per-RPC Finalize (validate, trim, sendable); import grouping
      wire.go                   Day(s), enum folding, RequireMemory
      agent.go                  operation table, JSON-schema building, argument coercion, text joining
      toolcopy.go               ToolCopy / ParameterCopy types
      toolcopy_gen.go           GENERATED by scripts/sync_tool_docs.py
    testserver/               in-process fake gRPC server (tests in both modules)
  examples/                   NESTED module (own go.mod; replace github.com/memcoai/memcoai/go => ../)
    README.md quickstart/ search_and_rate/ contribute/ import_memories/
    handling_errors/ concurrent_searches/ anthropic_agent/   (each: main.go + main_test.go)
  systemtest/                 //go:build systemtest — live suite
```

Import direction (no cycles): `memcoai` → `internal/{config,transport,fault,logging,warn,provenance,memory}` → `internal/client/...`, `internal` (descriptor). No internal package imports `memcoai`. Internal packages never construct public types: they return wire messages, plain values, or `*fault.Error`, which `memcoai/errors.go` maps to the concrete public type in one unexported function (`public(err error) error`) used on every return path.

| Go location | Node counterpart | Python counterpart |
|---|---|---|
| `memcoai/client.go` | `src/client.ts` | `__init__.py`, `_sync.py` |
| `memcoai/errors.go` + `internal/fault` | `src/errors.ts`, `internal/wire.ts` | `errors.py` |
| `memcoai/memory.go`, `session.go` | `src/operations.ts` | `operations.py` |
| `memcoai/types.go`, `convert.go` | `src/types.ts`, `internal/convert.ts` | `types.py`, `_convert.py` |
| `memcoai/agent.go` + `internal/memory/agent.go` | `src/agent.ts` | `agent.py` |
| `internal/config` | `src/internal/config.ts` | `_config.py` |
| `internal/transport` | `src/internal/channel.ts`, `auth.ts` | `_channel.py`, `_auth.py` |
| `internal/logging` | `src/internal/logging.ts` | `_logging.py` |
| `internal/warn` | `src/internal/deprecation.ts` | `_deprecation.py` |
| `internal/provenance` | `src/internal/provenance.ts` | `_provenance.py` |
| `internal/memory/limits.go` | `src/internal/limits.ts` | `_limits.py` |
| `internal/memory/validate.go`, `requests.go` | `src/internal/validate.ts`, `requests.ts` | `_validate.py`, `_requests.py` |
| `internal/memory/toolcopy_gen.go` | `src/gen/toolCopy.ts` | docstrings in `operations.py`/`types.py` |

How a memory call flows (e.g. `MemoryOperations.CreateMemory`): copy the public params into a `*memoryv1.CreateMemoryRequest` (plain field copy, slices copied) → `memory.FinalizeCreate(req, known.Snapshot(domain))` (all checks in Node's order, trims, `sendable`) → `transport` call through `Client.call` (ctx, default timeout, in-flight accounting, `fault.FromRPC`) → `toWriteResult(resp)` in `convert.go` → `public(err)` on every error path.

### Doc comment convention (applies to `go/memcoai` only)

Every exported function and method with parameters documents them in the Python-docstring shape (Args / Returns / Raises / Example), written as Go doc comments:

```go
// CreateMemory saves new knowledge to a memory domain. The write is accepted
// asynchronously: the result names the operation, not the memory it becomes.
//
// Parameters:
//   - ctx: bounds the call. Without a deadline the client's [Options.Timeout]
//     applies; cancelling it abandons the call, and nothing is sent if it has
//     already ended.
//   - p: what to save. Query, Title and Content are required; Domain or
//     SessionID names where it goes; Tags beyond the domain's cap are trimmed.
//
// It returns a [WriteResult] whose OperationID undoes the write through
// [MemoryOperations.RevertMemory], or is empty when the service could not mint
// one.
//
// Errors: [*InvalidRequestError] when a field is blank, too long, or names no
// domain (nothing is sent); [*PermissionError] when the credential may not
// write there; [*ResourceExhaustedError] when a quota or rate limit is hit;
// [*ConfigError] when the client is closed. See [Error] for the rest.
func (m *MemoryOperations) CreateMemory(ctx context.Context, p CreateMemoryParams) (*WriteResult, error)
```

plus `func ExampleMemoryOperations_CreateMemory()` in `example_test.go` (compiled, no `// Output:`; doc2go, gomarkdoc and pkg.go.dev render it under the method). Exported types and every exported struct field get a doc comment; `Error()`, `Unwrap()`, `String()` get one line. Internal packages: a one-line comment where the name is not self-explanatory, otherwise none; each internal package still carries a one-line package comment (staticcheck ST1000). golangci-lint enables no "exported identifier must be documented" rule, so nothing demands more there. `docs_test.go` enforces the public rules (see Tests).

`go/go.mod`: `go 1.25.0` (equals `requires.go.go` in the descriptor), `require google.golang.org/grpc v1.83.1`, `google.golang.org/protobuf v1.36.12`, `google.golang.org/genproto/googleapis/rpc v0.0.0-20260526163538-3dc84a4a5aaa`. Produce `go.sum` with `cd go && GOPROXY=off GOFLAGS=-mod=mod go mod tidy` (all in cache).

`go/examples/go.mod`: `module github.com/memcoai/memcoai/go/examples`, `go 1.25.0`, `require github.com/memcoai/memcoai/go v0.0.0`, `replace github.com/memcoai/memcoai/go => ../`, `require github.com/anthropics/anthropic-sdk-go v1.73.0` (needs network for `go mod tidy`).

---

## Public API

```go
package memcoai

const Version = "0.1.0"                       // user agent "memco-go/"+Version; release gate compares the tag to it
const DefaultHost = "grpc.memco.ai"
const DefaultPort = 443
const DefaultTimeout = 30 * time.Second
const LogEnv = "MEMCO_LOG"
const NewMemory = "new"
const DeprecationWarningName = "MemcoDeprecationWarning"

// Zero value = unset. Whitespace-only Token/Host is refused as "blank".
type Options struct {
    Token     string        // "" → MEMCO_API_TOKEN → MEMCO_API_KEY (warns once)
    Host      string        // "" → MEMCO_API_HOST → DefaultHost; "host[:port]", "[v6]:port", bare v6
    Plaintext bool          // TLS (system roots) unless true
    Timeout   time.Duration // 0 → DefaultTimeout; < 0 → ConfigError
    LogLevel  string        // "" → untouched; else SetLevel first (process-wide, like MEMCO_LOG)
    Logger    *slog.Logger  // nil → the SDK's dedicated logger; else every record of this client goes here
}

func NewClient(opts Options) (*Client, error)   // resolve + grpc.NewClient; sends nothing
type Client struct { Memory *MemoryOperations /* unexported state */ }
func (c *Client) Connect(ctx context.Context) error   // health Check, then ListDomains
func (c *Client) Close(ctx context.Context) error     // waits for in-flight calls until ctx ends, then closes regardless; idempotent; typed error if cut short
func (c *Client) Provenance() (Provenance, error)
func ReadProvenance() (Provenance, error)             // cached; needs no client

func SetLevel(level string) error                     // level of the SDK's dedicated logger: critical|error|warning|info|debug|none

type MemoryOperations struct{ /* unexported */ }
func (m *MemoryOperations) ListDomains(ctx context.Context) (*DomainList, error)
func (m *MemoryOperations) ListTools(ctx context.Context) ([]ToolDescriptor, error)
func (m *MemoryOperations) StartSession(ctx context.Context, domain string) (*Session, error)
func (m *MemoryOperations) Search(ctx context.Context, query string, p SearchParams) (*SearchResult, error)
func (m *MemoryOperations) GetMemory(ctx context.Context, idx string) (*Memory, error)
func (m *MemoryOperations) CreateMemory(ctx context.Context, p CreateMemoryParams) (*WriteResult, error)
func (m *MemoryOperations) EnrichMemory(ctx context.Context, p EnrichMemoryParams) (*WriteResult, error)
func (m *MemoryOperations) ShareFeedback(ctx context.Context, sessionID string, feedback []FeedbackRating) (*FeedbackResult, error)
func (m *MemoryOperations) RevertMemory(ctx context.Context, operationID string) (*RevertResult, error)
func (m *MemoryOperations) ImportMemories(ctx context.Context, memories []ImportedMemory, p ImportMemoriesParams) (*ImportResult, error)

type SearchParams struct { Domain, SessionID string; Tags []Tag }
type CreateMemoryParams struct { Query, Title, Content string; Domain, SessionID string; Tags []Tag; Source DataSource }
type EnrichMemoryParams struct { MemoryIdx, SessionID, Title, Content string; Tags []Tag; Sources []string; Source DataSource }
type ImportMemoriesParams struct { Domain, SessionID string }

type Session struct { ID string; Instructions Instructions /* + unexported ops, catalog []ToolDescriptor, catalogKnown bool */ }
func (s *Session) Search(ctx context.Context, query string, p ScopedSearchParams) (*SearchResult, error)
func (s *Session) GetMemory(ctx context.Context, idx string) (*Memory, error)      // feedback rebound to s.ID
func (s *Session) CreateMemory(ctx context.Context, p ScopedCreateMemoryParams) (*WriteResult, error)
func (s *Session) EnrichMemory(ctx context.Context, p ScopedEnrichMemoryParams) (*WriteResult, error)
func (s *Session) ShareFeedback(ctx context.Context, feedback []FeedbackRating) (*FeedbackResult, error)
func (s *Session) RevertMemory(ctx context.Context, operationID string) (*RevertResult, error)
func (s *Session) ImportMemories(ctx context.Context, memories []ImportedMemory) (*ImportResult, error)
func (s *Session) Tools() *Toolset
type ScopedSearchParams struct { Tags []Tag }
type ScopedCreateMemoryParams struct { Query, Title, Content string; Tags []Tag; Source DataSource }
type ScopedEnrichMemoryParams struct { MemoryIdx, Title, Content string; Tags []Tag; Sources []string; Source DataSource }
```

Result/input types (`types.go`), mirroring `nodejs/src/types.ts` field for field (Go names: `Idx`, `SessionID`, `OperationID`, `SHA256`, `ServerCommit`):
`Tag{Type, Value, Version string}` · `Instructions{Content, Policy, Adding, Rating, Next}` · `DomainEntry{Slug, Title, Summary, WhenToSearch, WhenToSave, WhatNotToSave, TagsDescription string; FilterTagTypes, VersionTagTypes []string; MaxTagsPerQuery int}` · `Limits{MaxQueryCharacters, MaxTextCharacters, MaxIdxCharacters, MaxSources, MaxFeedbackEntries, MaxImportMemories, MaxImportQueriesPerMemory, MaxImportInsightsPerMemory, MaxImportTagsPerMemory int}` · `DomainList{Domains []DomainEntry; Instructions; Limits *Limits; Deprecated bool; DeprecationMessage, SunsetDate, ServerCommit string}` · `ToolDescriptor{Name, Description string; Available bool}` · `Insight{Idx, Title, Content, Updated string; TimesServed, Endorsed, Disputed int64}` · `Memory{Idx, Kind string; TimesServed int64; Intents []string; Insights []Insight; Reference string /* + unexported ops, sessionID */}` with `func (m Memory) Feedback(ctx, MemoryFeedback) (*FeedbackEntry, error)` · `MemoryFeedback{Relevant, Correct bool; Comment string}` · `SearchResult{SessionID string; Memories []Memory; Notice string; Instructions}` · `WriteResult{OperationID string; Instructions}` · `FeedbackRating{Idx string; Relevant, Correct bool; Comment string}` · `FeedbackEntry{Idx string; Relevant, Correct bool; Advice string}` · `FeedbackResult{SessionID string; Entries []FeedbackEntry; Instructions}` · `RevertResult{OperationID string; Outcome RevertOutcome; Instructions}` · `ImportedInsight{Title, Content string}` · `ImportedMemory{Queries []string; Insights []ImportedInsight; Tags []Tag}` · `ImportOutcome{Index int; Status ImportStatus; Errors []string}` · `ImportResult{Results []ImportOutcome; Instructions}` · `ProtoRecord{Path, SHA256 string}` · `Provenance{ServerCommit string; Protos []ProtoRecord}`.
Enums are `int32` types with `String()` returning the unprefixed name (`MEMORY_REMOVED`): `DataSource` (Unspecified 0, User 1, Agent 2), `RevertOutcome` (Unspecified 0, MemoryRemoved 1, AdditionRemoved 2, EntryRemoved 3, Merged 4, NotFound 5, Expired 6, Refused 7), `ImportStatus` (Unspecified 0, Queued 1, Rejected 2, Error 3, Duplicate 4). Constants are prefixed: `DataSourceAgent`, `RevertOutcomeMerged`, `ImportStatusQueued`, …

Errors (`errors.go`):

```go
type Error interface{ error; memcoError() }                 // everything the SDK returns
type ConfigError struct{ Message string }                   // Error() == Message
type APIError struct{ Code codes.Code; Detail string; cause error }
func (e *APIError) Error() string    // codeName(e.Code) + ": " + e.Detail   (canonical names)
func (e *APIError) Unwrap() error    // cause: ctx.Err() only when the call's own context ended it
type AuthenticationError     struct{ APIError }                                    // UNAUTHENTICATED
type PermissionError         struct{ APIError }                                    // PERMISSION_DENIED
type InvalidRequestError     struct{ APIError }                                    // INVALID_ARGUMENT + local validation
type NotFoundError           struct{ APIError }                                    // NOT_FOUND
type PreconditionFailedError struct{ APIError }                                    // FAILED_PRECONDITION
type SunsetError             struct{ PreconditionFailedError; Kind SunsetKind }
type ResourceExhaustedError  struct{ APIError; Kind ResourceExhaustedKind }        // RESOURCE_EXHAUSTED
type UnavailableError        struct{ APIError }                                    // UNAVAILABLE
type UnhealthyError          struct{ UnavailableError }                            // health probe only
type TimeoutError            struct{ APIError }                                    // DEADLINE_EXCEEDED
type InternalError           struct{ APIError }                                    // everything else (CANCELLED, UNKNOWN, >16 → UNKNOWN)
type SunsetKind string            // SunsetClientVersion "client_version", SunsetAPIVersion "api_version"
type ResourceExhaustedKind string // ResourceExhaustedRateLimit "rate_limit", ResourceExhaustedQuota "quota", ResourceExhaustedUnknown "unknown"
```

**Every subtype declares its own `Unwrap()`** returning a pointer to its embedded parent (`&e.APIError`, `&e.PreconditionFailedError`, `&e.UnavailableError`); otherwise the promoted `(*APIError).Unwrap` skips a level. Chain: `*SunsetError → *PreconditionFailedError → *APIError → ctx error (if any)`. `ConfigError` and `APIError` each implement `memcoError()`. Document: use `errors.As`, test the subtype (Sunset, Unhealthy) before its parent; a type switch does not see parents. No exported `fromServiceError` (the generated client is internal). No `GRPCStatus()`. `errors.go` also holds the one unexported mapping `public(err error) error`: `*fault.Error` → the concrete type above (by kind/code, carrying `Detail`, `Kind`, `cause`); anything already public passes through; any other error becomes `*InternalError` (`Code: codes.Unknown`).

Agent (`agent.go`):

```go
type JSONSchema = map[string]any
type ToolParameters struct { Type string `json:"type"`; Properties map[string]JSONSchema `json:"properties"`; Required []string `json:"required"` } // Required never nil
type Tool struct { Name, Description string; Parameters ToolParameters /* + unexported invoke */ }
func (t Tool) Call(ctx context.Context, args json.RawMessage) (string, error)
type Toolset struct{ Tools []Tool }
func (s *Toolset) Call(ctx context.Context, name string, args json.RawMessage) (string, error)
func (s *Toolset) ToAnthropic() []AnthropicTool   // {name, description, input_schema}
func (s *Toolset) ToOpenAI() []OpenAITool         // {type:"function", function:{name, description, parameters}}
type AnthropicTool struct{ Name string `json:"name"`; Description string `json:"description"`; InputSchema ToolParameters `json:"input_schema"` }
type OpenAITool struct{ Type string `json:"type"`; Function OpenAIFunction `json:"function"` }
type OpenAIFunction struct{ Name string `json:"name"`; Description string `json:"description"`; Parameters ToolParameters `json:"parameters"` }
type Rendered interface{ render() string }        // sealed: SearchResult, Memory, WriteResult, FeedbackResult, RevertResult (value receivers)
func Render(r Rendered) string
func Briefing(domain DomainEntry, instructions Instructions) string
func IsAgentRecoverable(err error) bool           // errors.As InvalidRequestError or NotFoundError
```

---

## Behaviour spec

The source of truth for every rule and message is the Node SDK; read the named Node file before implementing each Go location. Exact strings below are the ones tests assert. Each heading names where the Go code lives.

### `internal/config` (Node `src/internal/config.ts`)
`config.Resolve(in Input, getenv func(string) string, log *slog.Logger) (*Config, error)`; `Input{Token, Host string; Plaintext bool; Timeout time.Duration}` is copied from `memcoai.Options` by `NewClient`. Errors are `fault.Config(msg)`.
Order: timeout (`timeout must be positive, got <d>` — Go prints the `time.Duration`, e.g. `-1s`) → token → token sendability → host.
- Token: explicit non-blank → trimmed; whitespace-only → `the token passed to the client is blank: pass a real token or set MEMCO_API_TOKEN`; else trimmed `MEMCO_API_TOKEN`; else trimmed `MEMCO_API_KEY` + `warn.Legacy` (once per process: `MEMCO_API_KEY is deprecated and will be removed in a future release; rename it to MEMCO_API_TOKEN.`); else `no API token: pass token=... or set MEMCO_API_TOKEN` (verbatim, for parity). Debug records `credential taken from the token argument` / `credential taken from MEMCO_API_TOKEN|MEMCO_API_KEY`.
- Sendability: every rune in `0x20..0x7e`, else `the token cannot be sent as an HTTP header: it must be printable ASCII, and the character at index N is not` (N counts runes; never print the value).
- Host: whitespace-only explicit → `the host passed to the client is blank: pass a real host or set MEMCO_API_HOST`; resolved = explicit, else non-blank env, else default; trimmed; then split. Messages (`%q` quoting): `host %q opens a bracket that is never closed`, `host %q has no address inside its brackets`, `host %q has unexpected text after the closing bracket`, `host %q has a port but no hostname`, `host %q has an unparseable port: %q is not an integer` (port text trimmed then `^[+-]?[0-9]+$`; the *untrimmed* text is quoted), `host %q has a port outside the range 1-65535: N` (parse with `math/big` so huge values echo exactly). Two or more colons without brackets → bare IPv6, port 443. `Config.Target()` is `"[h]:p"` if h contains `:`, else `"h:p"`; `Config.DialTarget()` is `(&url.URL{Scheme: "dns", Path: "/" + Target()}).String()`. Debug `endpoint` record with attrs `target`, `tls`, `source` (`the host argument` / `MEMCO_API_HOST` / `the default`).
- `Credential` holds the token and implements `String`, `GoString`, `Format`, `LogValue` → `[redacted]`; `Config` and `memcoai.Client` hold it only by pointer; only `transport` reads the value.

### `internal/transport` (Node `src/internal/channel.ts`, `auth.ts`)
- `type Service struct{ Name string; Retryable []string }` — each service package declares its own (`internal/memory/service.go`: `memcoai.memory.v1.MemoryService`, `ListDomains`, `GetMemory`, `ListTools`, with the "never retry Search/StartSession/writes/import" rationale copied from `channel.ts:60-103`). The user-management service will add another.
- `Dial(cfg *config.Config, userAgent string, services ...Service) (*grpc.ClientConn, error)` with: `WithTransportCredentials(credentials.NewTLS(&tls.Config{}))` or `insecure.NewCredentials()` when `Plaintext`; `WithUserAgent(userAgent)` (`"memco-go/"+memcoai.Version`, passed in because the version constant is public); `WithUnaryInterceptor(auth)`; `WithDisableServiceConfig()`; `WithDefaultServiceConfig(ServiceConfig(services...))`; `WithConnectParams(...)` (facts above). No keepalive, size limits or compression. A `grpc.NewClient` error → `fault.Config`.
- `ServiceConfig` JSON: one `methodConfig` whose `name` lists every service's retryable methods then `{service:"grpc.health.v1.Health",method:"Check"}`, with `retryPolicy {"maxAttempts":3,"initialBackoff":"0.1s","maxBackoff":"1s","backoffMultiplier":2,"retryableStatusCodes":["UNAVAILABLE"]}`.
- Auth interceptor: copy `metadata.FromOutgoingContext`, `Set("authorization", "Bearer "+token)`; for methods with prefix `/grpc.health.v1.` **delete** `authorization` instead.
- `Health(ctx, conn) (grpc_health_v1.HealthCheckResponse_ServingStatus, error)` with `Service: ""`; `StatusName(s)` from `HealthCheckResponse_ServingStatus_name`, else `UNRECOGNIZED(n)`.

### `internal/fault` (Node `src/errors.ts`, `internal/wire.ts`)
- `type Error struct{ Kind Kind; Code codes.Code; Detail string; Sunset string; Exhaustion string; cause error }` with `Kind` ∈ {`Config`, `API`, `Unhealthy`}; constructors `Config(msg)`, `Invalid(detail)`, `NotFound(detail)`, `Internal(detail)`, `Unhealthy(detail)`; `Error()`/`Unwrap()` so internal code can be tested on it directly.
- `FromRPC(ctx, err) *Error`: already `*Error` → as is; find the inner status with `errors.As(err, &interface{ GRPCStatus() *status.Status })` (exhausted retries wrap it); code > 16 → `Unknown`; `FAILED_PRECONDITION` scans `status.Details()` for `*errdetails.ErrorInfo` with `Domain == "memco.ai"` and reason `CLIENT_VERSION_SUNSET` → `client_version` / `API_VERSION_SUNSET` → `api_version` (keep scanning past unknown reasons); `RESOURCE_EXHAUSTED` kind from the lower-cased detail: any of `daily weekly monthly quota` → `quota`; else any of `rate limit`, `per-minute`, `per minute`, `per-second`, `too many requests` → `rate_limit`; else `unknown`; `cause = ctx.Err()` only when `ctx.Err() != nil` and the code is `Canceled` or `DeadlineExceeded`; a non-status error → `API`/`Unknown` with `Detail = err.Error()`.
- `FromContext(ctx) *Error` = `FromRPC(ctx, status.FromContextError(ctx.Err()).Err())` — the one way a context ending becomes an error anywhere in the SDK.
- `CodeName(code)`: canonical names (`INVALID_ARGUMENT`, `CANCELLED`, …, `UNRECOGNIZED(n)`).
- `memcoai.public` maps: `Config` → `*ConfigError`; `Unhealthy` → `*UnhealthyError` (code UNAVAILABLE); `API` by code → table in "Public API" (`FAILED_PRECONDITION` with `Sunset != ""` → `*SunsetError`).

### `memcoai/client.go` (Node `src/client.ts`)
- `NewClient`: `LogLevel` → `logging.SetLevel` first (bad value → `*ConfigError`); logger = `Options.Logger` or `logging.Default()`; `config.Resolve`; `transport.Dial(cfg, "memco-go/"+Version, memory.Service)`; build `Memory`. Sends nothing.
- In-flight accounting: `begin()` under a mutex — closed → `ConfigError("this client is closed; create a new one to make more calls")`; else `inflight.Add(1)`.
- Every RPC (health included) goes through one unexported `call`: begin → `ctx.Err() != nil` → `fault.FromContext(ctx)` **without sending** → if `ctx` has no deadline, `context.WithTimeout(ctx, cfg.Timeout)` (a caller's deadline **replaces** the default, shorter or longer) → invoke → on success a debug record `rpc ok` (attrs `rpc`, `elapsed_ms`); on failure **no record** — the error is returned as `fault.FromRPC(...)` → end.
- `Connect(ctx)`: `transport.Health`; not SERVING → `fault.Unhealthy("<target> reported health status <NAME>")`; then `Memory.ListDomains(ctx)`. Any failure is returned as is — not logged (Node/Python log `credential rejected`; Go does not log an error it returns). The channel is kept open so `Connect` can be retried. Success → info `connected` (attrs `target`, `tls`).
- `Close(ctx)`: first caller runs the shutdown (`sync.Once` + a `done` channel all callers wait on, each bounded by its own ctx): set closed under the lock; wait for `inflight` **or** `ctx.Done()`; then `conn.Close()` regardless (in-flight calls then end with typed errors); info `closed connection` (attr `target`). Returns `nil` after a full drain; when it stopped waiting, `fault.FromContext(ctx)` → `*TimeoutError` (deadline) or `*InternalError` with code `CANCELLED` (cancel), both unwrapping to `ctx.Err()`; a `conn.Close()` failure → `*InternalError`. Later calls return `nil` once shutdown completed.

### `internal/memory` + `memcoai/memory.go`, `session.go`, `convert.go` (Node `src/operations.ts`, `internal/requests.ts`, `validate.ts`, `limits.ts`, `convert.ts`)
Port rule-for-rule in the check order of `nodejs/src/internal/requests.ts` (the per-RPC table is in that file). `internal/memory` works only on `*memoryv1.*` messages; `memcoai` copies public params into them first (fresh slices — never the caller's), and converts responses back in `convert.go`. Go specifics:
- Blank = `strings.TrimSpace(v) == ""`. "Given" for scope = `v != ""`; so `Domain: "  "` → `domain must not be empty`, `Domain: ""` → absent. Scope message: `pass a domain or a session_id: a request needs one of them to name a domain`.
- Messages (all `fault.Invalid`, surfacing as `*InvalidRequestError` with `Code: codes.InvalidArgument`): `<field> must not be empty`; `<field> is <n> characters, which exceeds the limit of <cap>` (`utf8.RuneCountInString`); `<field> has <n> entries, which exceeds the limit of <cap>`; `<field> are <n> characters together, which exceeds the combined limit of <cap>` (default field `title and content`; import `memories[i] insights[j] title and content`); `feedback must contain at least one rating`; `feedback idx must not be empty`; `sources entry must not be empty`; `tag type/value must not be empty` (import: `memories[i] tag type …`); `memories must contain at least one memory`; `memories[i] must contain at least one query|insight`; `memories[i] queries[j] must not be empty`; `memories[i] insights[j] title|content must not be empty`; fields `operation_id`, `memory_idx` (exempt when exactly `new`), `idx`, `session_id`, `domain`, `query`, `title`, `content`.
- Cap 0 = unreported = no check. Refuse: query chars, text chars (title+content together), idx chars (`idx`, `memory_idx` — also for `new`, `operation_id`, `sources entry`, `feedback idx`), feedback entries, 3 per-import-entry counts (tag count checked on untrimmed tags). Trim (debug record `trimmed` with attrs `field`, `from`, `to`): `sources` to MaxSources; `tags` to the named domain's MaxTagsPerQuery (session-only scope → cap 0 → no trim; enrich never trims tags).
- `sendable(msg proto.Message)`: after all other checks, `proto.Marshal`; failure → `a field value cannot be sent: <err>`. Blank-tag errors win over it.
- `memory.Known` (one per `MemoryOperations`, `sync.RWMutex`): `Update(*memoryv1.Limits, []*memoryv1.DomainEntry)` stores a clone; nil never clears; tag caps merged per slug (never removed); `Snapshot(domain)` gives each builder one consistent view.
- Tag `Version ""` / rating `Comment ""` → field left nil on the wire (proto3 optional). `Source` zero → sent as AGENT (2).
- `ListDomains`: convert, `known.Update`, `warn.Deprecation(logger, DeprecationMessage, SunsetDate)`.
- `StartSession`: validate the domain (nothing sent if blank) → ListTools in a goroutine on a derived, cancellable context, StartSession in the caller; StartSession error → cancel, wait, return it; ListTools API-kind failure → catalog unknown (all tools offered) + warning `listTools failed while opening a session; treating every tool as available` (attr `error`); other failure → returned. No goroutine outlives the call.
- `GetMemory`: absent `memory` → `fault.NotFound` detail `no memory was returned for "<idx>"` (`%q`). The returned memory is bound to session `""` → its `Feedback` fails `session_id must not be empty` with zero calls.
- `ImportMemories`: scope check → groups of `MaxImportMemories` (0 = one group) → before each group, stop if `ctx.Err() != nil` (typed context error, no further calls) → validate the group (absolute `memories[i]`) → per-entry caps → send → next group; sequential; results renumbered `offset + index`; instructions from the first response; empty → `memories must contain at least one memory`. Each group call gets its own default timeout unless `ctx` carries a deadline (which then bounds the whole import).
- `RevertMemory`: wire field `OpId`; NOT_FOUND/EXPIRED/REFUSED are outcomes.
- `Memory.Feedback(ctx, MemoryFeedback)`: zero-value (hand-built) memory → `*InvalidRequestError` `this memory has no session to record feedback against`; else ShareFeedback with one rating; empty entries → `*InternalError` `the service recorded no feedback entry for "<idx>"`.
- Conversion (`convert.go`, using `memory.Day`, `memory.Fold*`): `""` stays `""` (Go has no null); `Limits` nil iff absent; dates: `time.Parse(time.DateOnly, s)` and `Year() >= 1`, keep the original string, else `""`; unknown enum values fold to Unspecified; repeated fields copied; nil `Instructions` → empty parts.

### `internal/warn` (Node `src/internal/deprecation.ts`)
- `Deprecation(log *slog.Logger, msg, sunset string)`: empty or already seen this process → nothing; else a **Warn** record through the given logger: message = `msg + " (stops working on " + sunset + ")"` when sunset is non-empty, else `msg`; attr `warning=MemcoDeprecationWarning`.
- `Legacy(log)`: once per process, Warn `MEMCO_API_KEY is deprecated and will be removed in a future release; rename it to MEMCO_API_TOKEN.` (attr `warning=DeprecationWarning`).
- Mutex-guarded seen-set; `Reset()` for tests. Never fatal. Because these go through the SDK logger, `MEMCO_LOG=none` (or a discarding `Options.Logger`) silences them — the developer's explicit choice.

### `internal/logging` (Node `src/internal/logging.ts`)
- `Default() *slog.Logger`: the SDK's own logger — `slog.NewTextHandler` over a writer that looks up `os.Stderr` on every write (write errors ignored), with `HandlerOptions{Level: levelVar, ReplaceAttr: …}` printing level 12 as `CRITICAL` and `WARN` as `WARNING`. Never touches `slog.Default()`.
- `Named(log, area) *slog.Logger` → `log.With("logger", "memco."+area)`; areas `client`, `config`, `memory`.
- Levels: `critical` = `slog.Level(12)`, `error`, `warning`, `info` (default), `debug`, `none` (above everything). `Parse(s)` trims and lower-cases; `SetLevel(s) error` → `fault.Config` `%q is not a log level; use one of critical, debug, error, info, warning, or none to silence`.
- `init()` reads `MEMCO_LOG` (empty = default); an invalid value leaves `info` and emits one Warn record `MEMCO_LOG ignored: <msg>` through `Default()`.
- What is logged: only events the SDK **handles** or reports as information — `connected`, `closed connection`, `rpc ok` (debug), `credential taken from …`/`endpoint` (debug), `trimmed` (debug), the ListTools fallback (warn, the failure is handled), `MEMCO_LOG ignored` (warn, handled by falling back), deprecation/legacy notices (warn). Never an error that is also returned.
- A custom `Options.Logger` is used as given (its handler decides levels); `LogLevel`/`SetLevel`/`MEMCO_LOG` govern only the dedicated logger. Records use `slog` attributes, not formatted strings; no record ever contains the token (the `Credential` `LogValue` guards accidental use).

### `internal/provenance` (Node `src/internal/provenance.ts`)
Port the strict subset parser (same error strings: `SDK_PROVENANCE.yaml is missing server_commit`, `… lists no protos`, `… has an empty protos entry`, `… has a protos entry missing path or sha256: <fields>`, `… has a protos entry naming <key> twice`, `… uses an unsupported block scalar for <where>`) as `Parse([]byte) (Record, error)`; `Read()` parses `internal.Descriptor` once (`sync.OnceValues`). `memcoai.ReadProvenance` converts `Record` to the public `Provenance` (clones).

### `internal/memory/agent.go` + `memcoai/agent.go` (Node `src/agent.ts`, `src/gen/toolCopy.ts`)
- From `internal/memory/toolcopy_gen.go` (generated): `ToolPrefix = "memco_"`, `OfferedTools` (`search, get_memory, create_memory, enrich_memory, share_feedback, revert_memory`), `AnsweredTools` (`list_domains, start_session`), `SDKShapedParameters` (`feedback, source, tags`), `ToolCopies []ToolCopy`, `NestedCopy []ParameterCopy`. Hand-written in `internal/memory/toolcopy.go`: `type ToolCopy struct{ Name, Description string; Parameters []ParameterCopy }` and `type ParameterCopy struct{ Name, Description string }` (**exactly this field order** — the generated literals are positional).
- `internal/memory/agent.go`: the operation table (names, snake_case arguments `memory_idx`/`operation_id`, shapes, required), bound arguments never shown (`domain`, `session_id`, `source`), the `reshaped`/`tagFields`/`feedbackFields` copy ported from `agent.ts:331-420`, `Schema(op) map[string]any` (fresh per call), `Arguments(op, raw map[string]any) (map[string]any, error)` with Node's exact messages (`unknown argument(s): "a", "b"` sorted and `%q`-quoted; `missing required argument(s): x, y`; `<where> must be a string|true or false, not <jsonType>`; `<arg> must be a list, not <t>`; `<arg> must hold objects, not <t>`; `<arg> is missing f1, f2`; JSON type names `null array object string number boolean`; null optional = absent; undeclared keys inside objects dropped), `Decode(raw json.RawMessage)` (`arguments are not valid JSON: <msg>`, `arguments must be an object, not <t>`), `Available(catalog, known)`, `Joined(parts...)`.
- `memcoai/agent.go`: the public types, `Session.Tools()` (catalog unknown → all offered; else offered ∩ `Available`), the per-operation invoke glue calling the `Session` methods, `Toolset.Call` (unknown name: `no tool named %q; there is <sorted comma list>`), recoverable errors (`IsAgentRecoverable`) returned as `(text, nil)` — `invalid request: <Detail>` / `nothing found: <Detail>` — everything else returned as the error, `Render` and `Briefing` in the exact formats of `agent.ts:1150-1234` (booleans `true/false`; an empty revert operation id renders `null`; briefing line `Pass 'new' as memory_idx to memco_enrich_memory to open a new memory.`).

### Go adaptations (deliberate differences — document in `doc.go` and `go/README.md`)
Context replaces per-call `timeout`; `Close` takes a context and returns a typed error (unwrapping to the context's) if the drain was cut short; returned errors are never also logged (no `credential rejected` / `failed` records); `CANCELLED` stays `InternalError` but `errors.Is(err, context.Canceled)` works (only for the caller's own context); an already-ended context sends nothing; zero values mean unset (`""` cannot be sent as a tag version or comment); no `WithSession` (use `StartSession`); `StartSession` waits for its ListTools; the health probe counts as in flight; `Options.Logger` for slog interop; deprecation notices are Warn records on the SDK logger (silenced by `none`); log records carry slog attributes rather than Node's formatted text; no LangChain adapter (a `Tool` already has the name/description/JSON-call shape frameworks need); `strings.TrimSpace` whitespace set.

---

## Tooling

### `scripts/sync_tool_docs.py` — Go target
- `GO_COPY = ROOT / "go" / "internal" / "memory" / "toolcopy_gen.go"` (an internal helper, like Python's private tables; the public package never exposes it); add `(GO_COPY, lambda _current, published: go_tool_copy_module(published))` to `targets`.
- New helpers (under `# -- the Go target --`), reusing `substituted`, `node_spelling`, `sdk_shaped`, `checked(template=True)`, `KEY`, `CARRIED`, `EXCLUDED`:
  - `go_parameter(key)` → `PARAMETER_ALIASES.get(key, key)` (aliased, **not** camelised).
  - `go_string(name)` → `KEY.match` else `Drift("…a key cannot spell…")`; returns `f'"{name}"'`.
  - `go_raw(text, where)` → `checked(text, where, template=True)`, then refuse `"\r"`, `"\x00"`, `"\ufeff"`; returns `` f"`{text}`" ``.
  - `go_lines(text, indent)` (indent only line 0), `go_pair(name, copy, indent)` → `{"name", `copy`},`, `go_strings(values)` → `[]string{"a", "b"}`, `go_table(declaration, lines)` (`T{}` when empty), `go_tool_entry(name, tool)`, `go_tool_copy_module(tools)`.
  - Extract the unnamed-field guard of `tool_entry` into `refuse_unnamed(name, tool)`, called by both emitters (message unchanged: "neither writes nor names").
- Emitted shape (positional literals and raw strings only; every declaration a separate top-level `var` → gofmt has nothing to align). Comments stay minimal — this is internal code:

```go
// Code generated by scripts/sync_tool_docs.py. DO NOT EDIT.

package memory

// <short free-floating comment: generated from the tool manifest with markers
// resolved; left behind: rpc, annotations, title, instructions, the
// SDK-shaped parameters>

// ToolPrefix is prepended to each offered operation's name.
const ToolPrefix = "memco_"

var OfferedTools = []string{"search", "get_memory", "create_memory", "enrich_memory", "share_feedback", "revert_memory"}

var AnsweredTools = []string{"list_domains", "start_session"}

var SDKShapedParameters = []string{"feedback", "source", "tags"}

// ToolCopies holds every tool the manifest publishes, in its order.
var ToolCopies = []ToolCopy{
	{
		"revert_memory",
		`Undo a memory you just wrote, using the operation id that memco_create_memory or memco_enrich_memory returned.
…`,
		[]ParameterCopy{
			{"operation_id", `(Required) The operation id returned by …`},
		},
	},
	{
		"list_domains",
		`…`,
		nil,
	},
}

// NestedCopy holds import_memories' nested request fields.
var NestedCopy = []ParameterCopy{
	{"memories[].insights", `(Required) …`},
}
```
- Update the module docstring ("four targets, in three languages"; Go keeps no doc comments at run time either), the `node_spelling`/`CARRIED`/`KEY` docstrings ("Node and Go"), `checked`'s backtick message ("ends the template literal or raw string it is written into"), and the comment above `targets`.
- `python/tests/test_tool_copy.py` — new `# -- the Go target --` block (use existing `manifest`/`published` fixtures): `test_the_go_target_keys_parameters_as_the_request_message_does`, `test_the_go_target_refuses_copy_a_raw_string_would_change` (backtick, trailing whitespace, carriage return, NUL), `test_the_go_target_refuses_a_name_a_string_cannot_spell`, `test_the_generated_go_file_announces_itself_as_generated`, `test_the_go_target_emits_the_tables_the_sdk_steers_by` (`const ToolPrefix = "memco_"`, `var OfferedTools = …`, `var AnsweredTools = …`, `var SDKShapedParameters = …`, `package memory`), `test_no_marker_reaches_the_copy_the_go_module_states` (after `var ToolCopies`), `test_the_go_target_carries_every_parameter_the_manifest_names` (pinned set = Node's `COMPARED` in snake_case), `test_the_go_target_leaves_out_the_parameters_this_sdk_shapes_differently`, `test_the_go_target_carries_the_nested_copy_by_its_manifest_path`, `test_the_go_target_refuses_a_marker_it_cannot_spell` / `…_cannot_parse` / `…_a_field_it_neither_writes_nor_names`, `test_the_committed_go_file_is_what_the_generator_writes`. Update the module docstring to name the Go files.

### `scripts/verify_provenance.py`
- Paths: `MANIFESTS[0] = "go/internal/client/tools/tools.json"`; descriptors = `sorted([*ROOT.glob("*/client/SDK_PROVENANCE.yaml"), *ROOT.glob("go/internal/client/SDK_PROVENANCE.yaml"), *ROOT.glob("python/memcoai/SDK_PROVENANCE.yaml")])`; update the comment at lines 27–28; constants `GO_ROOT`, `GO_GENERATED = GO_ROOT / "internal" / "client"`, `GO_MODULE_PATH = "github.com/memcoai/memcoai/go"`. Fix the docstring ("three-way" lists four items → "agreement", now six).
- Helpers: `VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")`; `requirements(go_mod) -> dict[str, str]` (only `require X v` lines and `require ( … )` blocks, `//` comments stripped); `at_least(have, want) -> bool` (numeric tuple compare, missing patch = 0; False if either is absent/prerelease/pseudo-version).
- Section 5 "go/go.mod requires at least what the Go descriptor declares": each missing prerequisite (`go/go.mod`, the Go descriptor, `memory.pb.go`) → `check(False, "<path> is present")`; else `module == GO_MODULE_PATH`; for `go`, `grpc`, `protobuf`: `at_least(have, scalar(block(desc, "requires", "go"), key))` with message `f"{key}: go.mod {have!r} >= descriptor {want!r}"`; the `// protoc-gen-go vX` stamp in `memory.pb.go` ≤ protobuf require; no `replace` directive in `go/go.mod`; `check(not (GO_ROOT / "client").exists(), "go/client is absent: the Go client is internal, and a copy there would publish it")`.
- Why `>=` (not `==` as for Python): a `require` is a floor under minimal version selection, the descriptor states the floor the generated code needs, and `go.mod` is hand-written (the repo already floors above the descriptor for Python ≥3.10 vs `>=3.9`, Node ≥22 vs `>=18`).

### `scripts/build_llms_txt.py` — Go reference
- `ANGLED = re.compile(r"\]\(<([^<>()\s]+)>\)")` and `unangled(markdown)` (prose lines only, never inside fences); call it first in `rendered()`: `source = unangled(body(read(...)))`.
- `GO_PACKAGE = "github.com/memcoai/memcoai/go/memcoai"`; `go_version(path)` reads `^const Version = "([^"\n]+)"$` (re.M) or `fail(...)`; `go_packages(markdown)` → every `*/index.md`, `index.md` first, rest sorted, `fail` if the root page is missing; `go_reference()` → `Reference(title=f"memcoai Go SDK {version}", summary=f"Go SDK for Memco Shared Memory. Install with `go get {GO_PACKAGE}`.", html=go/docs/_build/html, markdown=go/docs/_build/markdown, pages=(Page(title=heading(body(...)) or dir, group="Packages", href=f"{dir}index.html", source=s) …), markdown_anchors=True, rubric_level=0, rubrics=frozenset(), unlisted=frozenset({"_/"}))`; `LANGUAGES["go"] = go_reference`. Update the module docstring (generators, table of contents, usage).
- `python/tests/test_llms_txt.py`: `test_the_go_version_is_read_from_the_constant_the_tag_is_checked_against`, `test_the_go_markdown_tree_is_the_table_of_contents_root_package_first`, `test_a_go_reference_without_its_root_package_is_a_failure`, `test_an_angle_bracketed_destination_is_read_like_any_other` (incl. `<a b.md>`, `<f(1).md>` and fenced code left alone), `test_gomarkdocs_generated_banner_does_not_open_the_page`, a `go_ref` fixture (gomarkdoc-shaped `index.md` + `agent/index.md`, doc2go-shaped html incl. `_/css/main.css`) used by `test_the_go_reference_publishes_every_page_and_reaches_nothing_it_did_not` and `test_the_go_reference_lists_its_packages_under_one_group_root_first`, and `test_go_reference_reads_the_module_as_built` (monkeypatch `script.ROOT`).

### `go/.golangci.yml` (lint, format, static analysis)

```yaml
# golangci-lint v2 configuration for the Go SDK — both modules (golangci-lint
# finds this file by walking up from go/examples too). `make -C go lint` runs
# the linters and reports formatting drift; `make -C go format` applies the
# formatters. The version is pinned in go/Makefile, the only place it lives.
version: "2"

run:
  # The live suite is otherwise compiled by nothing hermetic.
  build-tags:
    - systemtest

linters:
  # errcheck, govet, ineffassign, staticcheck, unused.
  default: standard
  enable:
    - bidichk      # hostile Unicode in source (the SDK hands text to models)
    - errorlint    # errors.As/Is over == and type switches: the error hierarchy relies on it
    - misspell
    - nolintlint   # a suppression must name its linter and say why (CONTRIBUTING: "Suppressions need a reason")
  settings:
    staticcheck:
      # Everything, including the doc-comment form checks (ST1000, ST1020-ST1022)
      # that golangci-lint leaves off by default. Disable a check only with a
      # comment here naming why.
      checks: ["all"]
    nolintlint:
      require-explanation: true
      require-specific: true
  exclusions:
    # Only the export's tree is skipped. toolcopy_gen.go is generated here, by
    # scripts/sync_tool_docs.py, and is held to the same checks as any other
    # file — as Node's toolCopy.ts is to prettier and tsc. (Formatter findings
    # pass through this same exclusion step, so `generated` must be disabled
    # here too for `run` to report a mis-formatted generated file.)
    generated: disable
    paths:
      - internal/client/

formatters:
  enable:
    - gofmt
    - goimports
  settings:
    gofmt:
      simplify: true
    goimports:
      local-prefixes:
        - github.com/memcoai/memcoai/go
  exclusions:
    generated: disable
    paths:
      - internal/client/
```

Probed in a scratch module with golangci-lint 2.13.2: `config verify` exits 0; with both `generated: disable`, `run` reports gofmt drift in a `// Code generated … DO NOT EDIT.` file and skips `internal/client/`; `fmt --diff` exits 1 on drift. Suppression syntax everywhere: `//nolint:<linter> // <reason>`.

### `go/Makefile`

Header comments in the style of `nodejs/Makefile`; `.DEFAULT_GOAL := help`; same `help` recipe:

```make
GO ?= go
.NOTPARALLEL:
export GOWORK := off
# golangci-lint v2.13.2 declares go 1.26.0. Naming the toolchain exactly avoids
# GOTOOLCHAIN=+auto's proxy lookup (fails offline). CI, where setup-go installed
# 1.26 and exported GOTOOLCHAIN=local, passes LINT_TOOLCHAIN=local. Override
# GOLANGCI_LINT=golangci-lint to use an installed binary of the same version.
LINT_TOOLCHAIN ?= go1.26.8
GOLANGCI_LINT ?= GOTOOLCHAIN=$(LINT_TOOLCHAIN) $(GO) run github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.13.2
DOC2GO = $(GO) run go.abhg.dev/doc2go@v0.12.2
GOMARKDOC = $(GO) run github.com/princjef/gomarkdoc/cmd/gomarkdoc@v1.1.0
GO_TOOLCHAINS := go1.25.13 go1.26.8 go1.27.0
COVERAGE_FLOOR := 95
REVISION = $$(git rev-parse HEAD 2>/dev/null || echo main)
```

| Target | Recipe |
|---|---|
| `install` | `$(GO) mod download`; `cd examples && $(GO) mod download`; `$(GOLANGCI_LINT) version`; `$(DOC2GO) -version`; `$(GOMARKDOC) --version` |
| `lint` | `$(GOLANGCI_LINT) config verify`; `$(GOLANGCI_LINT) run ./...`; `cd examples && $(GOLANGCI_LINT) run ./...` (formatting drift is reported by `run` through the enabled formatters) |
| `format` | `$(GOLANGCI_LINT) fmt` (walks files; confirm it covers `examples/` — if not, repeat with `cd examples`) |
| `typecheck` | `$(GO) vet -tags systemtest ./...`; `cd examples && $(GO) vet ./...` |
| `test` | `$(GO) test -race ./...`; `cd examples && $(GO) test -race ./...` |
| `coverage` | `mkdir -p coverage`; `$(GO) test -race -coverpkg=$(COVERPKG) -coverprofile=coverage/coverage.out ./...` with `COVERPKG := ./memcoai/...,./internal/config/...,./internal/transport/...,./internal/fault/...,./internal/logging/...,./internal/warn/...,./internal/provenance/...,./internal/memory/...` (hand-written code only: not `internal/client` or `internal/testserver`); examples suite; `go tool cover -html`; awk over `go tool cover -func` total, fail below `$(COVERAGE_FLOOR)` |
| `test-all` | for each `$(GO_TOOLCHAINS)`: `GOTOOLCHAIN=$$v $(GO) test -race ./...` in both modules |
| `system-test` | `$(GO) test -tags systemtest -count=1 -timeout 40m -v ./systemtest/...` |
| `run-examples` | `cd examples`; for each `main` package from `go list`: `$(GO) run "$$pkg" \|\| exit 1` (does not skip without a credential) |
| `docs` | `rm -rf docs/_build/html docs/_build/markdown`; `$(DOC2GO) -out docs/_build/html -home github.com/memcoai/memcoai/go/memcoai -rel-link-style index -pagefind=false ./memcoai/...` (only the public package; doc2go also skips `internal` unless `-internal` is passed — never pass it); `cd memcoai && $(GOMARKDOC) --format github --output '../docs/_build/markdown/{{.Dir}}/index.md' --repository.url https://github.com/memcoai/memcoai --repository.default-branch "$(REVISION)" --repository.path /go/memcoai ./...`; `python3 ../scripts/build_llms_txt.py go` |
| `docs-serve` | `docs`, then `python3 -m http.server -d docs/_build/html 8000` |
| `build` | `$(GO) mod tidy -diff`; `$(GO) mod verify`; `$(GO) build ./...`; `cd examples && $(GO) mod tidy -diff && $(GO) build ./...` |
| `clean` | `rm -rf docs/_build coverage` |

System test is a separate package with `//go:build systemtest` (never compiled by `go test ./...`; vetted by `typecheck`), mirroring `python/systemtest` / `nodejs/systemtest`; `t.Skip` when `MEMCO_API_TOKEN` is empty.

### `.pre-commit-config.yaml`
- `&generated`: `'^(proto/|[a-z]+/client/|go/internal/client/|python/memcoai/memory/|nodejs/src/gen/|go/internal/memory/toolcopy_gen\.go$)'`.
- `provenance` `files`: add `go/internal/client/|go/go\.mod|`.
- `tool-docs` `files`: add `|go/internal/(client/tools/tools\.json|memory/toolcopy_gen\.go)` inside the group.
- New local hook `golangci-lint`: `name: golangci-lint (Go format and lint)`, `entry: make --no-print-directory -C go format lint`, `language: system`, `pass_filenames: false`, `files: ^go/.*\.go$|^go/\.golangci\.yml$`, `exclude: ^go/internal/client/` — the Go counterpart of the ruff (check + format) and prettier hooks.

---

## CI and release

Pins (same as existing workflows): `actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4`, `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065  # v5`, `actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02  # v4`, `actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093  # v4`; new `actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16  # v6`.

- **`ci_go.yaml`** — copy of `ci_nodejs.yaml`'s triggers, permissions, concurrency, `full` input and tiering. Jobs: `provenance` (shared), `lint`/`typecheck`/`test` (`with: go-version: "1.26"`), tiered `test-matrix`, `build`, `docs`, and `integration` (same `if:` as Node; `secrets: MEMCO_API_TOKEN: ${{ secrets.MEMCO_API_TOKEN }}`). Header comment: the pin is repeated in every call and equals `GO_VERSION` in `release_go.yaml`; 1.26 not 1.25 because golangci-lint v2.13.2 needs it under `GOTOOLCHAIN=local`; no runtime-compat job (MVS never resolves below go.mod; the matrix is the toolchain axis).
- **Each `ci_go_*.yaml`**: `on: workflow_call` with input `go-version` (except the matrix), `permissions: contents: read`, `defaults.run.working-directory: go`, checkout, setup-go with `cache-dependency-path: |\n go/go.sum\n go/examples/go.sum`.
  - `ci_go_lint.yaml`: `make lint LINT_TOOLCHAIN=local`.
  - `ci_go_typecheck.yaml`: `make typecheck`.
  - `ci_go_test.yaml` (`Test (${{ inputs.go-version }})`): `make coverage`; `Report coverage` step (`if: always()`) writing the total and a per-file table to `$GITHUB_STEP_SUMMARY` (dedupe profile blocks by key; "No report produced" when missing); upload `coverage-go` (`go/coverage/coverage.out`, 14 days).
  - `ci_go_test_matrix.yaml`: matrix `go: ["1.25", "1.27"]` (quoted), `fail-fast: false`, `make test`.
  - `ci_go_build.yaml` (`Build and consume`): tidy-diff + verify + build in both modules; zip the module as the proxy would (scratch module with `golang.org/x/mod@v0.41.0`, `zip.CreateFromVCS(out, module.Version{Path: "github.com/memcoai/memcoai/go", Version: "v"+Version}, $GITHUB_WORKSPACE, "HEAD", "go")`, unzip to `$RUNNER_TEMP/module`); assert `go.mod`, `LICENSE`, `internal/client/SDK_PROVENANCE.yaml`, `internal/client/tools/tools.json` and every `git ls-files internal/client` path are in it and no `examples/` path is; a fresh consumer module with `-replace` to the unzipped tree builds and prints `memcoai.Version` and the provenance commit; a consumer importing `…/go/internal/client/memcoai/memory/v1` fails with `use of internal package .* not allowed` (grep the output; `set -o pipefail` is absent — mirror Node's comment). No artifact.
  - `ci_go_docs.yaml`: setup-go + setup-python 3.12; `make docs`; upload `docs-go` (`go/docs/_build/html`).
  - `ci_go_integration.yaml`: copy of `ci_nodejs_integration.yaml` (guard step before checkout: token → run; no token on `pull_request` → notice + skip; otherwise `::error::` exit 1), `name: System tests (go)`, concurrency `memco-system-test-go` (no cancel), `timeout-minutes: 45`, `persist-credentials: false`, env `MEMCO_API_HOST: ${{ vars.MEMCO_API_HOST }}`, `run: make system-test`.
- **`release_go.yaml`** — `on: push: tags: ["go/v*"]`; `permissions: contents: read`; `concurrency: release-${{ github.ref }}` (never cancelled); `env: GO_VERSION: "1.26"`. Header: the slash is required; **the tag push is the publication** (proxy + sumdb keep the first copy forever); withdraw with `retract`, never by moving a tag.
  - `gate`: `fetch-depth: 0`; stdlib Python step: strip `go/v` (refuse otherwise), semver full-match without build metadata, equal to `const Version` in `go/memcoai/version.go`, major ≥ 2 requires `/vN` module suffix; outputs `version`, `prerelease`; then the "tagged commit is contained in main" step copied verbatim from `release_nodejs.yaml`.
  - `ci`: `uses: ./.github/workflows/ci_go.yaml`, `with: {full: true}`, `secrets: inherit`.
  - `publish` (`needs: [gate, ci]`, `environment: {name: go, url: https://pkg.go.dev/github.com/memcoai/memcoai/go/memcoai@v<version>}`, no secrets): setup-go (`cache: false`); `GOPROXY=https://proxy.golang.org go list -m -json github.com/memcoai/memcoai/go@v$VERSION` with up to 5 retries 30 s apart; then a fresh consumer `go get github.com/memcoai/memcoai/go/memcoai@v$VERSION` (default GOSUMDB) whose `go run .` must print `$VERSION`.
  - `release` (`needs: [gate, publish]`, `contents: write`): download `docs-go`, stage `stage/go/$VERSION`, `tar czf memco-docs-go-$VERSION.tar.gz -C stage go`, `gh release create "$TAG" --draft --verify-tag --generate-notes --title "Go SDK $VERSION" [--prerelease] memco-docs-go-$VERSION.tar.gz` (this is the workflow's own `gh`, as in the existing release workflows).
- Comment-only fixes: `release_python.yaml:3-4` and `release_nodejs.yaml:3-5` say `go-v*` → `go/v*` (the slash is required); `provenance.yaml` "runs twice" → "three times" and mention `go/internal/memory/toolcopy_gen.go`; `ci_nodejs.yaml`/`ci_python.yaml` "called from here as well as from …" → include `ci_go.yaml`.

---

## Repository docs

- **`README.md`**: add Go CI badge (`ci_go.yaml`) and a `go-1.25 | 1.26 | 1.27` badge (color `00add8`); coverage-badge comment names `COVERAGE_FLOOR` in `go/Makefile`; SDK table row `| Go | [`github.com/memcoai/memcoai/go/memcoai`](go/) | `go get github.com/memcoai/memcoai/go/memcoai` |`; **delete** "The generated gRPC client for [Go](go/) is also published here and can be used directly against the API."; replace "Go ships an accessor for it at `go/client/tools`;" with "the Go SDK compiles it into `go/internal/memory/toolcopy_gen.go`;"; add `### Go` quick start (below); layout block `go/ the Go SDK` / `memcoai/ hand-written SDK` / `internal/client/ generated gRPC client, and the tool manifest`; generated-path sentence adds "for Go `go/internal/client/`"; contributing sentence names the Go section.

```go
client, err := memcoai.NewClient(memcoai.Options{}) // reads MEMCO_API_TOKEN
if err != nil { log.Fatal(err) }
ctx := context.Background()
defer client.Close(ctx)
if err := client.Connect(ctx); err != nil { log.Fatal(err) }
session, err := client.Memory.StartSession(ctx, "coding")
if err != nil { log.Fatal(err) }
result, err := session.Search(ctx, "how should a client authenticate against the memory API", memcoai.ScopedSearchParams{})
if err != nil { log.Fatal(err) }
for _, memory := range result.Memories {
	for _, insight := range memory.Insights {
		fmt.Println(insight.Title, insight.Updated)
	}
}
```

- **`CONTRIBUTING.md`**: intro "three can be read side by side" + Go bullet (published through the Go module proxy as `github.com/memcoai/memcoai/go`); generated block `go/internal/client/`; provenance paragraph mentions the go.mod floor check; tool-manifest table Go row → `go/internal/memory/toolcopy_gen.go`; prerequisites row `| Go | a Go toolchain ≥ 1.21 | go fetches the toolchain go.mod names; -race needs a C compiler |` and "You only need more than one if you are changing more than one"; `make -C go help`; licence paragraph "update all four together". New `# Go` section with the same seven headings (Prerequisites — floor 1.25.0 forced by grpc v1.83.1, CI pins 1.26, matrix 1.25/1.27, `LINT_TOOLCHAIN`, no `go.work`; Setting up; Running the checks; Layout — public `memcoai/` vs `internal/` (the Python `_*.py` rule, the no-cycle rule that keeps wire↔public mapping in `memcoai`, the shared vs per-service internal packages ready for the user-management service), why the client is internal, why `internal/descriptor.go` exists, why `examples/` is its own module; Tests — real in-process server, `-race`, stdlib `testing` only, behaviour-named tests, `### The system test` describing what `go/systemtest` actually does; Documentation — the doc comment convention (public: Parameters / returns / Errors + an `Example…`; internal: terse or none) with `[Name]` links, only `memcoai/` is documented, doc2go + gomarkdoc, staticcheck's ST1000/ST1020-22 (enabled via `.golangci.yml`) for doc-comment form + `docs_test.go` as the presence check, llms pair; Style — `go/.golangci.yml` is the whole style guide: `make -C go format` applies gofmt (simplify) + goimports, `make -C go lint` runs the linters and static analysis; suppress only with `//nolint:<linter> // <reason>` (nolintlint enforces both parts)).
- **`go/README.md`** (mirrors `nodejs/README.md`): logo block, Install, Quick start, Configuration (env table), Sessions & operations, Agents (Toolset, Briefing, Render, the Anthropic example), Errors (`errors.As`, subtype first), Timeouts & cancellation (context), Logging (`MEMCO_LOG`, `Options.LogLevel`, `SetLevel`, `Options.Logger`, silencing with `none`, deprecation notices as Warn records), Provenance, Versioning (`go/vX.Y.Z`), Go adaptations list, Contributing, Licence.
- **`go/examples/README.md`**: table of the seven examples, `cd go/examples && go run ./quickstart`, note that the `replace` line is repo-only, `anthropic_agent` needs `ANTHROPIC_API_KEY`.

---

## Examples (`go/examples`, nested module)

Each directory: `main.go` with `func main()` creating `ctx` with `signal.NotifyContext(ctx, os.Interrupt)` → `run(ctx, …) error` (all I/O through parameters so tests can drive it), and `main_test.go` running `run` against `testserver`. Ports of `nodejs/examples/*.ts`: `quickstart` (connect, list domains, search), `search_and_rate` (session, search, rate), `contribute` (write, then revert), `import_memories` (batch + per-entry outcome), `handling_errors` (every error branch via `errors.As`, subtype first), `concurrent_searches` (N goroutines on one client).

`anthropic_agent` (port of `python/examples/langchain_agent.py`'s flow: same TASK text, `DOMAIN = "coding"`, runs the task twice to show a memory miss vs. hit, prints tokens and time):
- Model `claude-opus-5` (overridable with `MEMCO_EXAMPLE_MODEL`), `MaxTokens: 16000`, adaptive thinking left at the model default (unset).
- System prompt = `IDENTITY + "\n\n" + memcoai.Briefing(entry, session.Instructions)`; tools = `session.Tools().ToAnthropic()` converted to the SDK's tool params (name, description, `InputSchema{Properties, Required}`), **plus** Anthropic's server-side web search tool (`web_search_20260209`) as the fallback research source (replaces Python's `ddgs`; no client-side handling).
- Uses `client.Beta.Messages.New` with server-side refusal fallbacks enabled by default (`Fallbacks: []anthropic.BetaFallbackParam{{Model: "claude-opus-4-8"}}` + beta `anthropic.AnthropicBetaServerSideFallback2026_06_01`). Manual loop: append `resp.ToParam()` each turn; `pause_turn` → re-send; `tool_use` → `toolset.Call(ctx, block.Name, json.RawMessage(block.JSON.Input.Raw()))`; a returned error (non-recoverable) ends the run; results go back as `tool_result` blocks in **one** user message; `refusal` → print `StopDetails.Category` and end; anything else → print the text and stop.
- **Exact beta type/func names** (`BetaToolParam`, `BetaToolUnionParam`, `NewBetaUserMessage`, `NewBetaToolResultBlock`, `BetaStopReason*`, the web-search tool param) must be taken from anthropic-sdk-go v1.73.0's source/`examples/` (the refusal-fallback example) and the compiler — do not guess; write the file, then fix what `go vet` reports.
- `main_test.go`: `httptest.Server` speaking the Messages API, client built with `option.WithBaseURL(srv.URL)`, `option.WithAPIKey("test")`, `option.WithMaxRetries(0)`: a scripted `tool_use` gets a `tool_result` equal to `memcoai.Render` of the fake's answer; malformed input comes back as `invalid request…` text; an `AuthenticationError` from Memco ends the run with that error; `stop_reason: "refusal"` ends the run.
- A guard test (in any example package) parses every example and fails if one declares `init()`.

## System test (`go/systemtest`, `//go:build systemtest`)

Port `nodejs/systemtest/lifecycle.test.ts` and `tools.test.ts` step for step (per domain from `ListDomains`; zero domains is a failure): start session → create (nonce `gosys-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>` or random hex, only in the query; real prose about **this Go SDK** — e.g. its context-deadline rule — distinct from Python's and Node's content) → poll search (180 s, 15 s interval; resolve bare references with `GetMemory`) → share feedback → get memory → enrich → poll session-less search → revert enrichment (`ADDITION_REMOVED`) → revert original (`MEMORY_REMOVED`) → poll until gone (60 s) and `GetMemory` → `NotFoundError`. Every operation id registered for cleanup via `t.Cleanup` in reverse order (errors logged, not failed). `tools_test.go`: `ListTools` reports everything available; a session offers exactly `memory.ToolPrefix` + each of `memory.OfferedTools` (the system test is in the SDK module, so it may import `internal/memory`). Every poll loop and cleanup uses a context with a deadline. No import step (the Node/Python suites have none either).

---

## Tests (write all first; each must fail for the stated reason)

Stdlib `testing` only; `-race`. Unit tests sit beside the internal package they pin; behaviour tests (through a real client and the fake server) sit in `go/memcoai`. Reasons: **[S]** stub returns `fault.Internal("not implemented")`; **[Z]** stub returns a zero value; **[W]** wiring missing; **[A]** artefact missing. **⚠** = could pass vacuously against a stub — include the named positive control.

**Fake server — `internal/testserver`** (not a test file; built in step 2): `Memory` embeds `memoryv1.UnimplementedMemoryServiceServer`, mutex-guarded, keyed by RPC name (`ListDomains`, …): `Calls()`, `Metadata()` (full `metadata.MD` per call), `Request(name)`, `SetResponse`, `SetError(code, details)`, `SetRichError(code, details, reason, domain)` (`status.New(...).WithDetails(&errdetails.ErrorInfo{})`), `FailNext(name, …Failure)` (recorded **before** aborting), `Delay(name, d)` (returns early when the stream context ends), `Forget()`; defaults as `nodejs/tests/fakeServer.ts:178-225` (ListDomains empty; StartSession/Search/ShareFeedback `session-a`; GetMemory echoes idx; Create `create-a`; Enrich `enrich-a`; Revert echoes op id + MERGED; Import one QUEUED per memory; ListTools all 10 methods available). `Health`: settable `Status`, `Checked()`, `Metadata()`, `FailNext`. `Start(t testing.TB) *Harness` on `127.0.0.1:0`, `Stop()` in `t.Cleanup`, `Address`. (It must not import `memcoai`; a small helper in `memcoai`'s own `_test.go` builds a connected client with `Plaintext: true`, `Token: "test-token"`, a capturing `Options.Logger`, then `Forget()`s the connect calls.)

`go/internal/config/config_test.go` [Z]: every token/host/port/timeout rule and exact message in the spec; legacy key warns once; `MEMCO_API_TOKEN` precedence; unsendable tokens (`\u200b`, `\n`, `é`) name the index and never the value; `sk-live_AB.cd-09+/=~` accepted; IPv6 tables; `+50051` accepted; `99999999999999999999` echoed; timeout checked before token (`got -1s`); `DialTarget()` for host `unix:443` is `dns:///unix:443`; `[fe80::1%eth0]:443` passes `grpc.NewClient`; `Credential` renders `[redacted]` under `%v %+v %#v %s`, `json.Marshal` and `slog` ⚠ (control: `Reveal()`/equivalent still returns the token to `transport`).

`go/internal/logging/logging_test.go` [Z]: default level info; `MEMCO_LOG` table (case/whitespace tolerated; `warn/fatal/trace/off` refused → one Warn record `MEMCO_LOG ignored: …`); `none` silences critical ⚠ (control: written at critical); `SetLevel` exact error; `CRITICAL`/`WARNING` labels; `Named` adds `logger=memco.<area>`; output follows `os.Stderr` reassigned after init; a failing writer never panics ⚠ (control: record reached a working writer first); `Default()` is not `slog.Default()`.

`go/internal/warn/warn_test.go` [Z]: once per message; a changed message re-surfaces; verbatim text; sunset suffix; `warning=MemcoDeprecationWarning` attr at Warn; empty says nothing ⚠ (control: a non-empty one is written); legacy warning once; `Reset()`.

`go/internal/fault/fault_test.go` [Z]: all 17 codes + code 42 → kind/code; canonical names incl. `CANCELLED`, `UNRECOGNIZED(99)`; sunset detection (memco.ai + known reason → kind; other domain → none; unknown then known reason → kind; no details → none); exhaustion-kind table incl. "daily rate limit" → quota; a wrapped status (`fmt.Errorf("max retries exhausted…: %w", st.Err())`) keeps the server's detail; cause set only when the passed ctx has ended; non-status error → API/Unknown.

`go/internal/transport/transport_test.go` (uses `testserver`): user agent passes through (server sees `<ua> grpc-go/…`) [W]; `ServiceConfig(memory.Service)` unmarshals to the expected policy, names exist in `memoryv1.MemoryService_ServiceDesc`, health entry equals `grpc_health_v1.Health_Check_FullMethodName`, a second fake `Service` is appended, not merged [Z]; connect params 200 ms / 5 s / `MinConnectTimeout` 20 s [Z]; retries counted on the fake: one blip → 2 calls; gives up after exactly 3; ListDomains/ListTools/health retried; Search, Create, Enrich, Share, Revert, Import, StartSession exactly 1 call each [W]; every memory RPC carries exactly one `Bearer test-token`; the health probe carries none even when the caller's context has one ⚠ (control: probe ran); caller metadata (3 `AppendToOutgoingContext` spellings + capitalised `MD` key) cannot displace it, `x-other` survives, the caller's metadata is unmodified [W]; TLS by default verifies certificates (self-signed TLS testserver → UNAVAILABLE, 0 health calls) [W]; `Plaintext` selects insecure credentials only when set [Z]; `StatusName` incl. `UNRECOGNIZED(7)` [Z].

`go/internal/provenance/provenance_test.go` [Z]: `Read()` → 40-hex commit, `memcoai/memory/v1/memory.proto` listed with the sha256 of `../../../proto/memcoai/memory/v1/memory.proto`; `Parse` table with the exact messages and the Node/Python edge cases (quoted `#`, column-0 comments, nested decoys, key suffixes, CRLF, flush-left/indented sequences, extra spaces after dash, several dash-alone entries, block scalars, duplicate keys).

`go/internal/memory/` [Z]:
- `limits_test.go`: nothing known at first; learns limits and caps; later nil keeps them; caps accumulate per slug; re-report replaces; unknown domain → 0; session-only → 0; mutating the reported message changes nothing.
- `validate_test.go`: blank table by field name (`*fault.Error`, `codes.InvalidArgument`); nothing checked without caps (1 MB passes); cap 0 = none; over cap refused with exact text; code points (emoji); trim keeps the first entries and never mutates its input, logs `trimmed` at debug only when trimming; scope table; blank-but-given refused; sentinel exactly `new`; feedback/tag/source/import messages incl. absolute offsets.
- `requests_test.go`: every refuse/trim rule per RPC once limits are known (exact texts, incl. `title and content are 120 characters together, which exceeds the combined limit of 100`); `OpId`; `Version`/`Comment` nil when `""` (via the memcoai copy helpers — test that end in `memcoai`); import grouping (offsets 0, 2, 4), no cap → one group, per-entry caps name the entry, refusing tag count before trimming, empty batch refused; invalid UTF-8 in every string field → `a field value cannot be sent: …invalid UTF-8`; a blank tag's own message wins.
- `wire_test.go`: date table (Node's plus `2026-W35-3`, trailing `\n`, `0000-02-29`, leap day kept); enum folding; `RequireMemory` exact NotFound.
- `agent_test.go`: schemas (bound names absent; every parameter/nested field described — control count > 0; exact `required`, never nil; tag/feedback items with `additionalProperties: false`; copy from exactly one source, verbatim; fresh maps per call); `Arguments` malformed / wrongly-typed tables (snake keys), null optional = absent, `session_id`/`domain`/`timeout` → unknown argument, extra key inside a tag dropped, hostile names `%q`-escaped; `Decode` table (valid JSON, invalid JSON, string, array, null, 42); `Available` filtering (creator/reader/admin tables; missing from catalog → not offered).
- `toolcopy_test.go` [A]: reads `../client/tools/tools.json`; every manifest tool carried; descriptions equal the manifest with markers spelled (offered → `memco_x`; answered and `import_memories` → bare); pinned `COMPARED` both directions — `create_memory: content, domain, query, session_id, title`; `enrich_memory: content, memory_idx, session_id, sources, title`; `get_memory: idx`; `import_memories: domain, memories, session_id`; `revert_memory: operation_id`; `search: domain, query, session_id`; `share_feedback: session_id`; `start_session: domain`; `NESTED_COMPARED`: `memories[].insights`, `memories[].insights[].content`, `memories[].insights[].title`, `memories[].queries`; no `${`; `feedback`/`source`/`tags` absent; DO NOT EDIT header; `format.Source(src) == src`.

`go/memcoai/` (behaviour through the public API):
- `foundation_test.go`: `TestLicenceMatchesRepositoryRoot` (`../LICENSE` vs `../../LICENSE`) [A]; `TestGoModMatchesDescriptorFloors` (go directive and grpc/protobuf ≥ `requires.go`; direct requires ⊆ {grpc, protobuf, genproto rpc}) [A]; `ReadProvenance` works without a client and returns clones [Z].
- `errors_test.go`: `public` maps every fault kind/code to the right concrete type [Z]; `errors.As` reaches every ancestor and `Error`, never unrelated types [Z]; messages `INVALID_ARGUMENT: …` [Z]; 3× UNAVAILABLE on GetMemory → `*UnavailableError` with `Detail == "try again"` [S]; caller-cancelled ctx → `*InternalError` + `errors.Is(err, context.Canceled)`; expired → `*TimeoutError` + `errors.Is(err, context.DeadlineExceeded)`; server-sent DEADLINE_EXCEEDED is **not** `Is` [S]; sunset at connect → `*SunsetError{Kind: SunsetClientVersion}` and `errors.As` to `*PreconditionFailedError` [S].
- `client_test.go` [S]: `NewClient` sends nothing ⚠ (control: Connect on the same harness records calls); bad `Options.LogLevel` → `*ConfigError`; config errors surface as `*ConfigError`; Connect probes `""` then ListDomains; NOT_SERVING → `*UnhealthyError` exact text, 0 memory calls; status 7 → `UNRECOGNIZED(7)`; rejected credential surfaces after health; Connect retryable after failure; health unavailable 3× → `*UnavailableError`, 3 checks; unreachable port → `*UnavailableError`; `Close(ctx)` idempotent ⚠ (control: next call refused); call after Close → exact `*ConfigError`, 0 calls; Connect after Close → `*ConfigError`; Close waits for an in-flight delayed search; two concurrent closers both wait; **Close with a ctx that ends first returns `*TimeoutError` (deadline) / `*InternalError` CANCELLED (cancel) with `errors.Is(err, ctx.Err())`, still closes, and the in-flight call ends with a typed error**; limits learned at connect enforced with 0 calls; ctx deadline shorter than default → Timeout; longer than default honoured; no deadline → `Options.Timeout` applies; **already-ended ctx → typed error, 0 calls** ⚠ (control: same call with a live ctx records a call); `Provenance()`; the user agent is `memco-go/`+`Version` (no space, semver).
- `logging_test.go` [S]: records reach `Options.Logger` with `logger=memco.client` etc.; `connected` / `closed connection` at info; `rpc ok` debug record with `rpc`, `elapsed_ms`; **a returned error produces no record at any level** (rejected credential at Connect, a failed Search, a local validation failure, Close cut short) ⚠ (control: the same capture holds the `rpc ok` record of a successful call); ListTools-failure warning (handled, so logged); deprecation notice as one Warn record across two ListDomains (`upgrade the SDK (stops working on 2027-01-01)`), malformed sunset → message without suffix; `TestNoCredentialInAnyRecordAtDebug` ⚠ (control: records present and the server saw `Bearer test-token`; no record, attr or rendered line contains the token or "Bearer"); `LogLevel "none"` on the default logger silences everything incl. the deprecation notice ⚠ (control: the same run at info writes them).
- `convert_test.go` [Z]: instructions part by part and nil → empty; repeated fields copied; domain entry field by field; empty optional strings; limits nil vs populated; deprecation fields; enums typed; import renumbering + first instructions; search nesting; feedback binding (own idx, comment forwarded, exact empty-entries Internal, search binds its session id); wire copies: `Version`/`Comment` nil when `""`, AGENT = 2 for zero Source, caller slices never shared.
- `types_test.go` [Z]: enum numbers equal the generated `memoryv1` values; `String()` names; unknown values fold.
- `memory_test.go` [S]: every operation round-trips through the fake; StartSession records StartSession + ListTools, blank domain sends nothing, StartSession failure wins, ListTools failure → session + warning, a cancelled ctx leaves no goroutine behind (compare `runtime.NumGoroutine()` before/after, allowing for grpc's own; no goleak dependency); empty catalog offers nothing vs failed fetch offers everything; feedback from plain search, session search, `GetMemory` (refused locally, 0 calls), `Session.GetMemory` (sends session id), empty reply, repeated, after close, hand-built memory; revert NOT_FOUND is a value; import requires a scope (0 calls); **import stops between groups when ctx is cancelled** (no further calls, typed context error).
- `session_test.go` [S]: ID/instructions; scope binds the session onto search/create/enrich/share/import with wire domain `""`; forwards revert and get; still rejects locally with 0 calls; import of 5 with cap 2 → 3 calls, indices 0..4, first instructions; a bad 5th entry fails after two groups were sent.
- `parity_test.go` (reflection) [Z]: `Session` has every `MemoryOperations` method except ListDomains/ListTools/StartSession; no `Scoped*Params` has `Domain`/`SessionID`; Session methods minus {Tools, ImportMemories}, snake-cased, equal `memory.OfferedTools`.
- `agent_test.go` [S unless noted]: offered tools exactly, in order; catalog fetched once per session; failed catalog → all; calls checked on the wire (rendered result, `1 memory`, tag and rating objects reach the wire, create reports `create-a` with source AGENT, model-sent `source` → `unknown argument(s): "source"`, `cannot be undone`, enrich with `memory_idx`, `create-a: merged`, advice rendered); bad arguments → `invalid request…` text; NotFound → `nothing found`; `*AuthenticationError` **returned** by `Tool.Call` and `Toolset.Call`; `IsAgentRecoverable` table; `Toolset.Call` by name (`0 memories`), unknown name lists sorted names; a cancelled ctx is returned as an error, not text; Anthropic/OpenAI shapes carry the schema unchanged [Z]; `Render` exact strings (reference text, trust signals, full memory, `null:` revert id, notice, all instruction parts, ratings with/without advice, `memory removed`) [Z]; `Briefing` exact lines and no `\n\n\n` [Z].
- `regressions_test.go` (each with a comment naming the defect it pins): whitespace scope end to end (0 calls); expired ctx not widened; close race (5 clients × 8 goroutines × 4 methods: every error nil or satisfies `errors.As(err, new(memcoai.Error))`); blank host; IPv6 dial of `[::1]` (skip without IPv6); empty operation id; unsendable text everywhere; blank tag keeps its message; 8 concurrent calls survive Close; Connect retried after a transient failure.
- `docs_test.go` [A] (via `go/doc` on this package only): every exported identifier and exported struct field has a doc comment; every exported function/method with parameters has a `Parameters:` list naming each parameter; every one returning `error` has an `Errors:` paragraph; `NewClient`, `ReadProvenance`, `SetLevel`, `Render`, `Briefing`, every `Client`, `MemoryOperations`, `Session`, `Toolset`, `Tool` method and `Memory.Feedback` have an `Example…` in `example_test.go` (package `memcoai_test`, compiled only, no `// Output:`). `Error`, `Unwrap`, `String` are exempt from the Parameters/Errors/Example rules.

`go/examples/*/main_test.go` [A]: each `run()` against testserver (quickstart prints domains; search_and_rate shares feedback; contribute writes then reverts; import prints outcomes; handling_errors walks each staged branch; concurrent_searches runs N calls on one client; anthropic_agent as described; every `run` returns promptly when its ctx is cancelled).

Python: the `test_tool_copy.py` and `test_llms_txt.py` additions listed under Tooling.

---

## Implementation order

Each step: write/confirm its tests, run them, see the expected failure, implement, re-run the named target. Heavy make targets one at a time.

0. Copy this plan to `plans/go-sdk.md`. Step 0 move (`git mv`, then continue).
1. Reference updates for the move: `scripts/verify_provenance.py` paths (MANIFESTS, descriptor glob, comment), `.pre-commit-config.yaml` regexes, README/CONTRIBUTING path mentions. → `make provenance` green.
2. Module scaffolding: `go/go.mod` + `go.sum` (`GOPROXY=off GOFLAGS=-mod=mod go mod tidy` once the first imports exist), `go/LICENSE` (copy of root), `go/.golangci.yml`, minimal `go/Makefile`, `go/internal/descriptor.go`, `go/internal/testserver`, `go/memcoai/version.go`. verify_provenance section 5 (tests first; break a floor only in a scratch copy of go.mod). → `make provenance`; `cd go && go build ./...`.
3. Generator Go target: Python tests (fail) → implement → `mkdir -p go/internal/memory` + hand-written `toolcopy.go` → `make tool-docs` → `make tool-docs-check` → `make -C python test`.
4. Skeleton: every package under Layout with its exported identifiers and signatures, stub bodies (return `fault.Internal("not implemented")` — typed even in a stub — or zero values); public doc comments can be one line for now. Then write **all** Go test files above. → `make -C go typecheck` green; `cd go && go test ./...` compiles and fails at run time for the stated reasons (paste the failure summary to the user). Review every ⚠ control.
5. `internal/config` → its tests.
6. `internal/logging`, `internal/warn` → their tests.
7. `internal/fault` → its tests; `memcoai/errors.go` (`public`) → the unit parts of `errors_test`.
8. `internal/memory/service.go` + `internal/transport` → its tests.
9. `memcoai/client.go` (NewClient, call, Connect, Close, health) + minimal `ListDomains` (copy, `known.Update`, `warn.Deprecation`, convert) → `client_test`, `logging_test`, context parts of `errors_test`.
10. `internal/memory/limits.go`, `validate.go` → their tests.
11. `internal/memory/requests.go`, `wire.go` → their tests.
12. `memcoai/types.go`, `convert.go` → `convert_test`, `types_test`.
13. `memcoai/memory.go` → `memory_test`.
14. `memcoai/session.go` → `session_test`, `parity_test`.
15. `internal/provenance` + `ReadProvenance` → their tests, `foundation_test`.
16. `internal/memory/agent.go` → its tests + `toolcopy_test`; `memcoai/agent.go` → `agent_test`.
17. `cd go && go test -race ./...` → `regressions_test` green; no stub remains (`grep -rn '"not implemented"' go/` finds nothing); `grep -rnE 'errors\.New|fmt\.Errorf' go/memcoai go/internal --include='*.go' | grep -v _test.go` finds nothing that can reach a caller.
18. `doc.go`, full public doc comments (convention above), `example_test.go` → `docs_test`; trim internal comments to the terse rule; `make -C go lint`.
19. `go/examples` module (needs network for anthropic-sdk-go) and its tests → `make -C go test`.
20. `go/systemtest` → `make -C go typecheck` (compiles it); the live run is the user's (`make -C go system-test` with `MEMCO_API_TOKEN`).
21. Full Makefile targets (`install`, `coverage`, `test-all`, `docs`, `docs-serve`, `build`, `run-examples`) and the `build_llms_txt.py` Go reference (Python tests first) → `make -C python test`, then `make -C go docs` (confirm no `internal` page was generated), then `make -C go build`, then `make -C go coverage` (≥ 95%).
22. CI + release workflows and the comment-only fixes; `.pre-commit-config.yaml` golangci-lint hook.
23. README, CONTRIBUTING, `go/README.md`, `go/examples/README.md`.
24. `make check` (root; fans out to python, nodejs, go), then an independent review by a fresh agent team (quality, simplicity, clarity, security) per AGENTS.md; report nitpicks outside the request instead of applying them.
25. Report to the user: summary, the upstream export changes (section above), and the items under Risks.

## Verification

- `make provenance`, `make tool-docs-check`, `make -C python test` (generator + assembler tests), `make -C nodejs test` (unchanged, must stay green).
- `make -C go lint`, `make -C go typecheck`, `make -C go test`, `make -C go coverage` (≥ 95% of `memcoai/` statements), `make -C go test-all` (1.25.13 / 1.26.8 / 1.27.0), `make -C go build`, `make -C go docs` (then `find go/docs/_build -name index.md`; open `go/docs/_build/html/index.html`; check `llms.txt`/`llms-full.txt` exist).
- `make check` at the root.
- Manual smoke (user runs, needs a credential): `cd go/examples && go run ./quickstart`; `make -C go system-test`.
- `git status` shows the step-0 moves as pure renames (`R100`) plus only intended new/changed files; `make -C go lint` and `make -C go format` (then `git diff --stat` unchanged) prove the tree is golangci-clean.

## Risks and open items to report

1. Repo must be public before the first `go/v*` tag (proxy fetch). Tag pushes publish irrevocably.
2. Upstream export must stop writing `go/client/` and must not wipe `go/` (see the upstream list); until then `make provenance` fails if `go/client/` reappears — by design.
3. gomarkdoc v1.1.0 is from 2023 — verify it builds and runs (`go run github.com/princjef/gomarkdoc/cmd/gomarkdoc@v1.1.0 --version`) before wiring docs; its output escapes `-`/`(`/`)` heavily (acceptable; an unescaping rule is optional follow-up).
4. anthropic-sdk-go, doc2go, gomarkdoc and golangci-lint (via `go run`) need network to download; golangci-lint v2.13.2 needs a 1.26 toolchain (`LINT_TOOLCHAIN`).
5. `-race` needs cgo and a C compiler.
6. Adding `go/Makefile` makes root `make install`/`make check` require a Go toolchain for every contributor.
7. The existing `CONTRIBUTING.md` describes a permanent `import_memories` step in the Python/Node system tests that neither suite contains — report, don't fix.
8. `--generate-notes` picks the previous release regardless of language (affects all release pipelines) — report as follow-up.
9. The Anthropic example enables server-side refusal fallbacks (`claude-opus-4-8`) by default — tell the user; drop if they decline.
