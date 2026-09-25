"""The renewing credential behind every call: one mint at a time, no key ended under a call.

A client's token and an impersonated session's key are each held by one of
these. It mints a credential when none is held, and renews the one held in the
background once it reaches its renewal point, leasing it meanwhile. It ends a
credential it has replaced only once the last call using it has finished. It is
driven here with plain callables and the fake clock, so each rule is pinned
without a server in the way; the 0.8-of-lifetime renewal point is set by
whoever mints, and is pinned where the clients do that.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import grpc
import pytest

from memcoai import _auth, errors
from memcoai._auth import AsyncRenewing, Minted, Renewing

from .conftest import FakeClock

DUE_AFTER = 100.0
"""Seconds from minting to each test credential's renewal point."""

EXPIRES_AFTER = 125.0
"""Seconds from minting to each test credential's expiry, so it is due at four fifths."""

RACERS = 8


class Mints:
    """A mint that numbers what it hands out, and an end that records what it is given.

    Attributes:
        minted: How many credentials have been minted.
        attempts: How many mints were started, failed ones included.
        started: Set once a mint has started, so a test knows the lock is held.
        ended: The value of each credential ended, in order.
        failure: When set, minting raises it instead.
        delay: Seconds each mint takes, to widen the window a second caller
            could slip a mint of its own into.
        end_delay: Seconds each asyncio end takes, to leave a window to cancel
            it in.
    """

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.minted = 0
        self.attempts = 0
        self.started = threading.Event()
        self.ended: list[str] = []
        self.failure: Exception | None = None
        self.delay = 0.0
        self.end_delay = 0.0

    def _next(self) -> Minted:
        if self.failure is not None:
            raise self.failure
        self.minted += 1
        return Minted(
            f"value-{self.minted}",
            self._clock.monotonic() + DUE_AFTER,
            self._clock.monotonic() + EXPIRES_AFTER,
            key_id=f"key-{self.minted}",
        )

    def mint(self) -> Minted:
        self.attempts += 1
        self.started.set()
        time.sleep(self.delay)
        return self._next()

    def end(self, minted: Minted) -> None:
        self.ended.append(minted.value)

    async def mint_async(self) -> Minted:
        self.attempts += 1
        self.started.set()
        await asyncio.sleep(self.delay)
        return self._next()

    async def end_async(self, minted: Minted) -> None:
        await asyncio.sleep(self.end_delay)
        self.ended.append(minted.value)


@pytest.fixture
def mints(clock: FakeClock) -> Mints:
    return Mints(clock)


def unavailable() -> errors.MemcoUnavailableError:
    return errors.MemcoUnavailableError(grpc.StatusCode.UNAVAILABLE, "token service down")


def lease_once(renewing: Renewing) -> str:
    with renewing.lease() as held:
        return held.value


async def lease_once_async(renewing: AsyncRenewing) -> str:
    async with renewing.lease() as held:
        return held.value


def soon(done: Callable[[], bool]) -> None:
    """Wait for something a background thread will do, failing if it never does."""
    for _ in range(500):
        if done():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting")


def settle(renewing: Renewing) -> None:
    """Wait for the mint running in the background, if any, to land."""
    soon(lambda: not renewing._minting)


async def settle_async(renewing: AsyncRenewing) -> None:
    """Wait for the mint running in the background, if any, to land."""
    if renewing._minting is not None and not renewing._minting.done():
        await asyncio.wait((renewing._minting,))


# --- minting and renewal -------------------------------------------------


def test_the_first_lease_mints_and_later_ones_reuse_it(mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    assert [lease_once(renewing), lease_once(renewing)] == ["value-1", "value-1"]
    assert mints.minted == 1


def test_a_credential_is_renewed_in_the_background_once_its_renewal_point_arrives(
    clock: FakeClock, mints: Mints
):
    # Still honoured, so it is leased at once while the next one is minted:
    # a slow mint holds up no call.
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER - 1)
    assert lease_once(renewing) == "value-1"
    assert mints.attempts == 1
    clock.advance(1)
    assert lease_once(renewing) == "value-1"
    settle(renewing)
    assert lease_once(renewing) == "value-2"


def test_a_static_token_is_never_renewed(clock: FakeClock):
    # An API key has no lifetime the SDK knows of, so it is held as due never.
    minted: list[Minted] = []

    def mint() -> Minted:
        minted.append(Minted("static-token", math.inf))
        return minted[-1]

    renewing = Renewing(mint)
    lease_once(renewing)
    clock.advance(10 * 365 * 86400)
    assert lease_once(renewing) == "static-token"
    assert len(minted) == 1


def test_a_replaced_credential_is_ended_as_soon_as_nothing_uses_it(clock: FakeClock, mints: Mints):
    # Ended as its replacement lands: one live key per session, and the
    # service caps how many a user may hold.
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    lease_once(renewing)
    settle(renewing)
    soon(lambda: mints.ended == ["value-1"])


def test_a_replaced_credential_is_ended_only_after_its_last_lease_exits(
    clock: FakeClock, mints: Mints
):
    # Ending a key under a call still carrying it would fail that call
    # UNAUTHENTICATED for no reason of the caller's.
    renewing = Renewing(mints.mint, mints.end)
    with renewing.lease() as first:
        assert first.value == "value-1"
        clock.advance(DUE_AFTER)
        with renewing.lease() as meanwhile:
            assert meanwhile.value == "value-1"
        settle(renewing)
        with renewing.lease() as renewed:
            assert renewed.value == "value-2"
        assert mints.ended == []
    soon(lambda: mints.ended == ["value-1"])


def test_a_failed_renewal_leases_the_current_credential_and_the_next_lease_retries(
    clock: FakeClock, mints: Mints
):
    # It still works until it expires, so failing the call would turn a
    # renewal the service cannot make yet into an outage.
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    mints.failure = unavailable()
    assert lease_once(renewing) == "value-1"
    settle(renewing)
    assert lease_once(renewing) == "value-1"
    settle(renewing)
    assert mints.attempts == 3
    assert mints.ended == []
    mints.failure = None
    assert lease_once(renewing) == "value-1"
    settle(renewing)
    assert lease_once(renewing) == "value-2"
    soon(lambda: mints.ended == ["value-1"])


def test_a_failed_renewal_is_raised_once_the_current_credential_has_expired(
    clock: FakeClock, mints: Mints
):
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.failure = unavailable()
    with pytest.raises(errors.MemcoUnavailableError):
        lease_once(renewing)
    assert mints.ended == []


def test_a_fallback_is_logged_once_per_failed_attempt_by_key_id(
    clock: FakeClock, mints: Mints, caplog: pytest.LogCaptureFixture
):
    # The threads that waited on the attempt share its outcome, the fallback
    # included, and it is one failure, so one record.
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    mints.failure = unavailable()
    mints.delay = 0.2
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        assert [done.result() for done in race(renewing)] == ["value-1"] * RACERS
        settle(renewing)
        soon(lambda: bool(caplog.records))
    assert mints.attempts == 2
    (warning,) = caplog.records
    assert "key-1" in warning.getMessage()
    assert "value-1" not in caplog.text


def test_a_failed_lease_is_not_counted_as_a_user(clock: FakeClock, mints: Mints):
    # Otherwise the credential would look busy forever, and close() would
    # wait on a lease that no longer exists to end it.
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.failure = unavailable()
    with pytest.raises(errors.MemcoUnavailableError):
        lease_once(renewing)
    renewing.close()
    assert mints.ended == ["value-1"]


def test_without_an_end_a_replaced_credential_is_simply_dropped(clock: FakeClock, mints: Mints):
    # The contract has no call that revokes a client token, so that use has
    # nothing to end one with.
    renewing = Renewing(mints.mint)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    lease_once(renewing)
    settle(renewing)
    assert lease_once(renewing) == "value-2"
    renewing.close()


# --- close ---------------------------------------------------------------


def test_close_ends_an_idle_credential_at_once_and_only_once(mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    renewing.close()
    renewing.close()
    assert mints.ended == ["value-1"]


def test_close_leaves_a_busy_credential_to_its_last_lease(mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    with renewing.lease():
        renewing.close()
        assert mints.ended == []
    assert mints.ended == ["value-1"]


def test_a_lease_waiting_on_a_mint_is_refused_when_the_holder_closes(mints: Mints):
    # And the close does not wait for the mint: what it produces is ended as
    # it arrives, since nothing will lease it.
    mints.delay = 0.2
    renewing = Renewing(mints.mint, mints.end)
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(lease_once, renewing)
        assert mints.started.wait(5)
        renewing.close()
        assert mints.ended == []
        with pytest.raises(errors.MemcoConfigError, match="closed"):
            waiting.result(timeout=5)
    soon(lambda: mints.ended == ["value-1"])


def test_close_before_anything_was_minted_ends_nothing(mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    renewing.close()
    assert (mints.minted, mints.ended) == (0, [])


def test_a_lease_after_close_is_refused_without_minting(mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    renewing.close()
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        lease_once(renewing)
    assert mints.minted == 0


# --- concurrency ---------------------------------------------------------


def race(renewing: Renewing) -> list[Future[str]]:
    """Lease from several threads released at the same instant, once they are all done."""
    start = threading.Barrier(RACERS)

    def lease() -> str:
        start.wait()
        return lease_once(renewing)

    with ThreadPoolExecutor(max_workers=RACERS) as pool:
        return [pool.submit(lease) for _ in range(RACERS)]


def test_threads_leasing_at_once_share_a_single_mint(mints: Mints):
    mints.delay = 0.05
    renewing = Renewing(mints.mint, mints.end)
    assert [done.result() for done in race(renewing)] == ["value-1"] * RACERS
    assert mints.minted == 1


def test_threads_leasing_past_the_renewal_point_renew_once(clock: FakeClock, mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    mints.delay = 0.05
    assert [done.result() for done in race(renewing)] == ["value-1"] * RACERS
    settle(renewing)
    assert mints.minted == 2
    soon(lambda: mints.ended == ["value-1"])


def test_threads_waiting_on_a_failed_renewal_share_its_failure(clock: FakeClock, mints: Mints):
    # Each minting in turn would make the last of them wait out every mint
    # before its own, far past its deadline, and send the token service as
    # many requests as there were callers.
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.failure = unavailable()
    mints.delay = 0.2
    raised = [done.exception() for done in race(renewing)]
    assert mints.attempts == 2
    assert all(isinstance(error, errors.MemcoUnavailableError) for error in raised)
    # A copy each, so no two callers share one traceback.
    assert len({id(error) for error in raised}) == RACERS
    assert mints.ended == []


def test_a_lease_after_a_shared_failure_tries_again(clock: FakeClock, mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.failure = unavailable()
    mints.delay = 0.05
    race(renewing)
    mints.failure = None
    assert lease_once(renewing) == "value-2"
    assert mints.attempts == 3


def test_a_lease_waits_for_a_mint_in_flight_no_longer_than_its_timeout(mints: Mints):
    # Waiting on another caller's mint is bounded by this caller's own
    # deadline, rather than by however long that mint takes.
    mints.delay = 1.0
    renewing = Renewing(mints.mint, mints.end)
    with ThreadPoolExecutor(max_workers=1) as pool:
        minting = pool.submit(lease_once, renewing)
        assert mints.started.wait(5)
        waited = time.monotonic()
        with pytest.raises(errors.MemcoTimeoutError), renewing.lease(timeout=0.1):
            pass
        assert time.monotonic() - waited < 0.5
        assert minting.result(timeout=5) == "value-1"
    assert mints.attempts == 1


# --- a holder dropped unclosed -------------------------------------------


def test_a_holder_dropped_unclosed_hands_over_the_key_it_holds_now(clock: FakeClock, mints: Mints):
    # The one it replaced was ended by its last lease; handing that over
    # instead would end it twice and leave the live one to expire.
    dropped: list[str] = []
    renewing = Renewing(mints.mint, mints.end, dropped.append)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    lease_once(renewing)
    settle(renewing)
    soon(lambda: mints.ended == ["value-1"])
    del renewing
    # Once the thread that renewed it lets it go too.
    soon(lambda: dropped == ["key-2"])


def test_a_holder_closed_before_it_is_dropped_hands_over_nothing(mints: Mints):
    dropped: list[str] = []
    renewing = Renewing(mints.mint, mints.end, dropped.append)
    lease_once(renewing)
    renewing.close()
    del renewing
    assert dropped == []


async def test_async_a_holder_dropped_unclosed_hands_over_the_key_it_holds_now(
    clock: FakeClock, mints: Mints
):
    dropped: list[str] = []
    renewing = AsyncRenewing(mints.mint_async, mints.end_async, dropped.append)
    await lease_once_async(renewing)
    clock.advance(DUE_AFTER)
    await lease_once_async(renewing)
    await settle_async(renewing)
    del renewing
    assert dropped == ["key-2"]


# --- secrecy -------------------------------------------------------------


def test_the_value_never_reaches_a_repr():
    minted = Minted("secret-bearer-value", 1.0, key_id="key-1")
    assert "secret-bearer-value" not in repr(minted)


# --- the asyncio twin ----------------------------------------------------


async def test_async_the_first_lease_mints_and_later_ones_reuse_it(mints: Mints):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    assert [await lease_once_async(renewing), await lease_once_async(renewing)] == [
        "value-1",
        "value-1",
    ]
    assert mints.minted == 1


async def test_async_a_credential_is_renewed_in_the_background_once_its_renewal_point_arrives(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(DUE_AFTER - 1)
    assert await lease_once_async(renewing) == "value-1"
    clock.advance(1)
    assert await lease_once_async(renewing) == "value-1"
    await settle_async(renewing)
    assert await lease_once_async(renewing) == "value-2"
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def test_async_tasks_leasing_at_once_share_a_single_mint(mints: Mints):
    mints.delay = 0.01
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    leased = await asyncio.gather(*(lease_once_async(renewing) for _ in range(RACERS)))
    assert leased == ["value-1"] * RACERS
    assert mints.minted == 1


async def test_async_a_replaced_credential_is_ended_only_after_its_last_lease_exits(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    async with renewing.lease() as first:
        assert first.value == "value-1"
        clock.advance(DUE_AFTER)
        async with renewing.lease() as meanwhile:
            assert meanwhile.value == "value-1"
        await settle_async(renewing)
        async with renewing.lease() as renewed:
            assert renewed.value == "value-2"
        await asyncio.sleep(0.05)
        assert mints.ended == []
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def test_async_a_failed_renewal_leases_the_current_credential_and_the_next_lease_retries(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(DUE_AFTER)
    mints.failure = unavailable()
    assert await lease_once_async(renewing) == "value-1"
    await settle_async(renewing)
    assert await lease_once_async(renewing) == "value-1"
    await settle_async(renewing)
    assert mints.attempts == 3
    assert mints.ended == []
    mints.failure = None
    assert await lease_once_async(renewing) == "value-1"
    await settle_async(renewing)
    assert await lease_once_async(renewing) == "value-2"
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def test_async_a_failed_renewal_is_raised_once_the_current_credential_has_expired(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.failure = unavailable()
    with pytest.raises(errors.MemcoUnavailableError):
        await lease_once_async(renewing)
    assert mints.ended == []


async def test_async_tasks_waiting_on_a_failed_renewal_fall_back_together(
    clock: FakeClock, mints: Mints, caplog: pytest.LogCaptureFixture
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(DUE_AFTER)
    mints.failure = unavailable()
    mints.delay = 0.05
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        leased = await asyncio.gather(*(lease_once_async(renewing) for _ in range(RACERS)))
        await settle_async(renewing)
    assert leased == ["value-1"] * RACERS
    assert mints.attempts == 2
    (warning,) = caplog.records
    assert "key-1" in warning.getMessage()
    assert "value-1" not in caplog.text


async def test_async_close_ends_an_idle_credential_at_once_and_only_once(mints: Mints):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    await renewing.close()
    await renewing.close()
    assert mints.ended == ["value-1"]


async def test_async_close_leaves_a_busy_credential_to_its_last_lease(mints: Mints):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    async with renewing.lease():
        await renewing.close()
        assert mints.ended == []
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def settled(mints: Mints, ended: int) -> None:
    """Wait for the ends a cancelled caller left running to finish."""
    for _ in range(500):
        if len(mints.ended) >= ended:
            return
        await asyncio.sleep(0.01)


async def cancelled_mid_mint(renewing: AsyncRenewing) -> None:
    """Start a lease, and cancel it while its mint is still in flight."""
    leasing = asyncio.create_task(lease_once_async(renewing))
    await asyncio.sleep(0.01)
    leasing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leasing


async def test_async_a_lease_cancelled_mid_mint_ends_what_the_mint_produces(mints: Mints):
    # An open timed out by wait_for: cancelling the lease does not stop the
    # service issuing the credential, and one no holder knows of would stay
    # live, against the user's cap, until it expired.
    mints.delay = 0.05
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await cancelled_mid_mint(renewing)
    await renewing.close()
    await settled(mints, 1)
    assert (mints.minted, mints.ended) == (1, ["value-1"])


async def test_async_a_renewal_whose_lease_was_cancelled_still_replaces_the_credential(
    clock: FakeClock, mints: Mints
):
    # The lease had to wait, the credential held having expired; the mint it
    # waited on lands all the same, and the one it replaces is ended.
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.delay = 0.05
    await cancelled_mid_mint(renewing)
    assert await lease_once_async(renewing) == "value-2"
    await settled(mints, 1)
    assert (mints.attempts, mints.ended) == (2, ["value-1"])


async def test_async_a_mint_failing_after_its_lease_was_cancelled_ends_nothing_quietly(
    mints: Mints, caplog: pytest.LogCaptureFixture
):
    # Nothing was minted, so nothing is ended, and the failure is not left
    # for asyncio to report as an exception nobody retrieved.
    mints.delay = 0.05
    mints.failure = unavailable()
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        await cancelled_mid_mint(renewing)
        await asyncio.sleep(0.1)
    assert caplog.records == []
    mints.failure = None
    assert await lease_once_async(renewing) == "value-1"
    assert mints.ended == []


async def failures(renewing: AsyncRenewing) -> list[BaseException | None]:
    """Lease from several tasks at once, keeping what each raised."""
    leased = await asyncio.gather(
        *(lease_once_async(renewing) for _ in range(RACERS)), return_exceptions=True
    )
    return [error if isinstance(error, BaseException) else None for error in leased]


async def test_async_tasks_waiting_on_a_failed_renewal_share_its_failure(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.failure = unavailable()
    mints.delay = 0.05
    raised = await failures(renewing)
    assert mints.attempts == 2
    assert all(isinstance(error, errors.MemcoUnavailableError) for error in raised)
    assert len({id(error) for error in raised}) == RACERS
    mints.failure = None
    assert await lease_once_async(renewing) == "value-2"


async def test_async_a_lease_cancelled_while_it_waits_leaves_the_mint_to_land(mints: Mints):
    # The mint is a task of its own, so cancelling the lease waiting on it --
    # a timeout, a task group torn down -- does not stop it; what it produces
    # is held, and the next lease gets it without minting again.
    mints.delay = 0.05
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await cancelled_mid_mint(renewing)
    assert await lease_once_async(renewing) == "value-1"
    assert (mints.attempts, mints.ended) == (1, [])


async def test_async_a_lease_waits_for_a_mint_in_flight_no_longer_than_its_timeout(
    mints: Mints,
):
    mints.delay = 1.0
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    minting = asyncio.create_task(lease_once_async(renewing))
    await asyncio.sleep(0.05)
    waited = time.monotonic()
    with pytest.raises(errors.MemcoTimeoutError):
        async with renewing.lease(timeout=0.1):
            pass
    assert time.monotonic() - waited < 0.5
    assert await minting == "value-1"
    assert mints.attempts == 1


# The end of a credential is shielded as its mint is: cancelling the caller
# awaiting it -- a task group torn down, a timeout, or anyio re-cancelling at
# every await of a cancelled scope -- must not stop the service being told,
# or the key stays live, against the user's cap, until it expires.


async def test_async_a_close_cancelled_while_ending_still_ends_the_credential(mints: Mints):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    mints.end_delay = 0.05
    closing = asyncio.create_task(renewing.close())
    await asyncio.sleep(0.01)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def test_async_the_lease_that_renews_waits_for_no_end(clock: FakeClock, mints: Mints):
    # It waits for the new credential, the one held having expired, but not
    # for the service to confirm the old one ended; cancelling it after that
    # leaves the end to finish all the same.
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(EXPIRES_AFTER)
    mints.end_delay = 0.2
    assert await asyncio.wait_for(lease_once_async(renewing), 0.1) == "value-2"
    assert mints.ended == []
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def test_async_a_last_lease_cancelled_while_ending_a_retired_credential_still_ends_it(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    entered, leave = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with renewing.lease():
            entered.set()
            await leave.wait()

    holding = asyncio.create_task(hold())
    await asyncio.wait_for(entered.wait(), 5)
    clock.advance(DUE_AFTER)
    await lease_once_async(renewing)
    await settle_async(renewing)
    assert await lease_once_async(renewing) == "value-2"
    mints.end_delay = 0.2
    leave.set()
    # The last lease hands the end off rather than waiting for it, so it is
    # done long before the end is, and nothing it does cuts the end short.
    await asyncio.wait_for(holding, 0.1)
    assert mints.ended == []
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def test_async_a_lease_waiting_on_a_mint_is_refused_when_the_holder_closes(mints: Mints):
    mints.delay = 0.1
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    waiting = asyncio.create_task(lease_once_async(renewing))
    await asyncio.sleep(0.01)
    await renewing.close()
    assert mints.ended == []
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await waiting
    await settled(mints, 1)
    assert mints.ended == ["value-1"]


async def test_async_a_lease_after_close_is_refused_without_minting(mints: Mints):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await renewing.close()
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await lease_once_async(renewing)
    assert mints.minted == 0


def test_async_a_renewal_left_on_a_closed_loop_is_started_afresh_on_the_next(
    clock: FakeClock, mints: Mints
):
    # A loop closed without cancelling what it left pending -- a fresh loop
    # per job -- strands the renewal it had started, which can never finish,
    # nor be awaited from another loop.
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(lease_once_async(renewing)) == "value-1"
        clock.advance(DUE_AFTER)
        mints.delay = 60.0
        assert loop.run_until_complete(lease_once_async(renewing)) == "value-1"
        stranded = renewing._minting
    finally:
        loop.close()
    mints.delay = 0.0
    clock.advance(EXPIRES_AFTER)
    assert asyncio.run(lease_once_async(renewing)) == "value-2"
    assert stranded not in _auth._DETACHED


def test_async_renewing_can_be_reused_under_a_new_event_loop(clock: FakeClock, mints: Mints):
    # A client reused across two asyncio.run() calls carries its sessions
    # into the second loop, where the mint of the first has long finished.
    mints.delay = 0.01
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)

    async def contend() -> list[str]:
        leased = await asyncio.gather(*(lease_once_async(renewing) for _ in range(RACERS)))
        await settle_async(renewing)
        await settled(mints, mints.minted - 1)
        return leased

    assert asyncio.run(contend()) == ["value-1"] * RACERS
    clock.advance(DUE_AFTER)
    assert asyncio.run(contend()) == ["value-1"] * RACERS
    assert asyncio.run(contend()) == ["value-2"] * RACERS
    assert mints.minted == 2
    assert mints.ended == ["value-1"]
