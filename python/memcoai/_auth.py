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
import copy
import logging
import math
import threading
import warnings
import weakref
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

# Imported by name, and read through this module by the clients, so a test can
# move both clocks by patching them here without touching grpc's deadlines.
from time import monotonic, perf_counter, time
from typing import Any, NamedTuple, TypeVar

import grpc

from .errors import MemcoConfigError, MemcoTimeoutError, MemcoUnavailableError

_T = TypeVar("_T")

__all__ = [
    "AUTH_HEADER",
    "AUTH_SCHEME",
    "RENEW_AFTER",
    "AsyncRenewing",
    "Live",
    "Minted",
    "Renewing",
    "apart",
    "detach",
    "key_lifetime",
    "metadata",
    "monotonic",
    "orphaned",
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

_SKEWED = (
    "the credential the service just issued has already expired by this host's clock, "
    "which is well ahead of the service's; check the system clock"
)

# A child of the `memcoai` logger, like every module in the package; see
# memcoai/_logging.py.
_log = logging.getLogger(__name__)


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
        expires: The :func:`monotonic` reading at which the service stops
            honouring it. Until then it stays in use if renewing it fails.
            ``math.inf`` for a credential that never expires.
        key_id: The id the service names the credential by, if it has one.
            Safe to log: it revokes nothing.
        users: How many leases of it are open.
        retired: Whether it has been replaced or closed, so that its last
            lease to exit ends it.
    """

    value: str = field(repr=False)
    renew_at: float
    expires: float = math.inf
    key_id: str = ""
    users: int = 0
    retired: bool = False


class Live(NamedTuple):
    """An impersonation key a client minted and has not yet ended.

    Attributes:
        external_id: The user the key acts as.
        expires: The :func:`monotonic` reading at which the service stops
            honouring the key, after which there is nothing left to end.
        failed: Whether ending it was refused, so that the next key minted for
            the same user tries again first.
    """

    external_id: str
    expires: float
    failed: bool = False


def orphaned(orphans: deque[tuple[str, str]], external_id: str, key_id: str) -> None:
    """Queue the key of an impersonated session garbage-collected without being closed.

    Runs from the session credential's finalizer: when the last reference to
    it goes, or a collection frees it, on whatever thread that happens, and
    possibly while that thread holds the client's locks. So it sends nothing
    and takes no lock -- a deque appends atomically -- and the client ends the
    key at its next call. Queued before it warns, since a warning filter set
    to ``error`` raises here.

    Args:
        orphans: The owning client's queue of keys to end.
        external_id: The user the key acts as.
        key_id: The key the session held.
    """
    orphans.append((external_id, key_id))
    warnings.warn(
        f"an impersonated session holding key {key_id} was garbage-collected without "
        "being closed; its client ends the key at its next call. Close the session, or "
        "use it in a with block, to end the key at once",
        ResourceWarning,
        stacklevel=1,
    )


_DETACHED: set[asyncio.Future[Any]] = set()
"""Every task :func:`detach` has in flight. The event loop holds a task only weakly."""


def detach(work: Awaitable[_T]) -> asyncio.Future[_T]:
    """Run a mint or an end as a task of its own, held until it is done.

    Cancelling whoever awaits it -- a timeout, a task group torn down, or
    anyio, which cancels again at every await inside a cancelled scope -- then
    no longer stops it part-way, provided they await it through
    :func:`asyncio.shield` or :func:`asyncio.wait`. Held here rather than by
    its caller, which may be garbage-collected first. A task left pending on a
    loop that has since closed can never run, and is let go.

    Args:
        work: What to run.

    Returns:
        The task.
    """
    for stranded in [task for task in _DETACHED if task.get_loop().is_closed()]:
        _DETACHED.discard(stranded)
    task = asyncio.ensure_future(work)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    return task


def apart(work: Callable[..., object], *args: object) -> None:
    """Run a mint or an end on a thread of its own, so that no call waits for it.

    Not a daemon: one in flight as the interpreter exits is let finish, within
    its own deadline, rather than cut off inside grpc.

    Args:
        work: What to run.
        *args: What to run it with.
    """
    threading.Thread(target=work, args=args, name="memcoai-credential").start()


def _gave_up(timeout: float | None) -> MemcoTimeoutError:
    """The error for a lease that gave up waiting on a mint.

    Args:
        timeout: How long the lease waited.

    Returns:
        The error to raise.
    """
    return MemcoTimeoutError(
        grpc.StatusCode.DEADLINE_EXCEEDED,
        f"gave up after {timeout}s waiting for the credential to be minted",
    )


def _watch(
    holder: object, dropped: Callable[[str], None] | None, held: Minted | None
) -> weakref.finalize[[str], object] | None:
    """Hand the key id held to ``dropped`` if its holder is garbage-collected first.

    Only the id, so the value is in no global registry.

    Args:
        holder: The holder to watch.
        dropped: Called with the key id of ``held`` if ``holder`` is collected
            before the watch is detached, or ``None`` for nothing to do then.
        held: The credential now held, if any.

    Returns:
        The watch, to detach once the credential is no longer held, or
        ``None`` when there is nothing to watch.
    """
    if dropped is None or held is None:
        return None
    watch = weakref.finalize(holder, dropped, held.key_id)
    # Not at interpreter exit: the holder is not garbage then, and its client
    # makes no next call. typeshed gives finalize empty __slots__, but atexit
    # is a property of it.
    watch.atexit = False  # type: ignore[misc]
    return watch


def _honoured(held: Minted | None, failure: Exception | None) -> Minted:
    """What a lease that waited on a mint gets: the credential held, if it is honoured.

    After a mint that worked, that is the fresh credential. After one that
    failed, it is the one it was to replace, until that expires.

    Args:
        held: The credential held once the mint finished, if any.
        failure: What the mint raised, if it failed.

    Returns:
        The credential to lease.

    Raises:
        Exception: A copy of what the mint raised, when nothing held is still
            honoured. A copy, so no two callers share a traceback.
        MemcoConfigError: If the mint worked but what it produced has already
            expired by this host's clock, which is then well ahead of the
            service's.
    """
    if held is not None and monotonic() < held.expires:
        return held
    if failure is not None:
        raise copy.copy(failure)
    raise MemcoConfigError(_SKEWED)


def key_lifetime(key: Any, sent: float) -> tuple[float, float]:
    """Time an impersonation key: the monotonic reading its lifetime runs from, and its length.

    With ``expires_in``, the seconds the key has left by the service's own
    count, it is timed as a client token is: from a reading taken before the
    request was sent, so it can only fall due early, and with no wall clock in
    it, so a host whose clock is off times it correctly all the same. A service
    that does not send it yet sends 0, and ``expires_at``, absolute Unix time,
    is then read against this host's wall clock, once, on receipt -- which is
    what a clock well ahead of the service's gets wrong, and what
    :func:`_honoured` refuses a key for.

    Args:
        key: The ``ImpersonationKey`` the service returned.
        sent: The :func:`monotonic` reading taken before the request was sent.

    Returns:
        The reading the lifetime runs from, and the lifetime in seconds.
    """
    if key.expires_in > 0:
        return sent, float(key.expires_in)
    return monotonic(), key.expires_at - time()


def _fell_back(held: Minted | None, failure: Exception) -> None:
    """Report a failed renewal that leaves the credential held in use.

    Renewal comes well before expiry precisely so that a failed one fails no
    call: the credential held still works, and the next lease past its renewal
    point tries again. Reported once per failed mint, by the mint itself. A
    client that closed meanwhile is no failure to report.

    Args:
        held: The credential held, if any.
        failure: What the mint raised.
    """
    if isinstance(failure, MemcoConfigError) or held is None or monotonic() >= held.expires:
        return
    # The key id revokes nothing, so it is safe to log; the value never is.
    _log.warning(
        "could not renew %s, so it stays in use until it expires: %s",
        f"key {held.key_id}" if held.key_id else "the client token",
        failure,
    )


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
    """A credential minted on first use and renewed at its renewal point, apart from any call.

    A client's own token and each impersonated session's key are held by one of
    these. Each mint runs on a thread of its own, one at a time, so a burst of
    calls arriving at the renewal point mints once. Renewal starts well before
    expiry so that a slow or failing one holds up no call: until the credential
    held expires, every lease gets it at once. Only a lease with nothing
    honoured to lease waits for the mint -- no longer than its own deadline --
    and every lease waiting on one mint shares its outcome, rather than each
    minting again in turn.

    A credential it has replaced is ended once the last call carrying it has
    finished, and on a thread of its own too. So a renewal never revokes a key
    under a call still in flight, no replaced key is left live once its calls
    are done, and no call waits for the service to confirm the end.
    """

    def __init__(
        self,
        mint: Callable[[], Minted],
        end: Callable[[Minted], object] | None = None,
        dropped: Callable[[str], None] | None = None,
    ) -> None:
        """Hold nothing yet; the first lease mints.

        Args:
            mint: Mints a fresh credential. Raises a typed error on failure,
                which a lease waiting on it raises in turn.
            end: Ends a credential that has been replaced or closed. Must not
                raise. ``None`` when there is nothing to end a credential with.
            dropped: Called with the key id of the credential held if this is
                garbage-collected without being closed, from the collector, so
                it must neither block nor call the service. ``None`` when
                nothing needs to know.
        """
        self._mint = mint
        self._end = end
        self._dropped = dropped
        # Guards everything below, and is notified whenever a mint finishes or
        # the holder closes.
        self._state = threading.Condition()
        self._current: Minted | None = None
        self._closed = False
        self._minting = False
        # How many mints have finished, and what the last one raised, so a
        # lease waiting on one can tell that it has finished, and how.
        self._attempts = 0
        self._failed: Exception | None = None
        self._watching: weakref.finalize[[str], object] | None = None

    @contextmanager
    def lease(self, timeout: float | None = None) -> Iterator[Minted]:
        """Hold the current credential for the duration of one call.

        Args:
            timeout: The longest to wait when there is no credential honoured
                to lease and one is being minted, or ``None`` to wait for as
                long as that takes. The mint itself runs to its own deadline,
                the client's default, since every lease waiting on it shares it.

        Yields:
            The credential to send, as the :class:`Minted` itself rather than
            its bare value: the object's repr hides the value, so no caller
            need hold the bearer in a local an error's frame would show.

        Raises:
            MemcoConfigError: If this credential has been closed.
            MemcoTimeoutError: If a mint this lease had to wait on outlasted
                ``timeout``.
            MemcoError: A copy of what the mint raised, if nothing held is
                still honoured.
        """
        with self._state:
            held = self._leasable(timeout)
            held.users += 1
        try:
            yield held
        finally:
            with self._state:
                held.users -= 1
                done = held.retired and not held.users
            if done and self._end is not None:
                apart(self._end, held)

    def _leasable(self, timeout: float | None) -> Minted:
        """The credential to lease, renewing it in the background once it is due.

        Called under the lock.

        Args:
            timeout: As for :meth:`lease`.

        Returns:
            The credential to lease.
        """
        if self._closed:
            raise MemcoConfigError(_CLOSED)
        held = self._current
        if held is not None and monotonic() < held.renew_at:
            return held
        seen = self._attempts
        if not self._minting:
            # Flagged once started, not before, so a thread that could not
            # start leaves nothing waiting on it. It cannot finish first: it
            # needs this lock to.
            apart(self._renew)
            self._minting = True
        if held is not None and monotonic() < held.expires:
            return held
        until = None if timeout is None else perf_counter() + timeout
        while self._attempts == seen and not self._closed:
            left = None if until is None else until - perf_counter()
            if left is not None and left <= 0:
                raise _gave_up(timeout)
            # Capped: past TIMEOUT_MAX, a wait raises OverflowError instead.
            self._state.wait(None if left is None else min(left, threading.TIMEOUT_MAX))
        if self._closed:
            raise MemcoConfigError(_CLOSED)
        return _honoured(self._current, self._failed)

    def _renew(self) -> None:
        """Mint, then hold what it produced, or end it if closed meanwhile.

        Runs on a thread of its own. Whatever the mint raises is recorded for
        the leases waiting on it -- as a copy, whose traceback holds no frame
        of this holder -- and whatever happens, they are woken.
        """
        fresh: Minted | None = None
        failure: Exception | None = None
        stale: Minted | None = None
        try:
            fresh = self._mint()
        except Exception as exc:  # Handed to the leases waiting on it, never lost.
            failure = exc
        finally:
            with self._state:
                self._minting = False
                self._attempts += 1
                self._failed = None if failure is None else copy.copy(failure)
                held = self._current
                if fresh is not None:
                    # Once closed, nothing will lease it.
                    stale = fresh if self._closed else self._hold(fresh)
                self._state.notify_all()
        if failure is not None:
            _fell_back(held, failure)
        if stale is not None and self._end is not None:
            self._end(stale)

    def _hold(self, minted: Minted | None) -> Minted | None:
        """Hold a fresh credential, or none once closed, and watch it.

        Called under the lock.

        Args:
            minted: The credential to hold from now on, if any.

        Returns:
            The credential replaced, if it is to be ended now.
        """
        if self._watching is not None:
            self._watching.detach()
        self._watching = _watch(self, self._dropped, minted)
        replaced, self._current = _retire(self._current), minted
        return replaced

    def close(self) -> None:
        """Refuse further leases, and end the credential once nothing uses it.

        Safe to call more than once. A mint still in flight is not waited for:
        what it produces is ended as it arrives.
        """
        with self._state:
            if self._closed:
                return
            self._closed = True
            idle = self._hold(None)
            self._state.notify_all()
        if idle is not None and self._end is not None:
            self._end(idle)


class AsyncRenewing:
    """The asyncio twin of :class:`Renewing`.

    Each mint and each end runs as a task of its own (see :func:`detach`), so
    cancelling a lease never stops one part-way: a mint whose lease was
    cancelled still lands, and is held, or ended if the holder was closed
    meanwhile, and an end still reaches the service. Nothing needs a lock:
    nothing is awaited between reading this state and changing it.
    """

    def __init__(
        self,
        mint: Callable[[], Awaitable[Minted]],
        end: Callable[[Minted], Awaitable[object]] | None = None,
        dropped: Callable[[str], None] | None = None,
    ) -> None:
        """Hold nothing yet; the first lease mints.

        Args:
            mint: Mints a fresh credential. Raises a typed error on failure,
                which a lease waiting on it raises in turn.
            end: Ends a credential that has been replaced or closed. Must not
                raise. ``None`` when there is nothing to end a credential with.
            dropped: Called with the key id of the credential held if this is
                garbage-collected without being closed, from the collector, so
                it must neither block nor call the service. ``None`` when
                nothing needs to know.
        """
        self._mint = mint
        self._end = end
        self._dropped = dropped
        self._current: Minted | None = None
        self._closed = False
        # The mint in flight, or the last one. Its result is what it failed
        # with, or None.
        self._minting: asyncio.Future[Exception | None] | None = None
        self._watching: weakref.finalize[[str], object] | None = None

    @asynccontextmanager
    async def lease(self, timeout: float | None = None) -> AsyncIterator[Minted]:
        """Hold the current credential for the duration of one call.

        Args:
            timeout: The longest to wait when there is no credential honoured
                to lease and one is being minted, or ``None`` to wait for as
                long as that takes. The mint itself runs to its own deadline,
                the client's default, since every lease waiting on it shares it.

        Yields:
            The credential to send, as the :class:`Minted` itself rather than
            its bare value: the object's repr hides the value, so no caller
            need hold the bearer in a local an error's frame would show.

        Raises:
            MemcoConfigError: If this credential has been closed.
            MemcoTimeoutError: If a mint this lease had to wait on outlasted
                ``timeout``.
            MemcoError: A copy of what the mint raised, if nothing held is
                still honoured.
        """
        held = await self._leasable(timeout)
        held.users += 1
        try:
            yield held
        finally:
            held.users -= 1
            if held.retired and not held.users and self._end is not None:
                detach(self._end(held))

    async def _leasable(self, timeout: float | None) -> Minted:
        """The credential to lease, renewing it in the background once it is due.

        Args:
            timeout: As for :meth:`lease`.

        Returns:
            The credential to lease.
        """
        if self._closed:
            raise MemcoConfigError(_CLOSED)
        held = self._current
        if held is not None and monotonic() < held.renew_at:
            return held
        minting = self._minting
        running = asyncio.get_running_loop()
        # A mint left on a loop since closed, or merely no longer running, can
        # never be awaited from this one: it is abandoned, not waited on.
        if minting is None or minting.done() or minting.get_loop() is not running:
            minting = self._minting = detach(self._renew())
        if held is not None and monotonic() < held.expires:
            return held
        # Not asyncio.wait_for: it would cancel the mint on a timeout, and
        # before 3.12 can swallow a cancellation of this lease.
        done, _ = await asyncio.wait((minting,), timeout=timeout)
        if not done:
            raise _gave_up(timeout)
        if self._closed:
            raise MemcoConfigError(_CLOSED)
        if minting.cancelled():
            # The channel closed under it, which is no cancellation of this
            # lease's own.
            raise MemcoUnavailableError(
                grpc.StatusCode.UNAVAILABLE, "the connection closed while the credential was minted"
            )
        return _honoured(self._current, minting.result())

    async def _renew(self) -> Exception | None:
        """Mint, then hold what it produced, or end it if closed meanwhile.

        Runs as a task of its own.

        Returns:
            What the mint raised, if it failed, for the leases waiting on it.
        """
        try:
            fresh = await self._mint()
        except Exception as exc:  # Handed to the leases waiting on it, never lost.
            _fell_back(self._current, exc)
            # A copy, whose traceback holds no frame of this holder.
            return copy.copy(exc)
        stale = fresh if self._closed else self._hold(fresh)
        if stale is not None and self._end is not None:
            detach(self._end(stale))
        return None

    def _hold(self, minted: Minted | None) -> Minted | None:
        """Hold a fresh credential, or none once closed, and watch it.

        Args:
            minted: The credential to hold from now on, if any.

        Returns:
            The credential replaced, if it is to be ended now.
        """
        if self._watching is not None:
            self._watching.detach()
        self._watching = _watch(self, self._dropped, minted)
        replaced, self._current = _retire(self._current), minted
        return replaced

    async def close(self) -> None:
        """Refuse further leases, and end the credential once nothing uses it.

        Safe to call more than once. A mint still in flight is not waited for:
        what it produces is ended as it arrives. The end runs as a task of its
        own, so a close cancelled part-way -- the cleanup of an open that timed
        out, a scope torn down on the way out -- still finishes it.
        """
        if self._closed:
            return
        self._closed = True
        idle = self._hold(None)
        if idle is not None and self._end is not None:
            await asyncio.shield(detach(self._end(idle)))
