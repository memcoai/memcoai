"""The synchronous Memco client."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from functools import partial
from types import TracebackType
from typing import Any, TypeVar

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from memcoai.admin.v1 import admin_pb2_grpc as _admin_pbg
from memcoai.auth.v1 import auth_pb2_grpc as _auth_pbg
from memcoai.memory.v1 import memory_pb2_grpc as _pbg

from . import _auth, _requests
from ._auth import Minted, Renewing
from ._channel import build_channel
from ._config import DEFAULT_TIMEOUT, resolve
from ._config import deadline as _deadline
from ._logging import elapsed_ms, rpc_name, set_level
from ._provenance import provenance as _provenance
from .administration import NetworkOperations, UserOperations
from .errors import (
    MemcoAuthenticationError,
    MemcoConfigError,
    MemcoError,
    MemcoNotFoundError,
    MemcoUnhealthyError,
    from_rpc_error,
)
from .operations import MemoryOperations
from .types import Provenance

_T = TypeVar("_T")

__all__ = ["Memco"]

# A child of the `memcoai` logger, so the one name a caller configures governs
# every record the SDK writes, while still allowing this module to be
# quietened on its own. See memcoai/_logging.py for the tree and its handlers.
_log = logging.getLogger(__name__)


class Memco:
    """Synchronous client for Memco Shared Memory.

    Opens one gRPC channel and holds it until closed, so a single client should
    be created once and reused. It is safe to share between threads.

    Construction makes two calls. The first is the standard gRPC health
    endpoint, which is unauthenticated: it proves the host, port and TLS
    settings are sound. The second proves the credential, so a bad one fails
    here rather than on the first real call. With a token, it is
    :meth:`~memcoai.operations.MemoryOperations.list_domains`, which also
    reports the input limits the service enforces. The client keeps those, and
    from then on refuses an oversized field locally instead of spending a round
    trip on a call the service would refuse.

    With an API client's ``client_id`` and ``client_secret`` instead, the second
    call exchanges them for a token, and no memory method is called: an issued
    token carries no content role of its own, so the service would refuse one.
    The client renews the token by itself, before it expires.

    Connecting and closing are reported to the ``memcoai`` logger at ``INFO``,
    and a rejected credential at ``ERROR`` before it is raised, since a client
    is often built somewhere the traceback does not reach. The credential itself
    is never logged, at any level.

    Args:
        token: Credential to authenticate with, either a static Memco API key or
            a session token. When omitted, and the environment holds no client
            credentials, ``MEMCO_API_TOKEN`` is used, falling back to the
            deprecated ``MEMCO_API_KEY`` with a warning.
        host: Service endpoint, optionally including a port such as
            ``localhost:50051``. When omitted, ``MEMCO_API_HOST`` is used,
            falling back to ``grpc.memco.ai``. Port 443 is assumed when the
            host carries none.
        client_id: An API client's id, passed with ``client_secret`` instead of
            a token. When neither they nor ``token`` is passed, and both
            ``MEMCO_CLIENT_ID`` and ``MEMCO_CLIENT_SECRET`` are set, those are
            used, even if ``MEMCO_API_TOKEN`` is set too.
        client_secret: The API client's secret, passed with ``client_id``. It
            is sent only to exchange for a token, and never logged.
        token_lifetime: Lifetime in seconds to ask for on each token issued for
            the client credentials. Omitted, the service's default applies. The
            service owns the maximum, and refuses a longer lifetime.
        tls: Whether to dial over TLS using the system trust store. Set to
            ``False`` only for a plaintext endpoint such as a local server.
            When omitted, ``MEMCO_API_TLS`` decides -- ``true`` or ``false`` --
            falling back to ``True``. Without TLS, the client logs a
            ``WARNING`` as it is built.
        timeout: Default per-call deadline in seconds. Individual methods can
            override it.
        env: Environment mapping to read defaults from. Defaults to
            :data:`os.environ`; supplying one is mainly useful in tests.
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
        MemcoUnavailableError: If the service cannot be reached.
        MemcoUnhealthyError: If the service reports that it is not serving.
        MemcoAuthenticationError: If the credential is rejected.
        MemcoSunsetError: If what this client uses is past its sunset date.

    Attributes:
        memory: The memory operations, as
            :class:`~memcoai.operations.MemoryOperations`.
        networks: The network administration, as
            :class:`~memcoai.administration.NetworkOperations`. Needs client
            credentials.
        users: The user administration, as
            :class:`~memcoai.administration.UserOperations`. Needs client
            credentials.

    Example:
        >>> with Memco() as client:
        ...     session = client.memory.start_session("coding")
        ...     result = session.search("how does gRPC health checking work")
        ...     for memory in result.memories:
        ...         print(memory.idx, len(memory.insights))
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
        # Construction goes on to reach the service, and any failure there
        # carries this frame in its traceback, which an error tracker capturing
        # locals ships. The config holds the token and the secret out of its
        # repr; these names would not.
        del token, client_secret
        self._channel = build_channel(self._config)
        self._health = health_pb2_grpc.HealthStub(self._channel)
        self._tokens = _auth_pbg.TokenServiceStub(self._channel)
        self._admin = _admin_pbg.AdminServiceStub(self._channel)
        # The client's own credential, leased by every call not made for an
        # impersonated session. A static token is never due; an issued one is
        # renewed through _issue. Neither has an end: nothing in the contract
        # revokes an issued token.
        if self._config.client_id is None:
            static = Minted(self._config.token, math.inf)
            self._credential = Renewing(lambda: static)
        else:
            self._credential = Renewing(self._issue)
        self._closed = False
        # close() must not destroy the channel while a call is in flight: grpc
        # registers a call handle per method, and invoking one that was not
        # already warmed dereferences the destroyed channel and takes the
        # process down. A pre-check cannot prevent that, because the crash
        # happens inside the call rather than as an exception.
        self._state = threading.Condition()
        self._inflight = 0
        # The impersonation keys minted and not yet ended, key id to external
        # id, so close() can end those of sessions nobody closed. Guarded by
        # _state.
        self._live: dict[str, str] = {}
        self.memory = MemoryOperations(
            _pbg.MemoryServiceStub(self._channel), self._call, impersonate=self._impersonate
        )
        """The memory operations. See :class:`~memcoai.operations.MemoryOperations`."""
        self.networks = NetworkOperations(self._admin, self._call)
        """The network administration. See :class:`~memcoai.administration.NetworkOperations`."""
        self.users = UserOperations(self._admin, self._call)
        """The user administration. See :class:`~memcoai.administration.UserOperations`."""
        try:
            self._check_health()
            if self._config.client_id is None:
                # Discarding the result: what is worth keeping — the limits and
                # the per-domain tag cap — is retained by the call itself.
                self.memory.list_domains()
            else:
                # Issuing the token is what proves the credentials. A
                # ListDomains under it would only be refused: an issued token
                # carries no content role.
                with self._credential.lease():
                    pass
        except MemcoAuthenticationError:
            _log.error("credential rejected by %s", self._config.target)
            self.close()
            raise
        except BaseException:
            self.close()
            raise
        _log.info("connected to %s (tls=%s)", self._config.target, self._config.tls)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """End the keys of impersonated sessions left open, then close the channel.

        Blocks until any in-flight call has finished, so a client shared between
        threads can be closed from one of them safely, and so no key is ended
        under a call still using it. Safe to call more than once, and never
        raises: a key that cannot be ended is logged at ``WARNING`` and expires
        on its own. After closing, any further call raises
        :class:`~memcoai.errors.MemcoConfigError`, and closing a session changes
        nothing.
        """
        with self._state:
            if self._closed:
                return
            self._closed = True
            while self._inflight:
                self._state.wait()
            live, self._live = self._live, {}
        self._end_live(live)
        self._channel.close()
        _log.info("closed connection to %s", self._config.target)

    def __enter__(self) -> Memco:
        """Enter a context manager.

        Returns:
            This client.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the client on leaving a context manager."""
        self.close()

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

    def _check_health(self) -> None:
        """Probe the standard gRPC health endpoint.

        Raises:
            MemcoUnavailableError: If the service cannot be reached.
            MemcoUnhealthyError: If it answers but is not serving.
        """
        request = health_pb2.HealthCheckRequest(service="")
        started = time.perf_counter()
        try:
            response = self._health.Check(request, timeout=self._config.timeout)
        except grpc.RpcError as exc:
            raise from_rpc_error(exc) from exc
        if response.status != health_pb2.HealthCheckResponse.SERVING:
            name = health_pb2.HealthCheckResponse.ServingStatus.Name(response.status)
            raise MemcoUnhealthyError(
                grpc.StatusCode.UNAVAILABLE,
                f"{self._config.target} reported health status {name}",
            )
        _log.debug("health check on %s ok in %.0fms", self._config.target, elapsed_ms(started))

    def _issue(self) -> Minted:
        """Exchange the API client's credentials for a token.

        Sent on the stub directly rather than through :meth:`_call`: it runs
        while the client's credential is minting, and it is the one call that
        carries no bearer, since the credentials in the request are what
        authenticate it.

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
            response = self._tokens.IssueToken(
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

    def _impersonate(self, external_id: str) -> Renewing:
        """Hold the key an impersonated session acts for one external user with.

        Args:
            external_id: The user the session acts as.

        Returns:
            The session's credential, which mints its first key on first use.
        """
        return Renewing(partial(self._mint, external_id), partial(self._end_key, external_id))

    def _mint(self, external_id: str) -> Minted:
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
        key = self._call(
            self._admin.ImpersonateExternalUser, _requests.impersonate_request(external_id), None
        )
        with self._state:
            self._live[key.key_id] = external_id
        # expires_at is absolute Unix time; converted once, on receipt, into
        # the monotonic reading every renewal decision is made against.
        left = key.expires_at - _auth.time()
        return Minted(key.value, _auth.monotonic() + _auth.RENEW_AFTER * left, key_id=key.key_id)

    def _end_key(self, external_id: str, key: Minted) -> None:
        """End a key a session has renewed or closed. Never raises.

        Args:
            external_id: The user the key acts as.
            key: The key to end.
        """
        try:
            self._call(
                self._admin.EndImpersonation,
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
        with self._state:
            self._live.pop(key.key_id, None)

    def _end_live(self, live: dict[str, str]) -> None:
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
                # Sent directly: _call refuses every call once the client is
                # closed, and nothing else can be in flight by now.
                with self._credential.lease() as bearer:
                    self._send(
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

    def _call(
        self,
        method: Callable[..., _T],
        request: Any,
        timeout: float | None,
        credential: Renewing | None = None,
    ) -> _T:
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
        deadline = _deadline(timeout, self._config.timeout)
        with self._state:
            if self._closed:
                raise MemcoConfigError("this client is closed; create a new one to make more calls")
            self._inflight += 1
        try:
            with (credential or self._credential).lease() as bearer:
                return self._send(method, request, deadline, bearer)
        finally:
            with self._state:
                self._inflight -= 1
                if self._closed and not self._inflight:
                    self._state.notify_all()

    def _send(self, method: Callable[..., _T], request: Any, deadline: float, bearer: Minted) -> _T:
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
            MemcoAPIError: If the service returned an error status.
        """
        started = time.perf_counter()
        try:
            response = method(request, timeout=deadline, metadata=_auth.metadata(bearer.value))
        except grpc.RpcError as exc:
            # Translated once, so the record names the error the caller will
            # see and the translation cannot fail differently the second time.
            error = from_rpc_error(exc)
            _log.debug(
                "%s failed in %.0fms: %s",
                rpc_name(request),
                elapsed_ms(started),
                type(error).__name__,
            )
        else:
            _log.debug("%s ok in %.0fms", rpc_name(request), elapsed_ms(started))
            return response
        # Raised outside the handler, so grpc's error is neither this one's
        # cause nor its context: grpc's frames hold the metadata the call was
        # sent with, and with it the bearer, which an error tracker capturing
        # locals would ship.
        raise error
