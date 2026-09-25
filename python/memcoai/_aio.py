"""The asyncio Memco client."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Mapping
from functools import partial
from types import TracebackType
from typing import Any

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from memcoai.admin.v1 import admin_pb2_grpc as _admin_pbg
from memcoai.auth.v1 import auth_pb2_grpc as _auth_pbg
from memcoai.memory.v1 import memory_pb2_grpc as _pbg

from . import _auth, _requests
from ._auth import AsyncRenewing, Minted
from ._channel import build_async_channel
from ._config import DEFAULT_TIMEOUT, resolve
from ._config import deadline as _deadline
from ._logging import elapsed_ms, rpc_name, set_level
from ._provenance import provenance as _provenance
from .administration import AsyncNetworkOperations, AsyncUserOperations
from .errors import (
    MemcoAuthenticationError,
    MemcoConfigError,
    MemcoError,
    MemcoNotFoundError,
    MemcoUnhealthyError,
    from_rpc_error,
)
from .operations import AsyncMemoryOperations
from .types import Provenance

__all__ = ["AsyncMemco"]

# A child of the `memcoai` logger, like every other module in the package;
# see memcoai/_logging.py.
_log = logging.getLogger(__name__)


class _LazyStub:
    """Resolves to the real stub once the channel is open.

    The channel is built inside the running event loop rather than in
    ``__init__``, so the namespace is handed this stand-in and reaches the real
    stub through it on first use.
    """

    def __init__(self, client: AsyncMemco, attribute: str) -> None:
        """Bind to the client that owns the channel.

        Args:
            client: The client whose stub to resolve.
            attribute: The client attribute holding the real stub, which
                :meth:`AsyncMemco._open` sets.
        """
        self._client = client
        self._attribute = attribute

    def __getattr__(self, name: str) -> Any:
        """Return the named method from the real stub.

        Args:
            name: The RPC method name.

        Returns:
            The bound stub method.
        """
        # This stand-in exists to reach the owning client's channel; the
        # privacy it crosses is its own module's.
        self._client._open()  # noqa: SLF001
        return getattr(getattr(self._client, self._attribute), name)


class AsyncMemco:
    """Asyncio client for Memco Shared Memory.

    Mirrors :class:`~memcoai.Memco` method for method; only the awaiting
    differs. Because opening a connection requires I/O, the checks the
    synchronous client runs in ``__init__`` cannot run here: use it as an async
    context manager, or call :meth:`connect` yourself.

    Args:
        token: Credential to authenticate with, either a static Memco API key or
            a session token. When omitted, and the environment holds no client
            credentials, ``MEMCO_API_TOKEN`` is used, falling back to the
            deprecated ``MEMCO_API_KEY`` with a warning.
        host: Service endpoint, optionally including a port. When omitted,
            ``MEMCO_API_HOST`` is used, falling back to ``grpc.memco.ai``.
        client_id: An API client's id, passed with ``client_secret`` instead of
            a token. When neither they nor ``token`` is passed, and both
            ``MEMCO_CLIENT_ID`` and ``MEMCO_CLIENT_SECRET`` are set, those are
            used, even if ``MEMCO_API_TOKEN`` is set too.
        client_secret: The API client's secret, passed with ``client_id``. It
            is sent only to exchange for a token, and never logged.
        token_lifetime: Lifetime in seconds to ask for on each token issued for
            the client credentials. Omitted, the service's default applies. The
            service owns the maximum, and refuses a longer lifetime.
        tls: Whether to dial over TLS using the system trust store. When
            omitted, ``MEMCO_API_TLS`` decides -- ``true`` or ``false`` --
            falling back to ``True``. Without TLS, the client logs a
            ``WARNING`` as it is built.
        timeout: Default per-call deadline in seconds.
        env: Environment mapping to read defaults from. Defaults to
            :data:`os.environ`.
        log_level: The SDK's log level, as a name — ``"debug"``, ``"info"``,
            ``"warning"``, ``"error"``, ``"critical"``, or ``"none"`` to turn it
            off — or a :mod:`logging` constant. Defaults to ``"info"`` and
            overrides ``MEMCO_LOG``. Any level but ``"none"`` attaches the SDK's
            own stderr handler and stops the ``memcoai`` logger propagating, so
            records bypass the handlers the application configured; it is
            process-wide, since a logger is. An application with its own logging
            should pass ``"none"`` and set the level on the ``memcoai`` logger
            instead.

    Raises:
        MemcoConfigError: If no credential is available, a token is passed
            beside client credentials, only half of the client pair is given,
            ``token_lifetime`` is given without client credentials or is not
            positive, the host is unusable, ``MEMCO_API_TLS`` is neither
            ``true`` nor ``false``, or ``log_level`` is not a level this SDK
            accepts.

    Attributes:
        memory: The memory operations, as
            :class:`~memcoai.operations.AsyncMemoryOperations`.
        networks: The network administration, as
            :class:`~memcoai.administration.AsyncNetworkOperations`. Needs
            client credentials.
        users: The user administration, as
            :class:`~memcoai.administration.AsyncUserOperations`. Needs client
            credentials.

    Example:
        >>> async with AsyncMemco() as client:
        ...     session = await client.memory.start_session("coding")
        ...     result = await session.search("how does health checking work")
    """

    def __init__(
        self,
        token: str | None = None,
        host: str | None = None,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_lifetime: int | None = None,
        tls: bool | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        env: Mapping[str, str] | None = None,
        log_level: str | int | None = None,
    ) -> None:
        if log_level is not None:
            set_level(log_level)
        self._config = resolve(
            token,
            host,
            client_id=client_id,
            client_secret=client_secret,
            token_lifetime=token_lifetime,
            tls=tls,
            timeout=timeout,
            env=env,
        )
        if not self._config.tls:
            # Said at construction, before anything is sent, and at WARNING so
            # it shows by default: the credential crosses the wire readable.
            _log.warning(
                "TLS is off: %s is dialled in plaintext, credentials included",
                self._config.target,
            )
        self._closed = False
        # What close() started, which every later close() waits on too.
        self._closing: asyncio.Future[None] | None = None
        # Built lazily, never here. grpc.aio captures the running event loop at
        # channel construction, so a client created at module scope — or after
        # an earlier asyncio.run() returned — would bind to the wrong loop and
        # fail later with a cross-loop error, or raise a bare RuntimeError for
        # having no loop at all. Deferring binds it to the loop that awaits it.
        self._channel: Any = None
        self._stub: Any = None
        self._admin: Any = None
        self._tokens: Any = None
        self._health: Any = None
        # The loop the channel was built on. A channel cannot move between
        # loops, so a client reused across two asyncio.run() calls has to
        # rebuild rather than fail with a bare RuntimeError.
        self._loop: asyncio.AbstractEventLoop | None = None
        # close() waits for these rather than cancelling them: a cancellation
        # is a BaseException that neither `except MemcoError` nor
        # `except Exception` catches, and inside a gather it is
        # indistinguishable from the caller cancelling the task.
        self._inflight = 0
        self._idle = asyncio.Event()
        self._idle.set()
        # The client's own credential, leased by every call not made for an
        # impersonated session. A static token is never due; an issued one is
        # renewed through _issue. Neither has an end: nothing in the contract
        # revokes an issued token.
        if self._config.client_id is None:
            static = Minted(self._config.token, math.inf)

            async def held() -> Minted:
                return static

            self._credential = AsyncRenewing(held)
        else:
            self._credential = AsyncRenewing(self._issue)
        # The impersonation keys minted and not yet ended, key id to external
        # id, so close() can end those of sessions nobody closed.
        self._live: dict[str, str] = {}
        # Lazy for the client's own admin calls too: ending a key may be the
        # first call after the channel was dropped or its loop replaced.
        self._lazy_admin = _LazyStub(self, "_admin")
        self.memory = AsyncMemoryOperations(
            _LazyStub(self, "_stub"), self._call, impersonate=self._impersonate
        )
        """The memory operations. See :class:`~memcoai.operations.AsyncMemoryOperations`."""
        self.networks = AsyncNetworkOperations(self._lazy_admin, self._call)
        """The network administration.

        See :class:`~memcoai.administration.AsyncNetworkOperations`.
        """
        self.users = AsyncUserOperations(self._lazy_admin, self._call)
        """The user administration. See :class:`~memcoai.administration.AsyncUserOperations`."""

    # -- lifecycle --------------------------------------------------------

    def _open(self) -> None:
        """Open the channel if it is not open yet.

        Called from inside the running event loop, never from ``__init__``.

        Raises:
            MemcoConfigError: If the client has been closed.
        """
        if self._closed:
            raise MemcoConfigError("this client is closed; create a new one to make more calls")
        running = asyncio.get_running_loop()
        if self._channel is not None and self._loop is not running:
            # The loop it was bound to has gone. Drop the channel rather than
            # touch it: its transport belongs to a loop that is already closed.
            self._channel = None
            self._stub = None
            self._admin = None
            self._tokens = None
            self._health = None
        if self._channel is None:
            self._channel = build_async_channel(self._config)
            self._loop = running
            self._stub = _pbg.MemoryServiceStub(self._channel)
            self._admin = _admin_pbg.AdminServiceStub(self._channel)
            self._tokens = _auth_pbg.TokenServiceStub(self._channel)
            self._health = health_pb2_grpc.HealthStub(self._channel)
            self._idle = asyncio.Event()
            self._idle.set()

    async def connect(self) -> AsyncMemco:
        """Verify the connection, running the checks the constructor could not.

        Probes the health endpoint, then proves the credential. With a token
        that is :meth:`~memcoai.operations.AsyncMemoryOperations.list_domains`,
        which also teaches the client the input limits the service enforces.
        With client credentials it is the exchange for a token, and no memory
        method is called, since an issued token carries no content role.
        Calling this more than once repeats both, except that a token already
        issued is reused until it is due for renewal.

        Returns:
            This client.

        Raises:
            MemcoUnavailableError: If the service cannot be reached.
            MemcoUnhealthyError: If the service reports that it is not serving.
            MemcoAuthenticationError: If the credential is rejected.
            MemcoSunsetError: If what this client uses is past its sunset date.
        """
        self._open()
        try:
            await self._check_health()
            if self._config.client_id is None:
                # Discarding the result: what is worth keeping — the limits and
                # the per-domain tag cap — is retained by the call itself.
                await self.memory.list_domains()
            else:
                async with self._credential.lease():
                    pass
        except MemcoAuthenticationError:
            _log.error("credential rejected by %s", self._config.target)
            await self._reset()
            raise
        except BaseException:
            # Drop the channel but stay usable: a failed probe is often a
            # transient blip, and this method documents itself as repeatable.
            await self._reset()
            raise
        _log.info("connected to %s (tls=%s)", self._config.target, self._config.tls)
        return self

    async def _drain(self) -> None:
        """Wait for in-flight calls to finish.

        Closing while a call is in flight would cancel it, and a
        :class:`asyncio.CancelledError` is caught by neither ``except
        MemcoError`` nor ``except Exception`` — inside a gather it looks exactly
        like the caller cancelling the task.
        """
        if self._inflight:
            await self._idle.wait()

    async def _reset(self) -> None:
        """Tear the channel down without closing the client.

        Leaves the client able to open a fresh channel on the next call, which
        is what makes :meth:`connect` retryable after a transient failure.
        """
        if self._channel is not None:
            channel, self._channel = self._channel, None
            self._stub = None
            self._admin = None
            self._tokens = None
            self._health = None
            self._loop = None
            await self._drain()
            await channel.close(grace=None)

    async def close(self) -> None:
        """End the keys of impersonated sessions left open, then close the channel.

        Waits for any in-flight call to finish first, so no key is ended under
        a call still using it. Safe to call more than once, and never raises: a
        key that cannot be ended is logged at ``WARNING`` and expires on its
        own. After closing, any further call raises
        :class:`~memcoai.errors.MemcoConfigError`, and closing a session changes
        nothing. A close that is cancelled still finishes, and closing again
        waits for it to.
        """
        if self._closing is None:
            if self._live:
                # Ending them needs a channel on the running loop, which the
                # last one may not have been built on.
                self._open()
            self._closed = True
            self._closing = asyncio.ensure_future(self._shut_down())
        # Shielded: a close cancelled part-way -- a task group torn down
        # around it, a timeout on the way out -- would otherwise leave the keys
        # un-ended and the channel open, with every later close() returning at
        # once. A close already done, or cancelled with the loop it ran on, has
        # nothing left to wait for.
        if not self._closing.done():
            await asyncio.shield(self._closing)

    async def _shut_down(self) -> None:
        """Wait for calls in flight, end the keys still live, and close the channel.

        Runs once per client, as a task of its own, so that cancelling the
        :meth:`close` awaiting it does not stop it part-way. Never raises.
        """
        if self._channel is not None:
            await self._drain()
            live, self._live = self._live, {}
            await self._end_live(live)
            await self._channel.close(grace=None)
            self._channel = None
            self._loop = None
        _log.info("closed connection to %s", self._config.target)

    async def __aenter__(self) -> AsyncMemco:
        """Enter an async context manager, connecting and verifying.

        Returns:
            This client, once its connection checks have passed.
        """
        return await self.connect()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the client on leaving an async context manager."""
        await self.close()

    def provenance(self) -> Provenance:
        """Report which version of the contract this SDK was generated from.

        Returns:
            The provenance recorded when this package was built.

        Example:
            >>> client.provenance().server_commit  # the commit this wheel was built from
            '762721a87ab0...'
        """
        return _provenance()

    # -- internals --------------------------------------------------------

    async def _check_health(self) -> None:
        """Probe the standard gRPC health endpoint.

        Raises:
            MemcoUnavailableError: If the service cannot be reached.
            MemcoUnhealthyError: If it answers but is not serving.
        """
        request = health_pb2.HealthCheckRequest(service="")
        started = time.perf_counter()
        try:
            response = await self._health.Check(request, timeout=self._config.timeout)
        except grpc.RpcError as exc:
            raise from_rpc_error(exc) from exc
        if response.status != health_pb2.HealthCheckResponse.SERVING:
            name = health_pb2.HealthCheckResponse.ServingStatus.Name(response.status)
            raise MemcoUnhealthyError(
                grpc.StatusCode.UNAVAILABLE,
                f"{self._config.target} reported health status {name}",
            )
        _log.debug("health check on %s ok in %.0fms", self._config.target, elapsed_ms(started))

    async def _issue(self) -> Minted:
        """Exchange the API client's credentials for a token.

        Sent on the stub directly rather than through :meth:`_call`: it runs
        while the client's credential is minting, and it is the one call that
        carries no bearer, since the credentials in the request are what
        authenticate it. Always reached with the channel open.

        Returns:
            The token, due for renewal at :data:`~memcoai._auth.RENEW_AFTER` of
            its lifetime. The lifetime is counted from before the request was
            sent, so the renewal point can only fall early, never late.

        Raises:
            MemcoAPIError: If the exchange is refused or fails.
        """
        issued = _auth.monotonic()
        started = time.perf_counter()
        try:
            # Built inline rather than held in a local, so the secret it
            # carries is in no frame a traceback would capture.
            response = await self._tokens.IssueToken(
                _requests.issue_token_request(self._config), timeout=self._config.timeout
            )
        except grpc.RpcError as exc:
            error = from_rpc_error(exc)
            _log.debug("IssueToken failed in %.0fms: %s", elapsed_ms(started), type(error).__name__)
        else:
            _log.debug("IssueToken ok in %.0fms", elapsed_ms(started))
            return Minted(response.access_token, issued + _auth.RENEW_AFTER * response.expires_in)
        # Raised outside the handler, so grpc's error is neither this one's
        # cause nor its context: grpc's frames hold the request, and the
        # request holds the secret.
        raise error

    def _impersonate(self, external_id: str) -> AsyncRenewing:
        """Hold the key an impersonated session acts for one external user with.

        Args:
            external_id: The user the session acts as.

        Returns:
            The session's credential, which mints its first key on first use.
        """
        return AsyncRenewing(partial(self._mint, external_id), partial(self._end_key, external_id))

    async def _mint(self, external_id: str) -> Minted:
        """Mint a key acting as an external user, under the client's own credential.

        The key is registered before it is returned, so :meth:`close` can end
        it even if the session it was minted for is never closed.

        Args:
            external_id: The user the key acts as.

        Returns:
            The key, due for renewal at :data:`~memcoai._auth.RENEW_AFTER` of the
            lifetime left on it when it arrived.

        Raises:
            MemcoAPIError: If the service refuses to mint it.
        """
        # Not cancelled with an open that is: the session's credential runs
        # the mint shielded, and ends the key if nothing is left to take it.
        key = await self._call(
            self._lazy_admin.ImpersonateExternalUser,
            _requests.impersonate_request(external_id),
            None,
        )
        self._live[key.key_id] = external_id
        # expires_at is absolute Unix time; converted once, on receipt, into
        # the monotonic reading every renewal decision is made against.
        left = key.expires_at - _auth.time()
        return Minted(key.value, _auth.monotonic() + _auth.RENEW_AFTER * left, key_id=key.key_id)

    async def _end_key(self, external_id: str, key: Minted) -> None:
        """End a key a session has renewed or closed. Never raises.

        Args:
            external_id: The user the key acts as.
            key: The key to end.
        """
        try:
            await self._call(
                self._lazy_admin.EndImpersonation,
                _requests.end_impersonation_request(external_id, key.key_id),
                None,
            )
        except MemcoNotFoundError:
            pass  # Already expired, or its user deleted: nothing is left to end.
        except MemcoConfigError:
            return  # The client is closed, and its close() ends what is left.
        except MemcoError as exc:
            # Left registered, so closing the client tries again. The key id
            # revokes nothing, so it is safe to log; the value never is.
            _log.warning(
                "could not end impersonation key %s; closing the client tries again: %s",
                key.key_id,
                exc,
            )
            return
        self._live.pop(key.key_id, None)

    async def _end_live(self, live: dict[str, str]) -> None:
        """End the keys still live as the client closes. Never raises.

        Stops at the first failure: one refusal says the service cannot end
        them now, and asking again for each would only make closing slower.
        The keys left expire on their own.

        Args:
            live: The keys to end, key id to external id.
        """
        deadline = _deadline(None, self._config.timeout)
        key_ids = list(live)
        for ended, key_id in enumerate(key_ids):
            try:
                # Sent directly, on the channel close() made sure of: _call and
                # the lazy stub both refuse every call once the client is
                # closed, and nothing else can be in flight by now.
                async with self._credential.lease() as bearer:
                    await self._send(
                        self._admin.EndImpersonation,
                        _requests.end_impersonation_request(live[key_id], key_id),
                        deadline,
                        bearer,
                    )
            except MemcoNotFoundError:
                pass
            except MemcoError as exc:
                _log.warning(
                    "could not end impersonation keys %s; they expire on their own: %s",
                    ", ".join(key_ids[ended:]),
                    exc,
                )
                return

    async def _call(
        self,
        method: Any,
        request: Any,
        timeout: float | None,
        credential: AsyncRenewing | None = None,
    ) -> Any:
        """Invoke one RPC under a leased credential.

        The lease is taken inside the in-flight count, so a token refresh or a
        key renewal it sets off is waited for by :meth:`close`, and its failure
        reaches the caller typed like any other.

        Args:
            method: The stub method to call.
            request: The request message.
            timeout: Per-call deadline, or ``None`` to use the client default.
            credential: The credential to send, or ``None`` for the client's
                own. An impersonated session passes its key.

        Returns:
            The response message.

        Raises:
            MemcoConfigError: If the client, or the credential, has been closed.
            MemcoAPIError: If the service returned an error status, or the
                credential could not be renewed.
        """
        self._open()
        deadline = _deadline(timeout, self._config.timeout)
        self._inflight += 1
        self._idle.clear()
        try:
            async with (credential or self._credential).lease() as bearer:
                return await self._send(method, request, deadline, bearer)
        finally:
            self._inflight -= 1
            if not self._inflight:
                self._idle.set()

    async def _send(self, method: Any, request: Any, deadline: float, bearer: Minted) -> Any:
        """Send one RPC carrying one credential, translating any failure.

        Args:
            method: The stub method to call.
            request: The request message.
            deadline: The call's deadline in seconds.
            bearer: The credential to send, as the call's only one. Passed
                whole rather than as its value, and read only inline, so the
                bearer is in no local of this frame: an error raised here
                carries the frame, and an error tracker ships its locals.

        Returns:
            The response message.

        Raises:
            MemcoConfigError: If the client was closed while the call was sent.
            MemcoAPIError: If the service returned an error status.
        """
        started = time.perf_counter()
        try:
            response = await method(
                request, timeout=deadline, metadata=_auth.metadata(bearer.value)
            )
        except grpc.RpcError as exc:
            # Translated once, so the record names the error the caller will
            # see and the translation cannot fail differently the second time.
            error: MemcoError = from_rpc_error(exc)
            _log.debug(
                "%s failed in %.0fms: %s",
                rpc_name(request),
                elapsed_ms(started),
                type(error).__name__,
            )
        except grpc.aio.UsageError:
            # close() flips the flag and then tears the channel down, so a call
            # that passed the check above can still land on a dead channel.
            # UsageError is not an RpcError, so it would otherwise escape raw.
            error = MemcoConfigError("this client is closed; create a new one to make more calls")
        else:
            _log.debug("%s ok in %.0fms", rpc_name(request), elapsed_ms(started))
            return response
        # Raised outside the handler, so grpc's error is neither this one's
        # cause nor its context: grpc's frames hold the metadata the call was
        # sent with, and with it the bearer, which an error tracker capturing
        # locals would ship.
        raise error
