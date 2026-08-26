"""The synchronous Memco client."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, TypeVar

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
from memco.memory.v1 import memory_pb2_grpc as _pbg

from ._channel import build_channel
from ._config import DEFAULT_TIMEOUT, resolve
from ._config import deadline as _deadline
from ._memory import MemoryOperations
from ._provenance import provenance as _provenance
from .errors import MemcoConfigError, MemcoUnhealthyError, from_rpc_error
from .types import Provenance

_T = TypeVar("_T")

__all__ = ["Client"]


class Client:
    """Synchronous client for Memco Shared Memory.

    Opens one gRPC channel and holds it until closed, so a single client should
    be created once and reused. It is safe to share between threads.

    On construction the client verifies it can reach the service by calling the
    standard gRPC health endpoint. That probe is unauthenticated, so it proves
    the host, port and TLS settings are sound but says nothing about the
    credential; pass ``verify_credentials=True`` to check that too.

    Args:
        token: Credential to authenticate with, either a static Memco API key or
            a WorkOS JWT. When omitted, ``MEMCO_API_TOKEN`` is used, falling back
            to the deprecated ``MEMCO_API_KEY`` with a warning.
        host: Service endpoint, optionally including a port such as
            ``localhost:50051``. When omitted, ``MEMCO_API_HOST`` is used,
            falling back to ``grpc.spark.memco.ai``. Port 443 is assumed when the
            host carries none.
        tls: Whether to dial over TLS using the system trust store. Set to
            ``False`` only for a plaintext endpoint such as a local server.
        timeout: Default per-call deadline in seconds. Individual methods can
            override it.
        check_health: Whether to probe the health endpoint on construction. Set
            to ``False`` to construct without touching the network.
        verify_credentials: Whether to additionally call
            :meth:`~memco.client._memory.MemoryOperations.list_domains` on
            construction to prove the credential works. Off by default because
            that call is rate-limited and would spend one of the caller's
            per-minute tokens every time a client is built.
        env: Environment mapping to read defaults from. Defaults to
            :data:`os.environ`; supplying one is mainly useful in tests.

    Raises:
        MemcoConfigError: If no credential is available or the host is unusable.
        MemcoUnavailableError: If the service cannot be reached.
        MemcoUnhealthyError: If the service reports that it is not serving.
        MemcoAuthenticationError: If ``verify_credentials`` is set and the
            credential is rejected.

    Attributes:
        memory: The memory operations, as
            :class:`~memco.client._memory.MemoryOperations`.

    Example:
        >>> with Client() as client:
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
        check_health: bool = True,
        verify_credentials: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._config = resolve(token, host, tls=tls, timeout=timeout, env=env)
        self._channel = build_channel(self._config)
        self._health = health_pb2_grpc.HealthStub(self._channel)
        self._closed = False
        self.memory = MemoryOperations(_pbg.MemoryServiceStub(self._channel), self._call)
        """The memory operations. See :class:`~memco.client._memory.MemoryOperations`."""
        try:
            if check_health:
                self._check_health()
            if verify_credentials:
                self.memory.list_domains()
        except BaseException:
            self.close()
            raise

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Close the underlying channel.

        Safe to call more than once. After closing, any further call raises
        :class:`~memco.client.errors.MemcoConfigError`.
        """
        if not self._closed:
            self._closed = True
            self._channel.close()

    def __enter__(self) -> Client:
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
        if self._closed:
            raise MemcoConfigError("this client is closed; create a new one to make more calls")
        deadline = _deadline(timeout, self._config.timeout)
        try:
            return method(request, timeout=deadline)
        except grpc.RpcError as exc:
            raise from_rpc_error(exc) from exc
        except ValueError as exc:
            # close() flips the flag and then tears the channel down, so a call
            # that passed the check above can still land on a dead channel.
            # grpc signals that with a bare ValueError, which would escape raw.
            raise MemcoConfigError(
                "this client is closed; create a new one to make more calls"
            ) from exc
