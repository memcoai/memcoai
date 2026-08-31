"""The synchronous Memco client."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, TypeVar

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from memco.memory.v1 import memory_pb2_grpc as _pbg

from ._channel import build_channel
from ._config import DEFAULT_TIMEOUT, resolve
from ._config import deadline as _deadline
from ._provenance import provenance as _provenance
from .errors import (
    MemcoAuthenticationError,
    MemcoConfigError,
    MemcoUnhealthyError,
    from_rpc_error,
)
from .operations import MemoryOperations
from .types import Provenance

_T = TypeVar("_T")

__all__ = ["Memco"]

# Named for the package rather than the module, so the logger a caller
# configures is the one the SDK writes to. No handler is attached: choosing
# where records go is the application's call, not a library's.
_log = logging.getLogger("memco")


class Memco:
    """Synchronous client for Memco Shared Memory.

    Opens one gRPC channel and holds it until closed, so a single client should
    be created once and reused. It is safe to share between threads.

    Construction makes two calls. The first is the standard gRPC health
    endpoint, which is unauthenticated: it proves the host, port and TLS
    settings are sound. The second is
    :meth:`~memco.operations.MemoryOperations.describe_domains`, which carries
    the credential — so a bad token fails here rather than on the first real
    call — and reports the input limits the service enforces. The client keeps
    those, and from then on refuses an oversized field locally instead of
    spending a round trip on a call the service would refuse.

    A rejected credential is written to the ``memco`` logger before it is
    raised, since a client is often built somewhere the traceback does not
    reach. Nothing is logged on the way past otherwise, and the credential
    itself never is.

    Args:
        token: Credential to authenticate with, either a static Memco API key or
            a session token. When omitted, ``MEMCO_API_TOKEN`` is used, falling back
            to the deprecated ``MEMCO_API_KEY`` with a warning.
        host: Service endpoint, optionally including a port such as
            ``localhost:50051``. When omitted, ``MEMCO_API_HOST`` is used,
            falling back to ``grpc.spark.memco.ai``. Port 443 is assumed when the
            host carries none.
        tls: Whether to dial over TLS using the system trust store. Set to
            ``False`` only for a plaintext endpoint such as a local server.
        timeout: Default per-call deadline in seconds. Individual methods can
            override it.
        env: Environment mapping to read defaults from. Defaults to
            :data:`os.environ`; supplying one is mainly useful in tests.

    Raises:
        MemcoConfigError: If no credential is available or the host is unusable.
        MemcoUnavailableError: If the service cannot be reached.
        MemcoUnhealthyError: If the service reports that it is not serving.
        MemcoAuthenticationError: If the credential is rejected.
        MemcoSunsetError: If what this client uses is past its sunset date.

    Attributes:
        memory: The memory operations, as
            :class:`~memco.operations.MemoryOperations`.

    Example:
        >>> with Memco() as client:
        ...     session = client.memory.start_session("coding")
        ...     result = client.memory.search("how does gRPC health checking work",
        ...                                   session_id=session.session_id)
        ...     for memory in result.memories:
        ...         print(memory.idx, len(memory.insights))
    """

    def __init__(
        self,
        token: str | None = None,
        host: str | None = None,
        *,
        tls: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._config = resolve(token, host, tls=tls, timeout=timeout, env=env)
        self._channel = build_channel(self._config)
        self._health = health_pb2_grpc.HealthStub(self._channel)
        self._closed = False
        # close() must not destroy the channel while a call is in flight: grpc
        # registers a call handle per method, and invoking one that was not
        # already warmed dereferences the destroyed channel and takes the
        # process down. A pre-check cannot prevent that, because the crash
        # happens inside the call rather than as an exception.
        self._state = threading.Condition()
        self._inflight = 0
        self.memory = MemoryOperations(_pbg.MemoryServiceStub(self._channel), self._call)
        """The memory operations. See :class:`~memco.operations.MemoryOperations`."""
        try:
            self._check_health()
            # Discarding the result: what is worth keeping — the limits and the
            # per-domain tag cap — is retained by the call itself.
            self.memory.describe_domains()
        except MemcoAuthenticationError:
            _log.error("credential rejected by %s", self._config.target)
            self.close()
            raise
        except BaseException:
            self.close()
            raise

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the underlying channel.

        Blocks until any in-flight call has finished, so a client shared between
        threads can be closed from one of them safely. Safe to call more than
        once. After closing, any further call raises
        :class:`~memco.errors.MemcoConfigError`.
        """
        with self._state:
            if self._closed:
                return
            self._closed = True
            while self._inflight:
                self._state.wait()
        self._channel.close()

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

    def _call(self, method: Callable[..., _T], request: Any, timeout: float | None) -> _T:
        """Invoke one RPC, translating any failure into a typed exception.

        Args:
            method: The stub method to call.
            request: The request message.
            timeout: Per-call deadline, or ``None`` to use the client default.

        Returns:
            The response message.

        Raises:
            MemcoConfigError: If the client has been closed.
            MemcoAPIError: If the service returned an error status.
        """
        deadline = _deadline(timeout, self._config.timeout)
        with self._state:
            if self._closed:
                raise MemcoConfigError("this client is closed; create a new one to make more calls")
            self._inflight += 1
        try:
            return method(request, timeout=deadline)
        except grpc.RpcError as exc:
            raise from_rpc_error(exc) from exc
        finally:
            with self._state:
                self._inflight -= 1
                if self._closed and not self._inflight:
                    self._state.notify_all()
