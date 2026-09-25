"""Client credentials: an API client's id and secret, exchanged for a token the SDK renews.

The exchange is ``TokenService.IssueToken``, the one call sent with no bearer
of its own: the credentials in the request are what authenticate it. What each
call carried is read off the wire, since that is where a wrong or missing
credential would show.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2

from memcoai import AsyncMemco, Memco, errors
from memcoai.auth.v1 import auth_pb2 as auth_pb

from .conftest import CLIENT_ID, CLIENT_SECRET, TOKEN, FakeClock
from .fake_server import Harness

RENEWS_AT = 0.8 * 3600
"""Seconds after issue that the fake's default token falls due for renewal."""

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
    credentialed.networks.list()
    assert harness.tokens.calls == ["IssueToken"]
    assert bearers(harness.admin.raw_metadata) == [
        ["Bearer client-token-1"],
        ["Bearer client-token-2"],
    ]


def test_a_failed_renewal_is_raised_typed_and_the_next_call_tries_again(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    harness.tokens.transient_errors["IssueToken"] = [
        (grpc.StatusCode.UNAVAILABLE, "token service down")
    ]
    clock.advance(RENEWS_AT + 1)
    with pytest.raises(errors.MemcoUnavailableError):
        credentialed.networks.list()
    assert harness.admin.calls == []
    credentialed.networks.list()
    assert bearers(harness.admin.raw_metadata) == [["Bearer client-token-2"]]


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
    assert harness.tokens.calls == ["IssueToken"]
    assert bearers(harness.admin.raw_metadata) == [
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
