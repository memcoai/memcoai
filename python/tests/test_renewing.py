"""The renewing credential behind every call: one mint at a time, no key ended under a call.

A client's token and an impersonated session's key are each held by one of
these. It mints a credential when none is held or the one held has reached its
renewal point, and ends a credential it has replaced only once the last call
using it has finished. It is driven here with plain callables and the fake
clock, so each rule is pinned without a server in the way; the 0.8-of-lifetime
renewal point is set by whoever mints, and is pinned where the clients do that.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest

from memcoai import errors
from memcoai._auth import AsyncRenewing, Minted, Renewing

from .conftest import FakeClock

DUE_AFTER = 100.0
"""Seconds from minting to each test credential's renewal point."""

RACERS = 8


class Mints:
    """A mint that numbers what it hands out, and an end that records what it is given.

    Attributes:
        minted: How many credentials have been minted.
        ended: The value of each credential ended, in order.
        failure: When set, minting raises it instead.
        delay: Seconds each mint takes, to widen the window a second caller
            could slip a mint of its own into.
    """

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.minted = 0
        self.ended: list[str] = []
        self.failure: Exception | None = None
        self.delay = 0.0

    def _next(self) -> Minted:
        if self.failure is not None:
            raise self.failure
        self.minted += 1
        return Minted(
            f"value-{self.minted}",
            self._clock.monotonic() + DUE_AFTER,
            key_id=f"key-{self.minted}",
        )

    def mint(self) -> Minted:
        time.sleep(self.delay)
        return self._next()

    def end(self, minted: Minted) -> None:
        self.ended.append(minted.value)

    async def mint_async(self) -> Minted:
        await asyncio.sleep(self.delay)
        return self._next()

    async def end_async(self, minted: Minted) -> None:
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


# --- minting and renewal -------------------------------------------------


def test_the_first_lease_mints_and_later_ones_reuse_it(mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    assert [lease_once(renewing), lease_once(renewing)] == ["value-1", "value-1"]
    assert mints.minted == 1


def test_a_credential_is_renewed_once_its_renewal_point_arrives(clock: FakeClock, mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER - 1)
    assert lease_once(renewing) == "value-1"
    clock.advance(1)
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
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    with renewing.lease():
        # Ended on the way in: one live key per session, and the service caps
        # how many a user may hold.
        assert mints.ended == ["value-1"]
    assert mints.ended == ["value-1"]


def test_a_replaced_credential_is_ended_only_after_its_last_lease_exits(
    clock: FakeClock, mints: Mints
):
    # Ending a key under a call still carrying it would fail that call
    # UNAUTHENTICATED for no reason of the caller's.
    renewing = Renewing(mints.mint, mints.end)
    with renewing.lease() as first:
        assert first.value == "value-1"
        with renewing.lease():
            clock.advance(DUE_AFTER)
            with renewing.lease() as renewed:
                assert renewed.value == "value-2"
            assert mints.ended == []
        assert mints.ended == []
    assert mints.ended == ["value-1"]


def test_a_failed_mint_keeps_the_current_credential_and_the_next_lease_retries(
    clock: FakeClock, mints: Mints
):
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    mints.failure = unavailable()
    with pytest.raises(errors.MemcoUnavailableError):
        lease_once(renewing)
    assert mints.ended == []
    mints.failure = None
    assert lease_once(renewing) == "value-2"
    assert mints.ended == ["value-1"]


def test_a_failed_lease_is_not_counted_as_a_user(clock: FakeClock, mints: Mints):
    # Otherwise the credential would look busy forever, and close() would
    # wait on a lease that no longer exists to end it.
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
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


def race(renewing: Renewing) -> list[str]:
    """Lease from several threads released at the same instant."""
    start = threading.Barrier(RACERS)

    def lease() -> str:
        start.wait()
        return lease_once(renewing)

    with ThreadPoolExecutor(max_workers=RACERS) as pool:
        running = [pool.submit(lease) for _ in range(RACERS)]
        return [done.result(timeout=5) for done in running]


def test_threads_leasing_at_once_share_a_single_mint(mints: Mints):
    mints.delay = 0.05
    renewing = Renewing(mints.mint, mints.end)
    assert race(renewing) == ["value-1"] * RACERS
    assert mints.minted == 1


def test_threads_leasing_past_the_renewal_point_renew_once(clock: FakeClock, mints: Mints):
    renewing = Renewing(mints.mint, mints.end)
    lease_once(renewing)
    clock.advance(DUE_AFTER)
    mints.delay = 0.05
    assert race(renewing) == ["value-2"] * RACERS
    assert mints.minted == 2
    assert mints.ended == ["value-1"]


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


async def test_async_a_credential_is_renewed_once_its_renewal_point_arrives(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(DUE_AFTER - 1)
    assert await lease_once_async(renewing) == "value-1"
    clock.advance(1)
    assert await lease_once_async(renewing) == "value-2"
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
        async with renewing.lease() as renewed:
            assert renewed.value == "value-2"
        assert mints.ended == []
    assert mints.ended == ["value-1"]


async def test_async_a_failed_mint_keeps_the_current_credential_and_the_next_lease_retries(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(DUE_AFTER)
    mints.failure = unavailable()
    with pytest.raises(errors.MemcoUnavailableError):
        await lease_once_async(renewing)
    assert mints.ended == []
    mints.failure = None
    assert await lease_once_async(renewing) == "value-2"
    assert mints.ended == ["value-1"]


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
    assert mints.ended == ["value-1"]


async def settled(mints: Mints, ended: int) -> None:
    """Wait for a mint a cancelled lease left running to finish, and its end with it."""
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


async def test_async_a_renewal_cancelled_mid_mint_leaves_no_credential_live(
    clock: FakeClock, mints: Mints
):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await lease_once_async(renewing)
    clock.advance(DUE_AFTER)
    mints.delay = 0.05
    await cancelled_mid_mint(renewing)
    # The lock went with the cancelled lease, so the next one renews for
    # itself; the credential the cancelled one goes on to receive is ended.
    assert await lease_once_async(renewing) == "value-3"
    await settled(mints, 2)
    assert sorted(mints.ended) == ["value-1", "value-2"]


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


async def test_async_a_lease_after_close_is_refused_without_minting(mints: Mints):
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)
    await renewing.close()
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await lease_once_async(renewing)
    assert mints.minted == 0


def test_async_renewing_can_be_reused_under_a_new_event_loop(clock: FakeClock, mints: Mints):
    # An asyncio.Lock binds to the loop it is first contended on, and a client
    # reused across two asyncio.run() calls carries its sessions into the
    # second loop. Each run contends, so a lock carried over would raise.
    mints.delay = 0.01
    renewing = AsyncRenewing(mints.mint_async, mints.end_async)

    async def contend() -> list[str]:
        return await asyncio.gather(*(lease_once_async(renewing) for _ in range(RACERS)))

    assert asyncio.run(contend()) == ["value-1"] * RACERS
    clock.advance(DUE_AFTER)
    assert asyncio.run(contend()) == ["value-2"] * RACERS
    assert mints.minted == 2
    assert mints.ended == ["value-1"]
