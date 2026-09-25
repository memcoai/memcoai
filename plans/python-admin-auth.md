# Python SDK: client credentials, administration, impersonated sessions (+ 0.2.0)

## Context

The server has added two gRPC services next to `MemoryService`. The contracts are exported and the generated clients are present but untracked:
- `proto/memcoai/auth/v1/auth.proto` defines `TokenService.IssueToken`, a client-credentials exchange. It is the one unauthenticated RPC. The default lifetime is 3600s, and `ttl_seconds` values above 86400 are refused.
- `proto/memcoai/admin/v1/admin.proto` defines `AdminService`: networks, network members, IdP groups, external users, their keys, and impersonation.

The Python bindings are at `python/memcoai/{auth,admin}/v1/`, and `SDK_PROVENANCE.yaml` now lists admin and auth before memory.

Builders need to map their own customers' users into Memco and act as those users. So the Python SDK must:
- accept an API client's `client_id` + `client_secret` instead of an API key, exchange them for a bearer token, and refresh it;
- expose network and user administration for direct use (not as agent tools);
- open memory sessions that impersonate one external user, with many such sessions live at once on the async client.

All three SDKs go to 0.2.0. The Go and Node.js admin implementations come later; only their CI wiring and export housekeeping are in this change.

**Server facts that shape the design:**
- A client token has no content role, so MemoryService refuses it.
- Impersonation keys: 60 min by default, 1440 max, at most 20 live per user. An ended key is refused with UNAUTHENTICATED.

**User decisions (fixed):**
- Namespaces are `client.networks` and `client.users`.
- API keys and IdP groups are in scope.
- There is no standalone impersonate API. Impersonation is `start_session/with_session(domain, external_id=...)`.
- Keys renew automatically. A session must be closed, by leaving its with-block or calling `close()`, which ends the key. Client `close()` ends any key still live.
- Every impersonated session runs **impersonate → list_domains (with the key) → StartSession**, so limits and deprecation are learned per session.
- `MEMCO_CLIENT_ID` / `MEMCO_CLIENT_SECRET` env vars are read, and win over `MEMCO_API_TOKEN` when no `token` argument is given.
- Add `MemcoAlreadyExistsError`.
- CI reads `MEMCO_CLIENT_ID` / `MEMCO_CLIENT_SECRET` from GitHub Environments `python`, `go` and `nodejs`. Environment approval on PRs is accepted.

**Working rules:**
- Save this plan as `plans/python-admin-auth.md` before starting.
- Tests first: land all of them failing, then implement one path at a time.
- Leave everything uncommitted; the user commits.
- Run make targets one at a time.
- Finish with a fresh-agent review.

## Public API

```python
Memco(token=None, host=None, *, client_id=None, client_secret=None, token_lifetime: int | None = None,
      tls=True, timeout=DEFAULT_TIMEOUT, env=None, log_level=None)   # AsyncMemco identical
```

**Credential resolution (`_config.py`):**
- Arguments beat the environment.
- Passing a `token` argument together with `client_id`/`client_secret` arguments raises `MemcoConfigError`.
- A `token` argument alone ignores the client-credential env vars.
- With no credential arguments: if both `MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET` are set, use client credentials, even if `MEMCO_API_TOKEN` is set. Otherwise use `MEMCO_API_TOKEN` or the legacy `MEMCO_API_KEY`, as today.
- `MemcoConfigError` for:
  - half a pair, in args or in env;
  - a blank value;
  - `token_lifetime` without client credentials, or ≤ 0.
- `token_lifetime` is sent as `ttl_seconds`; `None` sends 0, the service default. No local maximum.
- `ClientConfig` gains `client_id` and `client_secret`, with `repr=False` on the secret.

**`client.memory`:**
- `start_session(domain, *, external_id=None, timeout=None)` and `with_session(...)` (async returns `AsyncSessionOpener`).
- `Session.close()` and `async AsyncSession.close()`.
- `__exit__`, `__aexit__` and `AsyncSessionOpener.__aexit__` call `close()`.
- For a plain session, `close()` is a no-op and the session stays usable.

**New public module `memcoai/administration.py`, exported from `memcoai`:**
- `NetworkOperations` / `AsyncNetworkOperations`, reached as `client.networks`:
  - `list(*, name, scope, owner, domain, parent_id, ids, page, page_size)` → `NetworkList`
  - `create(*, name, parent_id, domain, region, scope, owner, description)` → `Network`
  - `update(network_id, *, name, parent_id, scope, owner, description)` → `Network`. `None` leaves a field unset; `""` is sent, which clears it.
  - `delete(network_id)` → `DeletedNetwork`
  - `list_members(network_id, *, search, page, page_size)` → `MemberList`
  - `add_member(network_id, user_id, *, force=False)` → `MemberPlacement`
  - `remove_member(network_id, user_id)` → `None`
  - `list_groups(*, name, network_id, ids, page, page_size)` → `GroupList`
  - `list_group_members(group_id)` → `tuple[Member, ...]`
  - `add_group(network_id, group_id)` → `None`
  - `remove_group(network_id, group_id)` → `None`
- `UserOperations` / `AsyncUserOperations`, reached as `client.users`:
  - `list(*, search, page, page_size)` → `ExternalUserList`
  - `get(external_id)` → `ExternalUser`
  - `create(external_id, *, roles, name=None, email=None)` → `ExternalUser`
  - `update(external_id, *, name=None, email=None, roles=None)` → `ExternalUser`
  - `delete(external_id)` → `None`
  - `list_keys(external_id)` → `tuple[ExternalUserKey, ...]`
  - `create_key(external_id, *, preset, name=None, valid_until: datetime | None = None)` → `CreatedKey`
  - `delete_key(external_id, key_id)` → `None`
- Every method takes `timeout: float | None = None` last.
- **Rejected locally, with no RPC sent:**
  - blank handles;
  - a bare `str` passed for `ids` or `roles`;
  - empty `roles` on create, or on update when given (on the wire an empty list reads as "unset");
  - a naive `valid_until`.
- Scopes, presets, roles, TTLs and id counts are left to the service.

**New types in `types.py`**, all `@dataclass(frozen=True, slots=True)`:
- `Network`, `NetworkList(networks, total_count)`
- `Member`, `MemberList`, `MemberPlacement(network_id, user_id, moved_from: str | None)`
- `DeletedNetwork(id, removed: tuple[tuple[str, int], ...])`, with `removed` sorted
- `Group`, `GroupList`
- `ExternalUser`, `ExternalUserList`
- `ExternalUserKey(..., valid_until: datetime | None)`: UTC, 0 becomes `None`
- `CreatedKey(key, value=field(repr=False))`

**`errors.py`:** add `MemcoAlreadyExistsError(MemcoAPIError)`, mapped from `ALREADY_EXISTS`.

## Credential design

1. **Per-call bearer (`_auth.py`, `_channel.py`)**
   - Replace the fixed-token interceptors with explicit `metadata=` on each call. Delete `AuthInterceptor`, `AsyncAuthInterceptor`, `_ClientCallDetails`, `_merged` and `UNAUTHENTICATED_PREFIX`. Keep `AUTH_HEADER` and `AUTH_SCHEME`, and add `metadata(token)`.
   - Channels are built without interceptors, and the retry config is unchanged.
   - Every stub call goes through the client's `_send(method, request, deadline, bearer)`, where `bearer` is the leased `Minted` and `.value` is read only inline in `metadata=`, so no frame an error carries holds the bearer as a plain string. Health and IssueToken are sent without metadata, and any stray stub call carries no credential, so the design fails closed.
   - This makes the credential an argument, not ambient state, which is what rules out crosstalk between concurrent sessions.
2. **`Renewing` / `AsyncRenewing` (private, `_auth.py`)**
   - `Minted` holds `value` (repr hidden), `renew_at` (monotonic), `key_id`, `users` and `retired`.
   - `lease()` is a context manager that yields the `Minted` itself (its repr hides the value). Under a lock:
     - raise `MemcoConfigError` if closed;
     - if there is no value or `monotonic() >= renew_at`, mint (single-flight);
     - retire the old value;
     - `users += 1`.
   - A retired value is ended only when its last lease exits. A renewal therefore never revokes a key under an in-flight call, and there is one live key per session.
   - A failed mint raises the typed error and keeps the old value; the next call retries.
   - `close()` is idempotent. It ends the value now if idle, otherwise on the last lease.
   - `AsyncRenewing` uses an `asyncio.Lock` only around check-and-mint, and recreates it when the running loop changes.
   - **Renewal point:** 0.8 of the lifetime.
     - Client token: `t0 + 0.8*expires_in`, with `t0` taken before the send.
     - Key: `monotonic() + 0.8*(expires_at - time())`.
   - **The three uses:**
     - Static token: `Minted(token, inf)`.
     - Client token: `_issue`, with no end, because the contract cannot revoke one.
     - Impersonation: `_mint(xid)` / `_end_key(xid, key)`.
3. **Clients (`_sync.py`; `_aio.py` mirrors it)**
   - `_call(method, request, timeout, credential=None)`:
     1. Keep the closed check and the in-flight count.
     2. `with (credential or self._credential).lease() as bearer: return self._send(..., bearer)`.
   - Every refresh, mint and end therefore runs counted as in flight, and its failures come back typed.
   - `_issue()`:
     - calls `TokenService.IssueToken` directly;
     - on failure raises `from_rpc_error(exc) from None`, because grpc's frames hold the secret;
     - never logs the secret.
   - `_mint(xid)`:
     - sends ImpersonateExternalUser with `ttl_minutes=0` under the client token;
     - registers `_live[key_id] = xid` under `_state`;
     - async wraps the call and the registration in `asyncio.shield`, so a cancelled open still records its key.
   - `_end_key(xid, key)`:
     - sends EndImpersonation;
     - on success or NOT_FOUND, removes the key from `_live`;
     - if the client is closed, leaves the key for the sweep;
     - on any other error, logs a WARNING naming the key_id only;
     - never raises.
   - Async `_LazyStub` gains an attribute name, and `_open` builds the memory, admin and token stubs.
4. **Opening a session (`operations.py`)**
   - `MemoryOperations(stub, call, *, impersonate=None, known=None)`. The existing body moves to `_open_session(domain, timeout, release=None)`.
   - With `external_id`:
     1. Validate `domain` and `external_id` (blank sends nothing).
     2. Create `imp = impersonate(xid)`.
     3. Mint eagerly with `with imp.lease(): pass`.
     4. Build scoped ops, `MemoryOperations(stub, partial(call, credential=imp), known=self._known)`.
     5. `scoped.list_domains()`, which learns limits and deprecation under the key.
     6. `scoped._open_session(domain, timeout, release=imp.close)`.
     7. On any `BaseException`, `imp.close()` and re-raise.
   - StartSession, ListTools, every Session method, `session.tools()` and `Memory.feedback()` all reach the stub through `scoped`, so `agent.py` needs no change.
   - `AsyncSessionOpener` gains `external_id` and an `asyncio.Lock` around `_open`, so concurrent awaits mint one key. Its `__aexit__` closes what it opened.
5. **Construction and connect**
   - The health check runs first.
   - With client credentials, `with self._credential.lease(): pass` sends one IssueToken and no MemoryService call.
   - With a token, `list_domains` runs as today.
   - An authentication failure follows the existing path: ERROR log, close, raise.
   - Async `connect()` reuses a token that is not yet due.
6. **Client close**
   1. Set `_closed` and drain in-flight calls, as today.
   2. Snapshot and clear `_live`.
   3. EndImpersonation each key under a client-token lease. NOT_FOUND counts as ended. Stop at the first other failure and log one WARNING listing the key_ids left to expire.
   4. Close the channel.
   - Never raises.
   - Async reopens the channel on the current loop first if `_live` is not empty.
   - After client close, `session.close()` is a quiet no-op.
7. **Lock order:** session credential lock, then client-token lock, then `_state`, which is held only briefly.

## Files to change

**Export housekeeping (do first):**
- `python/pyproject.toml`:
  - coverage `omit`, ruff `extend-exclude`, mypy `exclude` (`^memcoai/(memory|admin|auth)/`) and the mypy overrides all gain admin and auth.
- `python/tests/test_provenance.py:18-28`: stop pinning `protos[0]`. Assert that the recorded paths equal `proto/**/*.proto` and that each sha256 matches.
- `scripts/verify_provenance.py`:
  - check every proto's checksum, not only `memory.proto` (`:28`, `:33`, `:37`);
  - require the admin and auth `_pb2`/`_pb2_grpc` files (`:219-224`).
- `go/memcoai/foundation_test.go:95-96`: stop asserting exactly one proto.
- Grep the nodejs tests for the same assertion.
- `tests/test_docstring_examples.py:33`: `GENERATED = {"memory","admin","auth"}`.
- `python/docs/conf.py` `nitpick_ignore`: add any `admin_pb2`/`auth_pb2` names that reach signatures.

**SDK:**
- `_auth.py`, `_channel.py`, `_config.py`, `_sync.py`, `_aio.py`: as described above.
- `operations.py`: `external_id`, `_open_session`, `release`, `close()`, the opener lock, and docstrings that no longer say "releases nothing".
- New `administration.py`: one line per method, `_convert.to_x(self._call(self._stub.X, _requests.x_request(...), timeout))`. Full Google docstrings with Examples using `client.networks.` / `client.users.`.
- `types.py`, `_convert.py` (plus `_to_instant`; `to_created_key` raises `MemcoInternalError` if the key is missing), `_requests.py` (issue token, impersonate, end, and the 19 admin builders), `_validate.py` (`check_strings`, `check_roles`, `check_aware`), `errors.py`.
- `__init__.py`: export `administration` and `MemcoAlreadyExistsError`; add client credentials and the env vars to the docstring.
- `scripts/sync_tool_docs.py`: add `external_id` to IGNORED, since it is not an agent-tool parameter.

**Tests (`python/tests/`):**
- `fake_server.py`:
  - a `_Recorder` base with `calls`, `received`, `metadata`, and `holds` (a `threading.Event` per method, to hold a call in flight);
  - `FakeTokenService`, which issues `client-token-{n}` with a configurable `expires_in`;
  - `FakeAdminService`, covering all 21 RPCs, with Impersonate returning `impersonation-{xid}-{n}` and `key-{n}`;
  - both registered in `Harness`.
- `conftest.py`: `credentialed` and `async_credentialed` fixtures; `_forget_the_construction_calls` also clears the new recorders.
- Update:
  - `test_client.py`: replace the `_merged` test with a wire test.
  - `test_agent.py`: `NOT_AN_OPERATION` gains `close`.
  - `test_parity.py`: an explicit `{"memory","networks","users"}` attribute map; the revert test covers memory only; the flat-call ban covers `administration`.
  - `test_docstring_examples.py`: `RECEIVERS` gains `client.networks` and `client.users`.
  - `test_config.py`: the new credential rules.
  - `test_errors.py`: ALREADY_EXISTS.
- New: `test_renewing.py`, `test_client_credentials.py`, `test_administration.py`, `test_impersonation.py`.

**System tests (`python/systemtest/`):**
- `conftest.py`:
  - `MEMCO_API_TLS=false` (read by the SDK itself; `tls=` wins over it, and a client without TLS logs a WARNING) turns TLS off for the local dev server at `localhost:50052`.
  - The memory client is built with an explicit `token=os.environ["MEMCO_API_TOKEN"]`. This is needed because CI now sets the client env vars too, which would otherwise win.
  - An `admin` client fixture, skipped without `MEMCO_CLIENT_ID`/`MEMCO_CLIENT_SECRET`.
  - `customer_network` and `external_user` fixtures with try/finally cleanup.
- New `test_admin.py`, mirroring the reference script:
  - root lookup via `list(parent_id="root", domain=...)`, then create a customer network;
  - network update, list and refusals;
  - groups, with PERMISSION_DENIED outside enterprise, or skip if there are none;
  - user CRUD: creator expands to reader, a duplicate raises `MemcoAlreadyExistsError`, `admin` is refused;
  - keys: create, list, delete, and a second delete gives NOT_FOUND;
  - member placement: a non-customer network is refused with `external_user_needs_customer_network`, and reclassifying is refused;
  - cleanup.
- New `test_impersonation.py`:
  - **sync:** search finds nothing; create_memory; poll search until the marker appears; close; a raw call with the ended key is UNAUTHENTICATED.
  - **async:** two users with two sessions under `asyncio.gather`, each writing and finding only its own marker, both keys ended on exit.
  - Confirm here that pages are 1-based and that End on an expired key is NOT_FOUND.

**Docs:**
- `python/README.md`:
  - Configuration rows for `client_id`, `client_secret`, `token_lifetime` and `MEMCO_CLIENT_ID`/`MEMCO_CLIENT_SECRET`, with the precedence rule.
  - New sections "Networks and users" and "Acting for your users". The latter covers impersonate→list→start, "close the session", auto-renewal, and an `asyncio.gather` example.
  - Contributing gains the generated trees.
- `python/docs/`:
  - new `administration.rst`, with a lead-in paragraph, added to the `index.rst` toctree;
  - `operations.rst`: the Sessions section covers `external_id`;
  - `clients.rst`: the construction intro;
  - `errors.rst`: picks up the new error automatically.
- Root `README.md`:
  - three contracts instead of just `memory.proto` in "Other languages";
  - a Python client-credentials and impersonation snippet (Go and Node.js later);
  - Repository layout lists the generated trees.
- `CONTRIBUTING.md` and the `python/Makefile` system-test comment: the new env vars and the local run command.

**CI (all three languages):**
- In `ci_{python,go,nodejs}_integration.yaml`, the job declares `environment: python` / `go` / `nodejs`. It reads `secrets.MEMCO_CLIENT_ID`/`MEMCO_CLIENT_SECRET` directly, because a called workflow cannot receive environment secrets from its caller, and passes them to the test step's env.
- Rewrite the "No `environment:`" comment: approval on PRs is accepted. A deployment-branch policy would fail PR refs (`refs/pull/N/merge`) outright, so the environments must not have one.
- Python guard:
  - the client pair is required alongside the token on non-PR runs;
  - a PR that cannot see the credentials skips.
- Go and Node.js guards are unchanged; the variables are passed through unused.

**Version 0.2.0:**
- `python/pyproject.toml:7`, plus the `python/uv.lock` memcoai entry, regenerated by `uv lock` via `make -C python install`.
- `nodejs/package.json:3` and `nodejs/package-lock.json:3,9`.
- `go/memcoai/version.go:5`.
- Grep the tests and release gates for pinned versions.

## Implementation order (TDD)

0. Save this plan to `plans/python-admin-auth.md`. Do the export housekeeping.
   - Verify: `test_provenance` fails first, then `make -C python lint`, `typecheck` and `test` are green. Also `go test ./...` via `make -C go test` and `python3 scripts/verify_provenance.py`.
1. Write all new and updated unit tests (steps 2-6 below) and confirm each fails for the right reason.
2. Credential config → `_config.py`.
3. The metadata seam: wire test with one authorization entry per call, none on health or IssueToken → `_auth.py`, `_channel.py`, `_send`.
4. `Renewing` / `AsyncRenewing`, with a patched clock:
   - single-flight across 8 threads or 8 gathered tasks;
   - renewal at 0.8;
   - a failed mint keeps the old value;
   - a retired value is ended after its last lease;
   - close semantics;
   - repr;
   - `asyncio.run()` twice.
5. Client credentials:
   - construction sends Check then IssueToken, with no memory calls;
   - the request fields and ttl are right, with no authorization;
   - UNAUTHENTICATED is typed, logged and closes the client;
   - the secret is absent from DEBUG logs and from the error chain;
   - the token refreshes;
   - env precedence.
6. Administration: a parametrised round trip for every method, sync and async; `HasField` for patches; local rejections send nothing; datetimes; `CreatedKey` repr; ALREADY_EXISTS, and `external_user_needs_customer_network` raising `MemcoPreconditionFailedError`.
7. Impersonation:
   - the order is Impersonate → ListDomains(key) → StartSession(key);
   - which calls carry the key;
   - failure paths send End;
   - close ends the key once, and later calls raise `MemcoConfigError`;
   - renewal with a held in-flight Search: `key-1` is ended only after it is released;
   - End failures;
   - client-close sweep;
   - the three async opener forms, concurrent awaits and cancellation;
   - crosstalk: `gather` over two users with 10 searches each, and two threads.
8. The guard suites: parity, docstring examples, `test_agent`, `make tool-docs-check`.
9. System tests locally: `MEMCO_API_TLS=false MEMCO_API_HOST=localhost:50052 MEMCO_CLIENT_ID=… MEMCO_CLIENT_SECRET=… MEMCO_API_TOKEN=… make -C python system-test`. Then against the live service.
10. Docs, CI and the version bump.

## Verification

Run these one at a time: `make -C python lint`, `make -C python typecheck`, `make -C python coverage` (floor ≥95), `make -C python docs` (`-W`, llms.txt), `make tool-docs-check`, `make provenance`, `make -C go test`, `make -C nodejs test`.

Then:
- run the local system test against the dev server on `localhost:50052`;
- run the fresh-agent review for quality, simplicity, clarity and security (AGENTS.md §5);
- report the results to the user.

**The user must create** `MEMCO_CLIENT_ID` / `MEMCO_CLIENT_SECRET` in the GitHub Environments `python`, `go` and `nodejs`, for an API client with the admin grant and the `user-management` and `network-management` scopes.
