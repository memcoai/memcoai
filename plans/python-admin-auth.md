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
  - a static token holding characters a request header cannot carry (non-ASCII, a lone surrogate, a control character), named without its value;
  - `token_lifetime` without client credentials, not a whole number (a float, a bool), or ≤ 0.
- The constructors re-raise a `MemcoConfigError` from resolution without the frames of `resolve()`, and only after deleting their own `token` and `client_secret` names, since each frame holds the secret.
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

**`errors.py`:** add `MemcoAlreadyExistsError(MemcoAPIError)`, mapped from `ALREADY_EXISTS`. `MemcoSunsetError` carries the ErrorInfo `metadata` like every other precondition failure, and keeps it through pickling and copying.

## Credential design

1. **Per-call bearer (`_auth.py`, `_channel.py`)**
   - Replace the fixed-token interceptors with explicit `metadata=` on each call. Delete `AuthInterceptor`, `AsyncAuthInterceptor`, `_ClientCallDetails`, `_merged` and `UNAUTHENTICATED_PREFIX`. Keep `AUTH_HEADER` and `AUTH_SCHEME`, and add `metadata(token)`.
   - Channels are built without interceptors, and the retry config is unchanged.
   - Every stub call goes through the client's `_send(method, request, deadline, bearer)`, where `bearer` is the leased `Minted` and `.value` is read only inline in `metadata=`, so no frame an error carries holds the bearer as a plain string. Health and IssueToken are sent without metadata, and any stray stub call carries no credential, so the design fails closed.
   - This makes the credential an argument, not ambient state, which is what rules out crosstalk between concurrent sessions.
2. **`Renewing` / `AsyncRenewing` (private, `_auth.py`)**
   - `Minted` holds `value` (repr hidden), `renew_at` and `expires` (both monotonic), `key_id`, `users` and `retired`.
   - `lease(timeout=None)` is a context manager that yields the `Minted` itself (its repr hides the value):
     - raise `MemcoConfigError` if closed;
     - a value before `renew_at` is leased at once;
     - past `renew_at`, a renewal is started unless one is in flight (single-flight), and **runs apart from every call** (user decision): on a thread of its own (sync, not a daemon, counted in flight by the client) or a detached task (async);
     - while the value held is before its `expires`, it is leased at once, so a slow or failing renewal holds up no call;
     - only a lease with nothing honoured -- none yet, or expired -- waits for the mint in flight, bounded by `timeout`, which `_call` passes as the call's own deadline. Giving up raises `MemcoTimeoutError`, and the mint runs on to its own deadline (the client default) regardless. The sync wait is a `Condition.wait` capped at `threading.TIMEOUT_MAX`. The async one is `asyncio.wait`, which never cancels the mint and never swallows the lease's own cancellation, unlike `asyncio.wait_for` before 3.12;
     - `users += 1`.
   - When a mint lands it is held, and the value it replaces is retired: ended at once, by the mint's worker, if idle, else by its last lease. **That end runs apart from the call too** (a thread or a detached task), so no lessee waits for EndImpersonation; it is still exactly once and after the last lease. A renewal therefore never revokes a key under an in-flight call, and there is one live key per session.
   - A failed mint keeps the old value. **While that value is before its `expires`, it stays in use** (user decision): the renewal point is early precisely so that a failed renewal fails no call. The mint logs one WARNING per failed attempt, naming the key id (or "the client token") and never the value; a client that closed meanwhile is not reported. Only once the value has expired, or when there is none, do the leases waiting on the mint raise the typed error. The next lease past `renew_at` tries renewing again.
   - Failure is single-flight too: every lease waiting on one mint shares its outcome -- the fresh value, or a copy of the failure (a copy, so no two callers share a traceback) -- instead of minting again in turn. A lease that begins after the failure starts a new attempt.
   - `close()` is idempotent. It ends the value now if idle, otherwise on the last lease, and never waits for a mint in flight: what that produces is ended as it lands. So an open that gave up on a slow mint returns at its own deadline, and the key is still ended once it arrives.
   - On the `expires_at` fallback only, a key the local wall clock already reads as expired when it arrives -- a host clock well ahead of the service's -- is refused with a `MemcoConfigError` naming the clock, never "closed"; the key is ended. A key carrying `expires_in` cannot hit this.
   - `AsyncRenewing` needs no lock: nothing is awaited between reading its state and changing it. A mint left on another loop -- one closed without cancelling it, or not running -- is abandoned and a fresh one started; `detach` lets go of tasks whose loop has closed. Every mint and every end runs as a task of its own, held in the module-level set `_auth.detach` keeps (the loop holds tasks only weakly, and the holder may be collected first). `close()` awaits its end through `asyncio.shield`, so a cancelled caller, anyio's level-triggered cancellation included, cannot abort EndImpersonation part-way.
   - **Dropped holders:** an optional `dropped(key_id)` hook. A `weakref.finalize` watches the holder, carrying the key id of the value held (only the id, so no value sits in the global finalize registry). It is re-pointed on each renewal, detached by `close()`, and never run at interpreter exit. If the holder is garbage-collected unclosed, the finalizer calls `dropped` from the collector, which may be on any thread and under any lock, so the hook must not send or lock.
   - **Renewal point:** 0.8 of the lifetime; **expiry:** the whole of it.
     - Client token: `t0 + 0.8*expires_in` and `t0 + expires_in`, with `t0` taken before the send.
     - Key (`_auth.key_lifetime`): with `ImpersonationKey.expires_in` (the seconds left by the service's own count; 0 from a service that does not send it yet, or once expired), `t0 + 0.8*expires_in` and `t0 + expires_in`, with `t0` the monotonic reading taken before ImpersonateExternalUser was sent, exactly as the client token is timed: no wall clock, so a skewed host clock does not matter. Without it, the fallback: `monotonic() + 0.8*left` and `monotonic() + left`, where `left = expires_at - time()`, converted once on receipt. The `_live` registry's expiry is the same either way.
   - **The three uses:**
     - Static token: `Minted(token, inf)`.
     - Client token: `_issue`, with no end, because the contract cannot revoke one.
     - Impersonation: `_mint(xid)`, an end of `_end_key(xid, key.key_id)`, and `dropped=partial(client._dropped, xid)`.
3. **Clients (`_sync.py`; `_aio.py` mirrors it)**
   - `_call(method, request, timeout, credential=None)`:
     1. Keep the closed check and the in-flight count (sync: the `_counted()` context manager).
     2. If any dropped session's key is queued, start ending those, without waiting. The queue is drained and every key claimed as under way before the call goes on. Sync ends them in turn on a thread of its own, stopping at the first the service refuses; async starts each as a detached task. Their failures are logged, never raised.
     3. `with (credential or self._credential).lease(deadline) as bearer: return self._send(..., ends - now, bearer)`.
   - **One end-to-end deadline per call** (user decision): the call's deadline D -- its `timeout`, else the client default -- is fixed as the call starts (`ends = perf_counter() + D`) and covers both the wait for a credential and the request. The wait may use as much of D as it needs; the request gets whatever remains, not a fresh D. With a credential to hand it gets all of D. Exceeding D anywhere raises `MemcoTimeoutError`, and a mint still in flight carries on in the background. Methods that send several requests -- `start_session` and `with_session`, and `import_memories` when a batch is above the service's cap per request -- keep a deadline per request for now.
   - Every mint and end calls the service through `_call`, so it is counted in flight while it does; a failure a lease waits on comes back typed. The sync token exchange counts itself (see `_issue`); the async one is not counted, and a close cuts it short.
   - `_live` maps key id to `Live(external_id, expires, failed)`: `expires` is the monotonic reading at which the service stops honouring the key, and `failed` marks a key whose end was refused.
   - **Work left on a loop the async client has left** (a loop closed without cancelling its tasks, or `asyncio.run` torn down with a call in flight) can never finish. `_settle()`, run by `_open()` and `close()` on the first use of a new loop, restarts the in-flight count, and marks the `_ending` entries still pending failed, so the next mint for their user retries them there. An end cancelled with its loop marks its key failed itself; a mint cut off by its loop logs a WARNING that a key may be left live until it expires (its id never arrived).
   - Sync `_send` re-raises anything but `grpc.RpcError` -- a signal handler's exception while grpc blocks -- without grpc's frames, whose locals hold the metadata.
   - `_ending` holds the ends under way, by key id: sync maps it to the external id, async to the external id and the task. A dropped-key reap claims all its keys as it drains the queue, under the same lock (sync), and releases and marks them if its thread cannot start; a failed-end retry claims its key as it takes it, and every `_end_key` claims its own. **A mint waits for every end under way for its user before sending ImpersonateExternalUser**, since the service counts each against the user's cap until it has ended. This covers a mint racing a reap on another call or thread, and a session closing while another opens for the same user.
   - `_issue()`:
     - calls `TokenService.IssueToken` directly, on the renewal's thread or task;
     - sync: counts itself in flight, and is allowed while the client closes (the sweep may need a fresh token) but refused once the channel is shut. `close()` waits for in-flight calls again after the sweep, then marks the channel shut, then closes it. Async: reopens a dropped channel unless closing, and maps `UsageError` to `MemcoConfigError`;
     - on failure raises `from_rpc_error(exc) from None`, because grpc's frames hold the secret;
     - never logs the secret.
   - `_mint(xid)`:
     - runs on the renewal's thread or task. Sync counts the whole of it in flight, so registering the key cannot fall after `close()`'s snapshot;
     - first prunes: drops every `_live` entry past its expiry, then retries the end of this user's `failed` keys, stopping at the first the service still refuses. This bounds the registry on a long-lived client, and frees a key under the user's cap before the new one is asked for;
     - sends ImpersonateExternalUser with `ttl_minutes=0` under the client token;
     - registers `_live[key_id] = Live(xid, expires)` under `_state`;
     - a cancelled open does not stop it, being the renewal's own task, so the key is still recorded, and held or ended.
   - `_end_key(xid, key_id)`: sync returns a bool; async starts the end as a detached task, registered in `_ending` at once, and returns the task, whose result is that bool:
     - sends EndImpersonation;
     - on success or NOT_FOUND, removes the key from `_live`. Sync counts itself in flight across the send and the removal, so a racing `close()` cannot snapshot the key in between and end it twice;
     - if the client is closed, leaves the key for the sweep;
     - on any other error, marks the entry `failed` and logs a WARNING naming the key_id only;
     - never raises; returns whether nothing is left to end.
   - **Dropped sessions (user decision):** each session credential's `dropped` hook is the client's `_dropped`. Once the client is closed it does nothing, because the close sweep ended the key. Otherwise it calls `_auth.orphaned`, which appends `(xid, key_id)` to the client's `_orphans` deque and then issues a `ResourceWarning` naming the key id, as an unclosed file does. It sends nothing. The next `_call`, or the next mint, drains the whole queue before ending any key, so the ends do not recurse. Sync ends them in turn on a worker thread, stopping at the first key the service will not end; however that loop stops, the keys left are unclaimed and marked `failed` for the next mint for their user to retry, so no interrupt can strand a claim. Async sends them all at once and marks each refused one. Explicit `close()` stays the documented way; the docstrings and the README say a dropped session is ended only eventually.
   - Async `_LazyStub` gains an attribute name, and `_open` builds the memory, admin and token stubs.
4. **Opening a session (`operations.py`)**
   - `MemoryOperations(stub, call, *, impersonate=None, known=None)`. The existing body moves to `_open_session(domain, timeout, release=None)`.
   - With `external_id`:
     1. Validate `domain` and `external_id` (blank sends nothing).
     2. Create `imp = impersonate(xid)`.
     3. (The first lease, below, mints the key.)
     4. Build scoped ops, `MemoryOperations(stub, partial(call, credential=imp), known=self._known)`.
     5. `scoped.list_domains()`, which learns limits and deprecation under the key.
     6. `scoped._open_session(domain, timeout, release=imp.close)`.
     7. On any `BaseException`, `imp.close()` and re-raise. The close never waits for a mint still in flight, so an open whose mint outlasts its `timeout` raises `MemcoTimeoutError` at that deadline, and the key is ended once it arrives.
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
   3. EndImpersonation each key not yet past its expiry, under a client-token lease; an expired key is skipped, since the service no longer honours it. NOT_FOUND counts as ended. Stop at the first other failure and log one WARNING listing the key_ids left to expire.
   4. Sync: wait for in-flight work again -- a token exchange the sweep set off -- then mark the channel shut, so nothing more is sent, and only then close it; closing a sync grpc channel under a call can take the process down. Async closes it at once, cutting a token exchange in flight short.
   - Sync `close()` is resumable: one runs at a time under a lock, the keys swept are kept on the client until each is dealt with, and only a close that ran to the end makes later ones return at once. A close cut short -- a signal handler raising in it -- is finished by the next.
   - Never raises.
   - Async reopens the channel on the current loop first if `_live` is not empty.
   - After client close, `session.close()` is a quiet no-op.
7. **Locks:** no lock is held across an RPC. A sync credential's `Condition` guards its own state and is released before any mint or end runs; the client's `_state` is taken only briefly, never while holding a credential's `Condition`, except by a mint's `Condition.wait` for its user's ends, which releases it. The async holders need no lock.

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
