"""The credential each call carries, and the renewing holder it is leased from.

The credential travels as a gRPC metadata header, passed explicitly on each
call rather than attached by a channel interceptor. A client holds more than
one credential at once -- its own token, and a key per impersonated session --
so which one a call carries has to be an argument of that call, not state on
the channel every call shares. Passing it explicitly also fails closed: a stub
call made without it carries no credential at all, which is exactly what the
health probe and the token exchange need. :func:`grpc.metadata_call_credentials`
is not used because gRPC refuses call credentials on an insecure channel, and
the SDK must also support plaintext endpoints for local development and tests.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

# Imported by name, and read through this module by the clients, so a test can
# move both clocks by patching them here without touching grpc's deadlines.
from time import monotonic, time

from .errors import MemcoConfigError

__all__ = [
    "AUTH_HEADER",
    "AUTH_SCHEME",
    "RENEW_AFTER",
    "AsyncRenewing",
    "Minted",
    "Renewing",
    "metadata",
    "monotonic",
    "time",
]

AUTH_HEADER = "authorization"
"""Metadata key carrying the credential."""

AUTH_SCHEME = "Bearer "
"""Scheme prefix placed before the credential.

The service matches this with a case-sensitive prefix check, so the capital
``B`` and the trailing space are both load-bearing. A lowercase ``bearer``
is rejected as an invalid credential.
"""

RENEW_AFTER = 0.8
"""The fraction of a minted credential's lifetime after which it is renewed.

Early enough that a call leased just before the renewal point still has a
fifth of the lifetime to finish in, and late enough that a credential is not
re-minted more often than it needs to be.
"""

_CLOSED = "this session is closed; open a new one to make more calls"


def metadata(token: str) -> tuple[tuple[str, str], ...]:
    """Build the metadata one call sends its credential in.

    Args:
        token: The bearer to send: an API key, an issued token or an
            impersonation key.

    Returns:
        The metadata, holding exactly one credential entry.
    """
    return ((AUTH_HEADER, f"{AUTH_SCHEME}{token}"),)


@dataclass(eq=False, slots=True)
class Minted:
    """One credential a :class:`Renewing` holds, and the calls still using it.

    Mutable, and compared by identity: the use count and the retired flag are
    the bookkeeping that decides when the credential may be ended.

    Attributes:
        value: The bearer itself. Excluded from ``repr`` so it cannot reach a
            log or a crash report.
        renew_at: The :func:`monotonic` reading at which to replace it.
            ``math.inf`` for a credential that is never renewed.
        key_id: The id the service names the credential by, if it has one.
            Safe to log: it revokes nothing.
        users: How many leases of it are open.
        retired: Whether it has been replaced or closed, so that its last
            lease to exit ends it.
    """

    value: str = field(repr=False)
    renew_at: float
    key_id: str = ""
    users: int = 0
    retired: bool = False


def _retire(minted: Minted | None) -> Minted | None:
    """Mark a replaced credential retired, returning it if it is to be ended now.

    Called under the holder's lock, as is every change to a use count, so
    exactly one party ends each credential: the retirer when nothing is using
    it, otherwise its last lease.

    Args:
        minted: The credential being replaced, if one was held.

    Returns:
        The credential when nothing is using it, else ``None``.
    """
    if minted is None:
        return None
    minted.retired = True
    return None if minted.users else minted


class Renewing:
    """A credential minted on first use and renewed at its renewal point.

    A client's own token and each impersonated session's key are held by one of
    these. At most one mint is in flight at a time, so a burst of calls arriving
    at the renewal point mints once. A credential it has replaced is ended only
    once the last call carrying it has finished, so a renewal never revokes a
    key under a call still in flight, yet no replaced key is left live once
    its calls are done.
    """

    def __init__(
        self, mint: Callable[[], Minted], end: Callable[[Minted], None] | None = None
    ) -> None:
        """Hold nothing yet; the first lease mints.

        Args:
            mint: Mints a fresh credential. Raises a typed error on failure,
                which the lease that called it raises in turn.
            end: Ends a credential that has been replaced or closed. Must not
                raise. ``None`` when there is nothing to end a credential with.
        """
        self._mint = mint
        self._end = end
        self._lock = threading.Lock()
        self._current: Minted | None = None
        self._closed = False

    @contextmanager
    def lease(self) -> Iterator[Minted]:
        """Hold the current credential for the duration of one call.

        The mint happens under the lock, which is what makes it single-flight.
        A failed mint raises before anything is counted and keeps the current
        credential, so the next lease simply tries again.

        Yields:
            The credential to send, as the :class:`Minted` itself rather than
            its bare value: the object's repr hides the value, so no caller
            need hold the bearer in a local an error's frame would show.

        Raises:
            MemcoConfigError: If this credential has been closed.
            MemcoError: Whatever the mint raised.
        """
        with self._lock:
            if self._closed:
                raise MemcoConfigError(_CLOSED)
            stale = None
            if self._current is None or monotonic() >= self._current.renew_at:
                fresh = self._mint()
                stale, self._current = _retire(self._current), fresh
            held = self._current
            held.users += 1
        try:
            if stale is not None and self._end is not None:
                self._end(stale)
            yield held
        finally:
            with self._lock:
                held.users -= 1
                done = held.retired and not held.users
            if done and self._end is not None:
                self._end(held)

    def close(self) -> None:
        """Refuse further leases, and end the credential once nothing uses it.

        Safe to call more than once. Waits for a mint in flight, so the
        credential it produces is ended too rather than left live.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            idle, self._current = _retire(self._current), None
        if idle is not None and self._end is not None:
            self._end(idle)


class AsyncRenewing:
    """The asyncio twin of :class:`Renewing`.

    An :class:`asyncio.Lock` makes the mint single-flight. It binds to the
    event loop it is first contended on, and a client reused across two
    :func:`asyncio.run` calls carries its credentials into the second loop, so
    the lock is recreated whenever the running loop changes. The use counts
    need no lock of their own: nothing is awaited between reading one and
    changing it.

    Unlike a thread, a lease can be cancelled mid-mint, and that is the one
    way a credential could otherwise be minted and never ended: see
    :meth:`_fresh`.
    """

    def __init__(
        self,
        mint: Callable[[], Awaitable[Minted]],
        end: Callable[[Minted], Awaitable[None]] | None = None,
    ) -> None:
        """Hold nothing yet; the first lease mints.

        Args:
            mint: Mints a fresh credential. Raises a typed error on failure,
                which the lease that called it raises in turn.
            end: Ends a credential that has been replaced or closed. Must not
                raise. ``None`` when there is nothing to end a credential with.
        """
        self._mint = mint
        self._end = end
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._current: Minted | None = None
        self._closed = False
        # Ends of credentials whose lease was cancelled mid-mint. The event
        # loop holds a task only weakly, so this holds each until it is done.
        self._ending: set[asyncio.Future[None]] = set()

    def _minting(self) -> asyncio.Lock:
        """Return the mint lock, bound to the running event loop.

        Returns:
            The lock to hold while checking and minting.
        """
        running = asyncio.get_running_loop()
        if self._lock is None or self._loop is not running:
            self._lock, self._loop = asyncio.Lock(), running
        return self._lock

    async def _fresh(self) -> Minted:
        """Mint under the lock, ending the credential if the lease is cancelled first.

        The mint runs as a task of its own, shielded from the lease awaiting
        it. Cancelling the lease -- an open timed out by
        :func:`asyncio.wait_for`, a task group torn down -- does not stop the
        service issuing the credential, and one no holder knows of would stay
        live, counted against the user's cap, until it expired. It is ended
        instead, as soon as it arrives. A renewal cancelled this way is minted
        again by the next lease, since the lock goes with the cancelled one.

        Returns:
            The fresh credential.

        Raises:
            MemcoError: Whatever the mint raised.
        """
        minting = asyncio.ensure_future(self._mint())
        try:
            return await asyncio.shield(minting)
        except asyncio.CancelledError:
            minting.add_done_callback(self._abandoned)
            raise

    def _abandoned(self, minting: asyncio.Future[Minted]) -> None:
        """End what a mint produced after the lease awaiting it was cancelled.

        Args:
            minting: The finished mint.
        """
        if self._end is None or minting.cancelled() or minting.exception() is not None:
            return
        ending = asyncio.ensure_future(self._end(minting.result()))
        self._ending.add(ending)
        ending.add_done_callback(self._ending.discard)

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Minted]:
        """Hold the current credential for the duration of one call.

        Yields:
            The credential to send, as the :class:`Minted` itself rather than
            its bare value: the object's repr hides the value, so no caller
            need hold the bearer in a local an error's frame would show.

        Raises:
            MemcoConfigError: If this credential has been closed.
            MemcoError: Whatever the mint raised.
        """
        async with self._minting():
            if self._closed:
                raise MemcoConfigError(_CLOSED)
            stale = None
            if self._current is None or monotonic() >= self._current.renew_at:
                fresh = await self._fresh()
                stale, self._current = _retire(self._current), fresh
            held = self._current
            held.users += 1
        try:
            if stale is not None and self._end is not None:
                await self._end(stale)
            yield held
        finally:
            held.users -= 1
            if held.retired and not held.users and self._end is not None:
                await self._end(held)

    async def close(self) -> None:
        """Refuse further leases, and end the credential once nothing uses it.

        Safe to call more than once. Waits for a mint in flight, so the
        credential it produces is ended too rather than left live.
        """
        async with self._minting():
            if self._closed:
                return
            self._closed = True
            idle, self._current = _retire(self._current), None
        if idle is not None and self._end is not None:
            await self._end(idle)
