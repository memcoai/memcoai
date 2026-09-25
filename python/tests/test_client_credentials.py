"""Client credentials: an API client's id and secret, exchanged for a token the SDK renews.

The exchange is ``TokenService.IssueToken``, the one call sent with no bearer
of its own: the credentials in the request are what authenticate it. What each
call carried is read off the wire, since that is where a wrong or missing
credential would show.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2

from memcoai import AsyncMemco, Memco, errors
from memcoai.auth.v1 import auth_pb2 as auth_pb

from .conftest import CLIENT_ID, CLIENT_SECRET, TOKEN, FakeClock
from .fake_server import Harness, Hold

RENEWS_AT = 0.8 * 3600
"""Seconds after issue that the fake's default token falls due for renewal."""

EXPIRES_AT = 3600
"""Seconds after issue that the fake's default token expires."""

CLIENT_ENV = {"MEMCO_CLIENT_ID": CLIENT_ID, "MEMCO_CLIENT_SECRET": CLIENT_SECRET}


def credentials(harness: Harness) -> dict[str, Any]:
    """The constructor arguments for a client-credential client of the fake server."""
    return {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "host": harness.address,
        "tls": False,
    }


def bearers(sent: list[tuple[tuple[str, str], ...]]) -> list[list[str]]:
    """Every authorization value each call carried, repeats included."""
    return [[value for key, value in call if key == "authorization"] for call in sent]


def chain(error: BaseException) -> list[BaseException]:
    """Every exception reachable from this one, whether a traceback shows it or not."""
    found: list[BaseException] = []
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is not None and all(current is not seen for seen in found):
            found.append(current)
            pending += [current.__cause__, current.__context__]
    return found


# --- construction --------------------------------------------------------


def test_construction_issues_a_token_and_calls_no_memory_method(harness: Harness):
    with Memco(**credentials(harness)):
        pass
    assert harness.health.checked_services == [""]
    assert harness.tokens.calls == ["IssueToken"]
    # A client token carries no content role, so the ListDomains a token
    # client proves itself with would only be refused.
    assert harness.memory.calls == []
    assert harness.admin.calls == []


def test_the_health_check_runs_before_a_token_is_asked_for(harness: Harness):
    harness.health.status = health_pb2.HealthCheckResponse.NOT_SERVING
    with pytest.raises(errors.MemcoUnhealthyError):
        Memco(**credentials(harness))
    assert harness.tokens.calls == []


def test_the_token_request_carries_the_credentials_and_no_bearer(harness: Harness):
    with Memco(**credentials(harness)):
        pass
    # ttl_seconds 0 takes the service's default lifetime, and no scope takes
    # everything the client was granted.
    assert harness.tokens.received == [
        auth_pb.IssueTokenRequest(
            grant_type="client_credentials", client_id=CLIENT_ID, client_secret=CLIENT_SECRET
        )
    ]
    assert bearers(harness.tokens.raw_metadata) == [[]]


def test_token_lifetime_is_sent_as_ttl_seconds(harness: Harness):
    with Memco(**credentials(harness), token_lifetime=600):
        pass
    assert harness.tokens.received[0].ttl_seconds == 600


def test_a_lifetime_over_the_service_cap_is_the_service_s_to_refuse(harness: Harness):
    harness.tokens.error = (grpc.StatusCode.INVALID_ARGUMENT, "ttl_seconds must be at most 86400")
    with pytest.raises(errors.MemcoInvalidRequestError):
        Memco(**credentials(harness), token_lifetime=100_000)
    assert harness.tokens.received[0].ttl_seconds == 100_000


@pytest.mark.parametrize(
    "given",
    [{"token_lifetime": 2**31}, {"client_secret": "secret-\ud800-value"}],
    ids=["lifetime too large for the wire", "secret that is not valid Unicode"],
)
def test_a_value_the_wire_cannot_carry_is_refused_unsent_and_unchained(
    harness: Harness, given: dict[str, Any]
):
    # An encoding error holds the text it refused, and here that may be the
    # secret, so the refusal chains nothing at all.
    with pytest.raises(errors.MemcoInvalidRequestError) as caught:
        Memco(**{**credentials(harness), **given})
    assert harness.tokens.calls == []
    assert chain(caught.value) == [caught.value]


async def test_async_connect_issues_a_token_and_calls_no_memory_method(harness: Harness):
    async with AsyncMemco(**credentials(harness)):
        pass
    assert harness.health.checked_services == [""]
    assert harness.tokens.calls == ["IssueToken"]
    assert bearers(harness.tokens.raw_metadata) == [[]]
    assert harness.memory.calls == []


async def test_async_the_health_check_runs_before_a_token_is_asked_for(harness: Harness):
    harness.health.status = health_pb2.HealthCheckResponse.NOT_SERVING
    with pytest.raises(errors.MemcoUnhealthyError):
        async with AsyncMemco(**credentials(harness)):
            pass
    assert harness.tokens.calls == []


async def test_async_connect_again_reuses_a_token_that_is_not_due(
    async_credentialed: AsyncMemco, harness: Harness
):
    await async_credentialed.connect()
    assert harness.tokens.calls == []
    assert harness.memory.calls == []


async def test_async_a_close_made_while_connecting_waits_for_the_exchange_in_flight(
    harness: Harness,
):
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    client = AsyncMemco(**credentials(harness))
    order: list[str] = []

    async def connecting() -> None:
        await client.connect()
        order.append("connected")

    async def closing() -> None:
        await client.close()
        order.append("closed")

    connected = asyncio.ensure_future(connecting())
    try:
        # After the health probe, so the exchange is the call in flight.
        assert await asyncio.to_thread(issuing.arrived.wait, 5)
        closed = asyncio.ensure_future(closing())
        # Long enough for a close that does not wait to shut the channel.
        await asyncio.sleep(0.1)
    finally:
        issuing.released.set()
    await asyncio.wait_for(asyncio.gather(connected, closed), 5)
    assert order == ["connected", "closed"]


async def test_async_a_close_waiting_on_a_connect_that_fails_does_not_raise(harness: Harness):
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    harness.tokens.transient_errors["IssueToken"] = [(grpc.StatusCode.UNAVAILABLE, "down")]
    client = AsyncMemco(**credentials(harness))
    connected = asyncio.ensure_future(client.connect())
    try:
        assert await asyncio.to_thread(issuing.arrived.wait, 5)
        closed = asyncio.ensure_future(client.close())
        # Long enough for the close to be waiting on the exchange.
        await asyncio.sleep(0.1)
    finally:
        issuing.released.set()
    with pytest.raises(errors.MemcoUnavailableError):
        await asyncio.wait_for(connected, 5)
    await asyncio.wait_for(closed, 5)
    assert client._channel is None


# --- the token on every call ---------------------------------------------


def test_admin_calls_carry_the_issued_token(credentialed: Memco, harness: Harness):
    credentialed.networks.list()
    credentialed.users.list()
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]] * 2


def test_memory_calls_carry_the_client_token_too(credentialed: Memco, harness: Harness):
    # The service refuses it -- a client token has no content role -- but
    # that refusal is the service's to make.
    credentialed.memory.list_domains()
    assert bearers(harness.memory.raw_metadata) == [["Bearer client-token-1"]]


def test_the_token_is_renewed_at_four_fifths_of_its_lifetime(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    clock.advance(RENEWS_AT - 1)
    credentialed.networks.list()
    assert harness.tokens.calls == []
    clock.advance(2)
    # Still honoured, so the call that sets the renewal off carries it.
    credentialed.networks.list()
    for _ in range(500):
        if not credentialed._credential._minting:
            break
        time.sleep(0.01)
    credentialed.networks.list()
    assert harness.tokens.calls == ["IssueToken"]
    assert bearers(harness.admin.raw_metadata) == [
        ["Bearer client-token-1"],
        ["Bearer client-token-1"],
        ["Bearer client-token-2"],
    ]


def test_a_token_due_for_renewal_serves_calls_while_it_renews(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # Renewal starts at four fifths of the lifetime so that a slow token
    # service holds up no call: the token held still works, so calls go on
    # with it while the next is issued.
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    clock.advance(0.85 * EXPIRES_AT)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            for _ in range(2):
                pool.submit(credentialed.networks.list, timeout=0.5).result(timeout=2)
        assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]] * 2
        assert issuing.arrived.wait(5)
    finally:
        issuing.released.set()
    for _ in range(500):
        credentialed.networks.list()
        if bearers(harness.admin.raw_metadata)[-1] == ["Bearer client-token-2"]:
            break
        time.sleep(0.01)
    assert harness.tokens.calls == ["IssueToken"]
    assert bearers(harness.admin.raw_metadata)[-1] == ["Bearer client-token-2"]


async def test_async_a_token_due_for_renewal_serves_calls_while_it_renews(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    clock.advance(0.85 * EXPIRES_AT)
    try:
        for _ in range(2):
            await asyncio.wait_for(async_credentialed.networks.list(timeout=0.5), 2)
        assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]] * 2
        assert await asyncio.to_thread(issuing.arrived.wait, 5)
    finally:
        issuing.released.set()
    for _ in range(500):
        await async_credentialed.networks.list()
        if bearers(harness.admin.raw_metadata)[-1] == ["Bearer client-token-2"]:
            break
        await asyncio.sleep(0.01)
    assert harness.tokens.calls == ["IssueToken"]
    assert bearers(harness.admin.raw_metadata)[-1] == ["Bearer client-token-2"]


# --- one deadline for the whole call --------------------------------------

DEADLINE = 1.0
"""The deadline the end-to-end tests give a call, per call or as the client's default."""


@pytest.fixture(params=[True, False], ids=["per-call timeout", "client default"])
def per_call(request: pytest.FixtureRequest) -> bool:
    """Whether the deadline is passed to the call, or is the client's default."""
    return bool(request.param)


def deadline_client(harness: Harness, per_call: bool) -> Memco:
    return Memco(**credentials(harness), timeout=30.0 if per_call else DEADLINE)


def async_deadline_client(harness: Harness, per_call: bool) -> AsyncMemco:
    return AsyncMemco(**credentials(harness), timeout=30.0 if per_call else DEADLINE)


def timed(per_call: bool) -> float | None:
    """The timeout to pass the call: ``None`` leaves it to the client's default."""
    return DEADLINE if per_call else None


def test_a_call_waits_for_a_credential_no_longer_than_its_deadline(
    clock: FakeClock, harness: Harness, per_call: bool
):
    # One deadline for the whole call: a token that never arrives fails it
    # when its caller said it would end, not a second deadline later.
    with deadline_client(harness, per_call) as client:
        issuing = harness.tokens.holds["IssueToken"] = Hold()
        clock.advance(EXPIRES_AT)
        started = time.monotonic()
        try:
            with pytest.raises(errors.MemcoTimeoutError):
                client.networks.list(timeout=timed(per_call))
            took = time.monotonic() - started
        finally:
            issuing.released.set()
    assert 0.9 * DEADLINE <= took < 1.3 * DEADLINE
    assert harness.admin.calls == []


def test_the_request_gets_only_what_the_wait_left_of_the_deadline(
    clock: FakeClock, harness: Harness, per_call: bool
):
    # The deadline is fixed as the call starts, so a call that waited for a
    # token still ends when its caller said it would.
    with deadline_client(harness, per_call) as client:
        issuing = harness.tokens.holds["IssueToken"] = Hold()
        listing = harness.admin.holds["ListNetworks"] = Hold()
        clock.advance(EXPIRES_AT)
        threading.Timer(0.3 * DEADLINE, issuing.released.set).start()
        started = time.monotonic()
        try:
            with pytest.raises(errors.MemcoTimeoutError):
                client.networks.list(timeout=timed(per_call))
            took = time.monotonic() - started
        finally:
            issuing.released.set()
            listing.released.set()
    assert listing.arrived.is_set()
    assert 0.9 * DEADLINE <= took < 1.2 * DEADLINE


def test_with_a_credential_to_hand_the_request_gets_the_whole_deadline(
    harness: Harness, per_call: bool
):
    with deadline_client(harness, per_call) as client:
        listing = harness.admin.holds["ListNetworks"] = Hold()
        started = time.monotonic()
        try:
            with pytest.raises(errors.MemcoTimeoutError):
                client.networks.list(timeout=timed(per_call))
            took = time.monotonic() - started
        finally:
            listing.released.set()
    assert 0.9 * DEADLINE <= took < 1.3 * DEADLINE


async def test_async_a_call_waits_for_a_credential_no_longer_than_its_deadline(
    clock: FakeClock, harness: Harness, per_call: bool
):
    async with async_deadline_client(harness, per_call) as client:
        issuing = harness.tokens.holds["IssueToken"] = Hold()
        clock.advance(EXPIRES_AT)
        started = time.monotonic()
        try:
            with pytest.raises(errors.MemcoTimeoutError):
                await client.networks.list(timeout=timed(per_call))
            took = time.monotonic() - started
        finally:
            issuing.released.set()
    assert 0.9 * DEADLINE <= took < 1.3 * DEADLINE
    assert harness.admin.calls == []


async def test_async_the_request_gets_only_what_the_wait_left_of_the_deadline(
    clock: FakeClock, harness: Harness, per_call: bool
):
    async with async_deadline_client(harness, per_call) as client:
        issuing = harness.tokens.holds["IssueToken"] = Hold()
        listing = harness.admin.holds["ListNetworks"] = Hold()
        clock.advance(EXPIRES_AT)
        threading.Timer(0.3 * DEADLINE, issuing.released.set).start()
        started = time.monotonic()
        try:
            with pytest.raises(errors.MemcoTimeoutError):
                await client.networks.list(timeout=timed(per_call))
            took = time.monotonic() - started
        finally:
            issuing.released.set()
            listing.released.set()
    assert listing.arrived.is_set()
    assert 0.9 * DEADLINE <= took < 1.2 * DEADLINE


async def test_async_with_a_credential_to_hand_the_request_gets_the_whole_deadline(
    harness: Harness, per_call: bool
):
    async with async_deadline_client(harness, per_call) as client:
        listing = harness.admin.holds["ListNetworks"] = Hold()
        started = time.monotonic()
        try:
            with pytest.raises(errors.MemcoTimeoutError):
                await client.networks.list(timeout=timed(per_call))
            took = time.monotonic() - started
        finally:
            listing.released.set()
    assert 0.9 * DEADLINE <= took < 1.3 * DEADLINE


def test_a_token_whose_renewal_fails_stays_in_use_until_it_expires(
    clock: FakeClock, credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.tokens.error = (grpc.StatusCode.UNAVAILABLE, "token service down")
    clock.advance(0.85 * EXPIRES_AT)
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        credentialed.networks.list()
        # Reported by the renewal, on its own thread, once it has failed.
        for _ in range(500):
            if caplog.records:
                break
            time.sleep(0.01)
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]]
    (warning,) = caplog.records
    assert "client token" in warning.getMessage()
    assert "client-token-1" not in caplog.text
    clock.advance(0.15 * EXPIRES_AT)
    with pytest.raises(errors.MemcoUnavailableError):
        credentialed.networks.list()
    assert harness.admin.calls == ["ListNetworks"]


async def test_async_a_token_whose_renewal_fails_stays_in_use_until_it_expires(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.tokens.error = (grpc.StatusCode.UNAVAILABLE, "token service down")
    clock.advance(0.85 * EXPIRES_AT)
    await async_credentialed.networks.list()
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]]
    clock.advance(0.15 * EXPIRES_AT)
    with pytest.raises(errors.MemcoUnavailableError):
        await async_credentialed.networks.list()
    assert harness.admin.calls == ["ListNetworks"]


def test_a_failed_renewal_of_an_expired_token_is_raised_typed_and_the_next_call_tries_again(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    harness.tokens.transient_errors["IssueToken"] = [
        (grpc.StatusCode.UNAVAILABLE, "token service down")
    ]
    clock.advance(EXPIRES_AT)
    with pytest.raises(errors.MemcoUnavailableError):
        credentialed.networks.list()
    assert harness.admin.calls == []
    credentialed.networks.list()
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-2"]]


def test_callers_waiting_on_a_failed_renewal_share_its_failure(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # One IssueToken, not one per caller: minting again in turn, each would
    # wait out every attempt before its own, far past its deadline.
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    harness.tokens.error = (grpc.StatusCode.UNAVAILABLE, "token service down")
    clock.advance(EXPIRES_AT)
    with ThreadPoolExecutor(max_workers=5) as pool:
        calls = [pool.submit(credentialed.users.list) for _ in range(5)]
        assert issuing.arrived.wait(5)
        time.sleep(0.2)
        issuing.released.set()
        raised = [call.exception(timeout=5) for call in calls]
    assert all(isinstance(error, errors.MemcoUnavailableError) for error in raised)
    assert harness.tokens.calls == ["IssueToken"]
    assert harness.admin.calls == []


def test_a_caller_waits_for_another_s_renewal_no_longer_than_its_own_timeout(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    clock.advance(EXPIRES_AT)
    with ThreadPoolExecutor(max_workers=1) as pool:
        renewing = pool.submit(credentialed.users.list)
        assert issuing.arrived.wait(5)
        waited = time.monotonic()
        with pytest.raises(errors.MemcoTimeoutError):
            credentialed.networks.list(timeout=0.2)
        assert time.monotonic() - waited < 1
        issuing.released.set()
        renewing.result(timeout=5)
    assert harness.tokens.calls == ["IssueToken"]
    assert harness.admin.calls == ["ListExternalUsers"]


async def test_async_callers_waiting_on_a_failed_renewal_share_its_failure(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    harness.tokens.error = (grpc.StatusCode.UNAVAILABLE, "token service down")
    clock.advance(EXPIRES_AT)
    calls = [asyncio.create_task(async_credentialed.users.list()) for _ in range(5)]
    assert await asyncio.to_thread(issuing.arrived.wait, 5)
    await asyncio.sleep(0.1)
    issuing.released.set()
    raised = await asyncio.gather(*calls, return_exceptions=True)
    assert all(isinstance(error, errors.MemcoUnavailableError) for error in raised)
    assert harness.tokens.calls == ["IssueToken"]
    assert harness.admin.calls == []


async def test_async_a_caller_waits_for_another_s_renewal_no_longer_than_its_own_timeout(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    clock.advance(EXPIRES_AT)
    renewing = asyncio.create_task(async_credentialed.users.list())
    assert await asyncio.to_thread(issuing.arrived.wait, 5)
    waited = time.monotonic()
    with pytest.raises(errors.MemcoTimeoutError):
        await async_credentialed.networks.list(timeout=0.2)
    assert time.monotonic() - waited < 1
    issuing.released.set()
    await renewing
    assert harness.tokens.calls == ["IssueToken"]
    assert harness.admin.calls == ["ListExternalUsers"]


async def test_async_admin_calls_carry_the_issued_token(
    async_credentialed: AsyncMemco, harness: Harness
):
    await async_credentialed.networks.list()
    await async_credentialed.users.list()
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]] * 2


async def test_async_the_token_is_renewed_at_four_fifths_of_its_lifetime(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    clock.advance(RENEWS_AT - 1)
    await async_credentialed.networks.list()
    assert harness.tokens.calls == []
    clock.advance(2)
    await async_credentialed.networks.list()
    minting = async_credentialed._credential._minting
    assert minting is not None
    await minting
    await async_credentialed.networks.list()
    assert harness.tokens.calls == ["IssueToken"]
    assert bearers(harness.admin.raw_metadata) == [
        ["Bearer client-token-1"],
        ["Bearer client-token-1"],
        ["Bearer client-token-2"],
    ]


# --- a rejected credential -----------------------------------------------


def test_a_rejected_credential_is_typed_logged_and_closes_the_client(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.tokens.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid client credentials")
    with (
        caplog.at_level(logging.INFO, logger="memcoai"),
        pytest.raises(errors.MemcoAuthenticationError),
    ):
        Memco(**credentials(harness))
    logged = [record.name for record in caplog.records if record.levelno == logging.ERROR]
    assert logged == ["memcoai._sync"]
    assert "closed connection to" in caplog.text
    assert harness.memory.calls == []


async def test_async_a_rejected_credential_is_typed_and_logged(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    harness.tokens.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid client credentials")
    with (
        caplog.at_level(logging.ERROR, logger="memcoai"),
        pytest.raises(errors.MemcoAuthenticationError),
    ):
        async with AsyncMemco(**credentials(harness)):
            pass
    assert [record.name for record in caplog.records] == ["memcoai._aio"]
    assert harness.memory.calls == []


def test_the_secret_never_reaches_a_log_or_an_error(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG, logger="memcoai"):
        with Memco(**credentials(harness)) as connected:
            connected.networks.list()
        harness.tokens.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid client credentials")
        with pytest.raises(errors.MemcoAuthenticationError) as caught:
            Memco(**credentials(harness))
    assert "IssueToken" in caplog.text, "the exchange was not logged at all"
    assert CLIENT_SECRET not in caplog.text
    assert "client-token-1" not in caplog.text
    # grpc's own frames hold the request, secret and all, so its error must not
    # be reachable from this one at all: not as its cause, and not as the
    # context a traceback hides but an error tracker or a debugger still walks.
    assert chain(caught.value) == [caught.value]
    assert CLIENT_SECRET not in str(caught.value)
    assert CLIENT_SECRET not in repr(caught.value)


@pytest.mark.parametrize(
    "given",
    [{"host": "grpc.memco.ai:44x"}, {"log_level": "loud"}, {"token_lifetime": 0}],
    ids=["bad host", "bad log level", "bad lifetime"],
)
@pytest.mark.parametrize("client_class", [Memco, AsyncMemco], ids=["sync", "async"])
def test_a_client_refused_its_configuration_leaves_the_secret_in_no_frame(
    harness: Harness, client_class: type[Memco] | type[AsyncMemco], given: dict[str, Any]
):
    arguments = {**credentials(harness), **given}
    with pytest.raises(errors.MemcoConfigError) as caught:
        client_class(**arguments)
    held = [
        (frame.f_code.co_name, name)
        for frame, _ in traceback.walk_tb(caught.value.__traceback__)
        if frame.f_code.co_filename != __file__
        for name, value in frame.f_locals.items()
        if CLIENT_SECRET in repr(value)
    ]
    assert held == []
    assert CLIENT_SECRET not in repr(caught.value)


def test_a_failed_construction_leaves_the_secret_in_no_frame(harness: Harness):
    # An error tracker capturing locals ships every frame of the traceback,
    # and a local named client_secret is on no usual denylist. The service is
    # reached, and refuses, only after the constructor has taken the secret.
    harness.tokens.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid client credentials")
    with pytest.raises(errors.MemcoAuthenticationError) as caught:
        Memco(**credentials(harness))
    held = [
        (frame.f_code.co_name, name)
        for frame, _ in traceback.walk_tb(caught.value.__traceback__)
        for name, value in frame.f_locals.items()
        if CLIENT_SECRET in repr(value)
    ]
    assert held == []


async def test_async_the_secret_never_reaches_a_log_or_an_error(
    harness: Harness, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG, logger="memcoai"):
        async with AsyncMemco(**credentials(harness)) as connected:
            await connected.networks.list()
        harness.tokens.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid client credentials")
        with pytest.raises(errors.MemcoAuthenticationError) as caught:
            async with AsyncMemco(**credentials(harness)):
                pass
    assert CLIENT_SECRET not in caplog.text
    assert "client-token-1" not in caplog.text
    assert chain(caught.value) == [caught.value]
    assert CLIENT_SECRET not in str(caught.value)
    assert CLIENT_SECRET not in repr(caught.value)


# --- which credential the environment supplies ---------------------------


def test_client_credentials_in_the_environment_win_over_a_token_there(harness: Harness):
    env = {**CLIENT_ENV, "MEMCO_API_TOKEN": TOKEN}
    with Memco(host=harness.address, tls=False, env=env) as connected:
        connected.networks.list()
    assert [sent.client_id for sent in harness.tokens.received] == [CLIENT_ID]
    assert harness.memory.calls == []
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]]


def test_a_token_argument_ignores_client_credentials_in_the_environment(harness: Harness):
    with Memco(token=TOKEN, host=harness.address, tls=False, env=CLIENT_ENV):
        pass
    assert harness.tokens.calls == []
    assert harness.memory.calls == ["ListDomains"]
    assert bearers(harness.memory.raw_metadata) == [[f"Bearer {TOKEN}"]]


async def test_async_client_credentials_in_the_environment_win_over_a_token_there(
    harness: Harness,
):
    env = {**CLIENT_ENV, "MEMCO_API_TOKEN": TOKEN}
    async with AsyncMemco(host=harness.address, tls=False, env=env) as connected:
        await connected.networks.list()
    assert [sent.client_id for sent in harness.tokens.received] == [CLIENT_ID]
    assert harness.memory.calls == []
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-1"]]
