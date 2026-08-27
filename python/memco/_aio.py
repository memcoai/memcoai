"""The asyncio Memco client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from types import TracebackType
from typing import Any

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from memco.memory.v1 import memory_pb2_grpc as _pbg

from ._channel import build_async_channel
from ._config import DEFAULT_TIMEOUT, resolve
from ._config import deadline as _deadline
from ._provenance import provenance as _provenance
from .errors import (
    MemcoAuthenticationError,
    MemcoConfigError,
    MemcoUnhealthyError,
    from_rpc_error,
)
from .operations import AsyncMemoryOperations
from .types import Provenance

__all__ = ["AsyncMemco"]

# The same logger the synchronous client writes to; see memco/_sync.py.
_log = logging.getLogger("memco")


class _LazyStub:
    """Resolves to the real stub once the channel is open.

    The channel is built inside the running event loop rather than in
    ``__init__``, so the namespace is handed this stand-in and reaches the real
    stub through it on first use.
    """

    def __init__(self, client: AsyncMemco) -> None:
        """Bind to the client that owns the channel.

        Args:
            client: The client whose stub to resolve.
        """
        self._client = client

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
        return getattr(self._client._stub, name)  # noqa: SLF001


class AsyncMemco:
    """Asyncio client for Memco Shared Memory.

    Mirrors :class:`~memco.Memco` method for method; only the awaiting
    differs. Because opening a connection requires I/O, the checks the
    synchronous client runs in ``__init__`` cannot run here: use it as an async
    context manager, or call :meth:`connect` yourself.

    Args:
        token: Credential to authenticate with, either a static Memco API key or
            a session token. When omitted, ``MEMCO_API_TOKEN`` is used, falling back
            to the deprecated ``MEMCO_API_KEY`` with a warning.
        host: Service endpoint, optionally including a port. When omitted,
            ``MEMCO_API_HOST`` is used, falling back to ``grpc.spark.memco.ai``.
        tls: Whether to dial over TLS using the system trust store.
        timeout: Default per-call deadline in seconds.
        env: Environment mapping to read defaults from. Defaults to
            :data:`os.environ`.

    Raises:
        MemcoConfigError: If no credential is available or the host is unusable.

    Attributes:
        memory: The memory operations, as
            :class:`~memco.operations.AsyncMemoryOperations`.

    Example:
        >>> async with AsyncMemco() as client:
        ...     session = await client.memory.start_session("coding")
        ...     result = await client.memory.search("how does health checking work",
        ...                                         session_id=session.session_id)
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
        self._closed = False
        # Built lazily, never here. grpc.aio captures the running event loop at
        # channel construction, so a client created at module scope — or after
        # an earlier asyncio.run() returned — would bind to the wrong loop and
        # fail later with a cross-loop error, or raise a bare RuntimeError for
        # having no loop at all. Deferring binds it to the loop that awaits it.
        self._channel: Any = None
        self._stub: Any = None
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
        self.memory = AsyncMemoryOperations(_LazyStub(self), self._call)
        """The memory operations. See :class:`~memco.operations.AsyncMemoryOperations`."""

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
            self._health = None
        if self._channel is None:
            self._channel = build_async_channel(self._config)
            self._loop = running
            self._stub = _pbg.MemoryServiceStub(self._channel)
            self._health = health_pb2_grpc.HealthStub(self._channel)
            self._idle = asyncio.Event()
            self._idle.set()

    async def connect(self) -> AsyncMemco:
        """Verify the connection, running the checks the constructor could not.

        Probes the health endpoint, then calls
        :meth:`~memco.operations.AsyncMemoryOperations.describe_domains`, which
        proves the credential and teaches the client the input limits the
        service enforces. Calling this more than once simply repeats both.

        Returns:
            This client.

        Raises:
            MemcoUnavailableError: If the service cannot be reached.
            MemcoUnhealthyError: If the service reports that it is not serving.
            MemcoAuthenticationError: If the credential is rejected.
        """
        self._open()
        try:
            await self._check_health()
            # Discarding the result: what is worth keeping — the limits and the
            # per-domain tag cap — is retained by the call itself.
            await self.memory.describe_domains()
        except MemcoAuthenticationError:
            _log.error("credential rejected by %s", self._config.target)
            await self._reset()
            raise
        except BaseException:
            # Drop the channel but stay usable: a failed probe is often a
            # transient blip, and this method documents itself as repeatable.
            await self._reset()
            raise
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
            self._health = None
            self._loop = None
            await self._drain()
            await channel.close(grace=None)

    async def close(self) -> None:
        """Close the underlying channel.

        Safe to call more than once. After closing, any further call raises
        :class:`~memco.errors.MemcoConfigError`.
        """
        if self._closed:
            return
        self._closed = True
        if self._channel is not None:
            await self._drain()
            await self._channel.close(grace=None)
            self._channel = None
            self._loop = None

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

    async def _call(self, method: Any, request: Any, timeout: float | None) -> Any:
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
        self._open()
        deadline = _deadline(timeout, self._config.timeout)
        self._inflight += 1
        self._idle.clear()
        try:
            return await method(request, timeout=deadline)
        except grpc.RpcError as exc:
            raise from_rpc_error(exc) from exc
        except grpc.aio.UsageError as exc:
            # close() flips the flag and then tears the channel down, so a call
            # that passed the check above can still land on a dead channel.
            # UsageError is not an RpcError, so it would otherwise escape raw.
            raise MemcoConfigError(
                "this client is closed; create a new one to make more calls"
            ) from exc
        finally:
            self._inflight -= 1
            if not self._inflight:
                self._idle.set()
