"""Sessions that act as one external user: impersonate, list domains, start, end the key.

Which credential each call carried is read off the wire, call by call. The
failures worth catching are a call sent with the wrong credential -- the
client's token where the user's key belongs, or one user's key on another
user's call -- and a session that reported the right key while sending another
would pass anything read off the SDK.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
import traceback
from collections.abc import Awaitable, Callable
from concurrent import futures
from typing import Any

import grpc
import pytest

from memcoai import AsyncMemco, Memco, errors, types
from memcoai.admin.v1 import admin_pb2 as admin_pb
from memcoai.memory.v1 import memory_pb2 as pb

from .conftest import TOKEN, FakeClock
from .fake_server import FakeAdminService, FakeMemoryService, Harness, Hold

XID = "customer-42"
KEY_1 = f"Bearer impersonation-{XID}-1"
KEY_2 = f"Bearer impersonation-{XID}-2"
CLIENT_TOKEN = "Bearer client-token-1"

KEY_LIFETIME = 600
"""The key lifetime the renewal tests stage, so a key falls due at 480s.

Well inside the client token's own renewal point, so only the key renews.
"""


@pytest.fixture
def clock(clock: FakeClock, harness: Harness) -> FakeClock:
    """The shared fake clock, also dating the keys the fake service mints."""
    harness.admin.clock = clock.time
    return clock


def bearer(metadata: dict[str, str]) -> str:
    return metadata["authorization"]


def recorded(
    service: FakeMemoryService | FakeAdminService,
) -> list[tuple[str, Any, dict[str, str]]]:
    """Each call a service received, as its method, request and metadata.

    Not strict: a call arriving mid-read may be on one list and not yet the
    next, and zip stopping at the shorter leaves exactly the calls fully
    recorded.
    """
    return list(zip(service.calls, service.received, service.metadata, strict=False))


def ends(harness: Harness) -> list[tuple[str, str]]:
    """The external id and key id of every EndImpersonation the service received."""
    return [
        (sent.external_id, sent.key_id)
        for name, sent, _ in recorded(harness.admin)
        if name == "EndImpersonation"
    ]


def carried(harness: Harness, method: str) -> list[str]:
    """The bearer each memory call to one method carried, in order."""
    return [bearer(metadata) for name, _, metadata in recorded(harness.memory) if name == method]


def found() -> pb.SearchResponse:
    return pb.SearchResponse(session_id="session-a", memories=[pb.MemoryResult(idx="memory-a-1")])


def rated() -> pb.ShareFeedbackResponse:
    return pb.ShareFeedbackResponse(
        session_id="session-a",
        entries=[pb.FeedbackEntry(idx="memory-a-1", relevant=True, correct=True)],
    )


def imported() -> list[types.ImportedMemory]:
    return [
        types.ImportedMemory(
            queries=["how does X work"], insights=[types.ImportedInsight(title="t", content="c")]
        )
    ]


async def eventually(done: Callable[[], bool]) -> None:
    """Wait for something a task left running will do, failing if it never does."""
    for _ in range(500):
        if done():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting")


def assert_no_crosstalk(harness: Harness, users: tuple[str, ...], searches: int) -> None:
    """Pair every search with the key it carried, and every end with the key it named."""
    sent = [
        (request.query.split()[0], bearer(metadata))
        for name, request, metadata in recorded(harness.memory)
        if name == "Search"
    ]
    assert len(sent) == len(users) * searches
    for user, sent_with in sent:
        assert sent_with.startswith(f"Bearer impersonation-{user}-"), (user, sent_with)
    # Each key ended is the one its own user's calls carried.
    assert sorted(user for user, _ in ends(harness)) == sorted(users)
    keys = {sent_with for _, sent_with in sent}
    for user, key_id in ends(harness):
        assert f"Bearer impersonation-{user}-{key_id.removeprefix('key-')}" in keys


# --- opening -------------------------------------------------------------


def test_opening_impersonates_then_lists_domains_then_starts_under_the_key(
    credentialed: Memco, harness: Harness
):
    session = credentialed.memory.start_session("coding", external_id=XID)

    # Minted with the client's own token, for the service's default lifetime.
    assert harness.admin.received == [admin_pb.ImpersonateExternalUserRequest(external_id=XID)]
    assert bearer(harness.admin.metadata[0]) == CLIENT_TOKEN
    # Everything after carries the key, starting with the ListDomains that
    # tells this session its limits and any deprecation.
    assert harness.memory.calls == ["ListDomains", "StartSession", "ListTools"]
    assert [bearer(metadata) for metadata in harness.memory.metadata] == [KEY_1] * 3
    assert session.id == "session-a"


def test_with_session_opens_the_same_way(credentialed: Memco, harness: Harness):
    with credentialed.memory.with_session("coding", external_id=XID):
        assert harness.admin.calls == ["ImpersonateExternalUser"]
        assert harness.memory.calls == ["ListDomains", "StartSession", "ListTools"]


async def test_async_opening_impersonates_then_lists_domains_then_starts_under_the_key(
    async_credentialed: AsyncMemco, harness: Harness
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)

    assert harness.admin.received == [admin_pb.ImpersonateExternalUserRequest(external_id=XID)]
    assert bearer(harness.admin.metadata[0]) == CLIENT_TOKEN
    # StartSession and ListTools are sent together, so only ListDomains has a
    # fixed place.
    assert harness.memory.calls[0] == "ListDomains"
    assert sorted(harness.memory.calls[1:]) == ["ListTools", "StartSession"]
    assert [bearer(metadata) for metadata in harness.memory.metadata] == [KEY_1] * 3
    assert session.id == "session-a"


def test_the_session_learns_its_limits_under_its_own_key(credentialed: Memco, harness: Harness):
    harness.memory.responses["ListDomains"] = pb.ListDomainsResponse(
        limits=pb.Limits(max_query_characters=10)
    )
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.memory.clear()
    with pytest.raises(errors.MemcoInvalidRequestError, match="query"):
        session.search("x" * 11)
    assert harness.memory.calls == []


# --- which calls carry the key -------------------------------------------


def test_every_call_through_the_session_carries_the_key(credentialed: Memco, harness: Harness):
    harness.memory.responses["Search"] = found()
    harness.memory.responses["ShareFeedback"] = rated()
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.memory.clear()

    memory = session.search("how does X work").memories[0]
    session.get_memory("memory-a-1")
    session.create_memory(query="q", title="t", content="c")
    session.enrich_memory(memory_idx="new", title="t", content="c")
    session.share_feedback(
        feedback=[types.FeedbackRating(idx="memory-a-1", relevant=True, correct=True)]
    )
    session.revert_memory("create-a")
    session.import_memories(imported())
    session.tools().call("memco_search", {"query": "how does Y work"})
    memory.feedback(relevant=True, correct=True)

    assert harness.memory.calls == [
        "Search",
        "GetMemory",
        "CreateMemory",
        "EnrichMemory",
        "ShareFeedback",
        "RevertMemory",
        "ImportMemories",
        "Search",
        "ShareFeedback",
    ]
    assert [bearer(metadata) for metadata in harness.memory.metadata] == [KEY_1] * 9


async def test_async_every_call_through_the_session_carries_the_key(
    async_credentialed: AsyncMemco, harness: Harness
):
    harness.memory.responses["Search"] = found()
    harness.memory.responses["ShareFeedback"] = rated()
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    harness.memory.clear()

    memory = (await session.search("how does X work")).memories[0]
    await session.get_memory("memory-a-1")
    await session.create_memory(query="q", title="t", content="c")
    await session.enrich_memory(memory_idx="new", title="t", content="c")
    await session.share_feedback(
        feedback=[types.FeedbackRating(idx="memory-a-1", relevant=True, correct=True)]
    )
    await session.revert_memory("create-a")
    await session.import_memories(imported())
    await session.tools().call("memco_search", {"query": "how does Y work"})
    await memory.feedback(relevant=True, correct=True)

    assert len(harness.memory.calls) == 9
    assert [bearer(metadata) for metadata in harness.memory.metadata] == [KEY_1] * 9


def test_calls_on_the_client_keep_the_client_s_own_credential(
    credentialed: Memco, harness: Harness
):
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.memory.clear()
    harness.admin.clear()
    credentialed.networks.list()
    credentialed.memory.list_domains()
    session.search("how does X work")
    assert [bearer(metadata) for metadata in harness.admin.metadata] == [CLIENT_TOKEN]
    assert [bearer(metadata) for metadata in harness.memory.metadata] == [CLIENT_TOKEN, KEY_1]


def test_a_token_client_keeps_its_token_beside_an_impersonated_session(
    client: Memco, harness: Harness
):
    session = client.memory.start_session("coding", external_id=XID)
    # The mint goes out under the static token; whether that token may
    # impersonate is the service's to decide.
    assert bearer(harness.admin.metadata[0]) == f"Bearer {TOKEN}"
    harness.memory.clear()
    client.memory.list_domains()
    session.search("how does X work")
    assert [bearer(metadata) for metadata in harness.memory.metadata] == [f"Bearer {TOKEN}", KEY_1]


def test_a_key_never_reaches_a_log(
    credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.DEBUG, logger="memcoai"):
        with credentialed.memory.with_session("coding", external_id=XID) as session:
            session.search("how does X work")
        credentialed.close()
    assert "Search" in caplog.text, "nothing was logged at all"
    assert f"impersonation-{XID}-" not in caplog.text


def frames_holding(error: BaseException, secret: str) -> list[tuple[str, str]]:
    """The locals, by SDK frame, of the error's own traceback that show the secret.

    Those frames are the other thing an error tracker capturing locals ships,
    and it shows each local as its repr. The test's own frame is left out: it
    holds whatever the test itself put there.
    """
    return [
        (frame.f_code.co_name, name)
        for frame, _ in traceback.walk_tb(error.__traceback__)
        if f"{os.sep}memcoai{os.sep}" in frame.f_code.co_filename
        for name, value in frame.f_locals.items()
        if secret in repr(value)
    ]


def test_a_refused_call_chains_nothing_holding_the_key(credentialed: Memco, harness: Harness):
    # grpc's frames hold the metadata the call was sent with, key and all, and
    # an error tracker capturing locals ships the frames of every exception
    # chained to the one raised.
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.memory.transient_errors["GetMemory"] = [(grpc.StatusCode.NOT_FOUND, "no such memory")]
    with pytest.raises(errors.MemcoNotFoundError) as caught:
        session.get_memory("memory-a-1")
    assert (caught.value.__cause__, caught.value.__context__) == (None, None)
    assert frames_holding(caught.value, f"impersonation-{XID}-") == []


async def test_async_a_refused_call_chains_nothing_holding_the_key(
    async_credentialed: AsyncMemco, harness: Harness
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    harness.memory.transient_errors["GetMemory"] = [(grpc.StatusCode.NOT_FOUND, "no such memory")]
    with pytest.raises(errors.MemcoNotFoundError) as caught:
        await session.get_memory("memory-a-1")
    assert (caught.value.__cause__, caught.value.__context__) == (None, None)
    assert frames_holding(caught.value, f"impersonation-{XID}-") == []


# --- failing to open -----------------------------------------------------

BLANK = [
    pytest.param("coding", "", id="empty external_id"),
    pytest.param("coding", "   ", id="blank external_id"),
    pytest.param("", XID, id="empty domain"),
    pytest.param("  ", XID, id="blank domain"),
]


@pytest.mark.parametrize(("domain", "external_id"), BLANK)
def test_a_blank_external_id_or_domain_sends_nothing(
    credentialed: Memco, harness: Harness, domain: str, external_id: str
):
    with pytest.raises(errors.MemcoInvalidRequestError):
        credentialed.memory.start_session(domain, external_id=external_id)
    assert harness.admin.calls == []
    assert harness.memory.calls == []


@pytest.mark.parametrize(("domain", "external_id"), BLANK)
async def test_async_a_blank_external_id_or_domain_sends_nothing(
    async_credentialed: AsyncMemco, harness: Harness, domain: str, external_id: str
):
    with pytest.raises(errors.MemcoInvalidRequestError):
        await async_credentialed.memory.start_session(domain, external_id=external_id)
    assert harness.admin.calls == []
    assert harness.memory.calls == []


def test_a_refused_impersonation_opens_nothing(credentialed: Memco, harness: Harness):
    harness.admin.transient_errors["ImpersonateExternalUser"] = [
        (grpc.StatusCode.PERMISSION_DENIED, "the API client lacks the admin grant")
    ]
    with pytest.raises(errors.MemcoPermissionError):
        credentialed.memory.start_session("coding", external_id=XID)
    # Nothing was minted, so there is nothing to end.
    assert harness.admin.calls == ["ImpersonateExternalUser"]
    assert harness.memory.calls == []


async def test_async_a_refused_impersonation_opens_nothing(
    async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.transient_errors["ImpersonateExternalUser"] = [
        (grpc.StatusCode.PERMISSION_DENIED, "the API client lacks the admin grant")
    ]
    with pytest.raises(errors.MemcoPermissionError):
        await async_credentialed.memory.start_session("coding", external_id=XID)
    assert harness.admin.calls == ["ImpersonateExternalUser"]
    assert harness.memory.calls == []


@pytest.mark.parametrize("failing", ["ListDomains", "StartSession"])
def test_a_failure_after_the_mint_ends_the_key(credentialed: Memco, harness: Harness, failing: str):
    harness.memory.transient_errors[failing] = [
        (grpc.StatusCode.PERMISSION_DENIED, "no network provisioned for this domain")
    ]
    with pytest.raises(errors.MemcoPermissionError):
        credentialed.memory.start_session("coding", external_id=XID)
    assert ends(harness) == [(XID, "key-1")]
    assert bearer(harness.admin.metadata[-1]) == CLIENT_TOKEN
    assert harness.memory.calls[-1] == failing


@pytest.mark.parametrize("failing", ["ListDomains", "StartSession"])
async def test_async_a_failure_after_the_mint_ends_the_key(
    async_credentialed: AsyncMemco, harness: Harness, failing: str
):
    harness.memory.transient_errors[failing] = [
        (grpc.StatusCode.PERMISSION_DENIED, "no network provisioned for this domain")
    ]
    with pytest.raises(errors.MemcoPermissionError):
        await async_credentialed.memory.start_session("coding", external_id=XID)
    # A ListTools sent beside a failed StartSession may still hold the key, in
    # which case its end waits for that call to finish.
    await eventually(lambda: bool(ends(harness)))
    assert ends(harness) == [(XID, "key-1")]


# --- closing -------------------------------------------------------------


def test_leaving_the_block_ends_the_key_once(credentialed: Memco, harness: Harness):
    with credentialed.memory.with_session("coding", external_id=XID) as session:
        session.search("how does X work")
        assert ends(harness) == []
    assert ends(harness) == [(XID, "key-1")]
    assert bearer(harness.admin.metadata[-1]) == CLIENT_TOKEN
    session.close()
    assert ends(harness) == [(XID, "key-1")]


def test_a_closed_session_refuses_every_call_locally(credentialed: Memco, harness: Harness):
    harness.memory.responses["Search"] = found()
    session = credentialed.memory.start_session("coding", external_id=XID)
    memory = session.search("how does X work").memories[0]
    toolset = session.tools()
    session.close()
    session.close()
    harness.memory.clear()

    with pytest.raises(errors.MemcoConfigError, match="closed"):
        session.search("how does X work")
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        toolset.call("memco_search", {"query": "how does X work"})
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        memory.feedback(relevant=True, correct=True)
    assert harness.memory.calls == []
    # Refused, not quietly re-minted.
    assert harness.admin.calls == ["ImpersonateExternalUser", "EndImpersonation"]


async def test_async_a_closed_session_refuses_every_call_locally(
    async_credentialed: AsyncMemco, harness: Harness
):
    harness.memory.responses["Search"] = found()
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    memory = (await session.search("how does X work")).memories[0]
    toolset = session.tools()
    await session.close()
    await session.close()
    harness.memory.clear()

    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await session.search("how does X work")
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await toolset.call("memco_search", {"query": "how does X work"})
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await memory.feedback(relevant=True, correct=True)
    assert harness.memory.calls == []
    assert harness.admin.calls == ["ImpersonateExternalUser", "EndImpersonation"]


def test_closing_a_plain_session_changes_nothing(client: Memco, harness: Harness):
    with client.memory.with_session("coding") as session:
        pass
    session.close()
    session.search("how does X work")
    assert harness.admin.calls == []
    assert harness.memory.calls[-1] == "Search"


async def test_async_closing_a_plain_session_changes_nothing(
    async_client: AsyncMemco, harness: Harness
):
    async with async_client.memory.with_session("coding") as session:
        pass
    await session.close()
    await session.search("how does X work")
    assert harness.admin.calls == []
    assert harness.memory.calls[-1] == "Search"


async def await_it(client: AsyncMemco) -> None:
    session = await client.memory.with_session("coding", external_id=XID)
    await session.search("how does X work")
    await session.close()


async def enter_it(client: AsyncMemco) -> None:
    async with client.memory.with_session("coding", external_id=XID) as session:
        await session.search("how does X work")


async def enter_what_was_awaited(client: AsyncMemco) -> None:
    async with await client.memory.with_session("coding", external_id=XID) as session:
        await session.search("how does X work")


@pytest.mark.parametrize(
    "use",
    [await_it, enter_it, enter_what_was_awaited],
    ids=["await", "async with", "async with await"],
)
async def test_each_async_opener_form_ends_the_key_once(
    async_credentialed: AsyncMemco, harness: Harness, use: Callable[[AsyncMemco], Awaitable[None]]
):
    await use(async_credentialed)
    assert ends(harness) == [(XID, "key-1")]
    await async_credentialed.close()
    assert ends(harness) == [(XID, "key-1")]


async def test_two_awaits_of_one_opener_share_one_key(
    async_credentialed: AsyncMemco, harness: Harness
):
    # Each await is its own coroutine: gather would collapse the same
    # awaitable passed twice into one.
    opener = async_credentialed.memory.with_session("coding", external_id=XID)

    async def open_it() -> object:
        return await opener

    first, second = await asyncio.gather(open_it(), open_it())
    assert first is second
    assert harness.admin.calls == ["ImpersonateExternalUser"]
    assert harness.memory.calls.count("StartSession") == 1


# --- renewal -------------------------------------------------------------


def test_a_due_key_is_renewed_and_the_old_one_ended(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(0.8 * KEY_LIFETIME - 1)
    session.search("before")
    assert harness.admin.calls == ["ImpersonateExternalUser"]
    clock.advance(2)
    session.search("after")
    assert harness.admin.calls == [
        "ImpersonateExternalUser",
        "ImpersonateExternalUser",
        "EndImpersonation",
    ]
    assert ends(harness) == [(XID, "key-1")]
    assert carried(harness, "Search") == [KEY_1, KEY_2]


async def test_async_a_due_key_is_renewed_and_the_old_one_ended(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(0.8 * KEY_LIFETIME - 1)
    await session.search("before")
    assert harness.admin.calls == ["ImpersonateExternalUser"]
    clock.advance(2)
    await session.search("after")
    assert ends(harness) == [(XID, "key-1")]
    assert carried(harness, "Search") == [KEY_1, KEY_2]


def test_renewal_never_ends_a_key_under_a_call_still_using_it(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    hold = harness.memory.holds["Search"] = Hold()
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(session.search, "held")
        assert hold.arrived.wait(5)
        clock.advance(0.8 * KEY_LIFETIME + 1)
        session.get_memory("memory-a-1")
        # The new key serves new calls, but the old one still carries the
        # held search, so ending it now would fail that search.
        assert ends(harness) == []
        hold.released.set()
        held.result(timeout=5)
    assert ends(harness) == [(XID, "key-1")]
    assert carried(harness, "Search") == [KEY_1]
    assert carried(harness, "GetMemory") == [KEY_2]


async def test_async_renewal_never_ends_a_key_under_a_call_still_using_it(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    hold = harness.memory.holds["Search"] = Hold()
    held = asyncio.create_task(session.search("held"))
    assert await asyncio.to_thread(hold.arrived.wait, 5)
    clock.advance(0.8 * KEY_LIFETIME + 1)
    await session.get_memory("memory-a-1")
    assert ends(harness) == []
    hold.released.set()
    await held
    assert ends(harness) == [(XID, "key-1")]
    assert carried(harness, "Search") == [KEY_1]
    assert carried(harness, "GetMemory") == [KEY_2]


# --- ending a key --------------------------------------------------------


def test_a_failed_end_is_logged_and_retried_when_the_client_closes(
    credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ]
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        session.close()
    # Named by its id, which revokes nothing, and never by its value.
    (warning,) = caplog.records
    assert "key-1" in warning.getMessage()
    assert f"impersonation-{XID}-" not in caplog.text
    credentialed.close()
    assert ends(harness) == [(XID, "key-1"), (XID, "key-1")]


async def test_async_a_failed_end_is_logged_and_retried_when_the_client_closes(
    async_credentialed: AsyncMemco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ]
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        await session.close()
    (warning,) = caplog.records
    assert "key-1" in warning.getMessage()
    assert f"impersonation-{XID}-" not in caplog.text
    await async_credentialed.close()
    assert ends(harness) == [(XID, "key-1"), (XID, "key-1")]


def test_an_end_answered_not_found_counts_as_ended(
    credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    # The key expired, or its user was deleted: either way nothing is left
    # to end, and nothing is wrong.
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.NOT_FOUND, "impersonation key not found")
    ]
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        session.close()
        credentialed.close()
    assert caplog.records == []
    assert ends(harness) == [(XID, "key-1")]


async def test_async_an_end_answered_not_found_counts_as_ended(
    async_credentialed: AsyncMemco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.NOT_FOUND, "impersonation key not found")
    ]
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        await session.close()
        await async_credentialed.close()
    assert caplog.records == []
    assert ends(harness) == [(XID, "key-1")]


# --- closing the client --------------------------------------------------


def test_closing_the_client_ends_the_keys_of_sessions_left_open(
    credentialed: Memco, harness: Harness
):
    credentialed.memory.start_session("coding", external_id="u1")
    credentialed.memory.start_session("coding", external_id="u2")
    credentialed.close()
    assert sorted(ends(harness)) == [("u1", "key-1"), ("u2", "key-2")]
    assert [
        bearer(metadata)
        for name, _, metadata in recorded(harness.admin)
        if name == "EndImpersonation"
    ] == [CLIENT_TOKEN] * 2


async def test_async_closing_the_client_ends_the_keys_of_sessions_left_open(
    async_credentialed: AsyncMemco, harness: Harness
):
    await async_credentialed.memory.start_session("coding", external_id="u1")
    await async_credentialed.memory.start_session("coding", external_id="u2")
    await async_credentialed.close()
    assert sorted(ends(harness)) == [("u1", "key-1"), ("u2", "key-2")]


def test_a_session_closed_after_its_client_is_a_quiet_no_op(
    credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    session = credentialed.memory.start_session("coding", external_id=XID)
    credentialed.close()
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        session.close()
    assert caplog.records == []
    assert ends(harness) == [(XID, "key-1")]


async def test_async_a_session_closed_after_its_client_is_a_quiet_no_op(
    async_credentialed: AsyncMemco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    await async_credentialed.close()
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        await session.close()
    assert caplog.records == []
    assert ends(harness) == [(XID, "key-1")]


def test_the_close_sweep_counts_not_found_as_ended_and_goes_on(
    credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    credentialed.memory.start_session("coding", external_id="u1")
    credentialed.memory.start_session("coding", external_id="u2")
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.NOT_FOUND, "impersonation key not found")
    ]
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        credentialed.close()
    assert caplog.records == []
    assert ends(harness) == [("u1", "key-1"), ("u2", "key-2")]


async def test_async_the_close_sweep_counts_not_found_as_ended_and_goes_on(
    async_credentialed: AsyncMemco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    await async_credentialed.memory.start_session("coding", external_id="u1")
    await async_credentialed.memory.start_session("coding", external_id="u2")
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.NOT_FOUND, "impersonation key not found")
    ]
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        await async_credentialed.close()
    assert caplog.records == []
    assert ends(harness) == [("u1", "key-1"), ("u2", "key-2")]


# Run in a subprocess, like the loop tests in test_regressions.py: it is about
# event-loop lifecycle, so it must not share a loop, or grpc.aio's global
# state, with the rest of the suite.
CLOSED_ON_A_NEW_LOOP = """
import asyncio
import sys

from memcoai import AsyncMemco

client = AsyncMemco(client_id="c", client_secret="s", host=sys.argv[1], tls=False)


async def left_open() -> None:
    await client.connect()
    await client.memory.start_session("coding", external_id="customer-42")


asyncio.run(left_open())
asyncio.run(client.close())
print("closed")
"""


def test_a_client_closed_on_a_new_loop_still_ends_the_keys_left_open(harness: Harness, tmp_path):
    # The channel the key was minted on belongs to a loop that has gone, so
    # ending the key needs one built on the loop doing the closing.
    source = tmp_path / "script.py"
    source.write_text(CLOSED_ON_A_NEW_LOOP)
    finished = subprocess.run(  # noqa: S603 - the interpreter and script are ours
        [sys.executable, str(source), harness.address],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert "closed" in finished.stdout
    assert ends(harness) == [(XID, "key-1")]
    assert "was never awaited" not in finished.stderr
    assert "attached to a different loop" not in finished.stderr


def test_closing_the_client_waits_for_calls_in_flight_before_ending_keys(
    credentialed: Memco, harness: Harness
):
    session = credentialed.memory.start_session("coding", external_id=XID)
    hold = harness.memory.holds["Search"] = Hold()
    with futures.ThreadPoolExecutor(max_workers=2) as pool:
        held = pool.submit(session.search, "held")
        assert hold.arrived.wait(5)
        closing = pool.submit(credentialed.close)
        _, waiting = futures.wait([closing], timeout=0.2)
        # Still waiting on the search, which still needs its key.
        assert closing in waiting
        assert ends(harness) == []
        hold.released.set()
        assert held.result(timeout=5).session_id == "session-a"
        closing.result(timeout=5)
    assert ends(harness) == [(XID, "key-1")]


async def test_async_closing_the_client_waits_for_calls_in_flight_before_ending_keys(
    async_credentialed: AsyncMemco, harness: Harness
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    hold = harness.memory.holds["Search"] = Hold()
    held = asyncio.create_task(session.search("held"))
    assert await asyncio.to_thread(hold.arrived.wait, 5)
    closing = asyncio.create_task(async_credentialed.close())
    await asyncio.sleep(0.2)
    assert not closing.done()
    assert ends(harness) == []
    hold.released.set()
    assert (await held).session_id == "session-a"
    await closing
    assert ends(harness) == [(XID, "key-1")]


def test_the_close_sweep_stops_at_the_first_failure_with_one_warning(
    credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    for user in ("u1", "u2", "u3"):
        credentialed.memory.start_session("coding", external_id=user)
    harness.admin.error = (grpc.StatusCode.UNAVAILABLE, "admin service down")
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        credentialed.close()
    # One refusal says the service cannot be reached; asking twice more would
    # only make close() slower. The keys expire on their own.
    assert len(ends(harness)) == 1
    (warning,) = [record for record in caplog.records if record.levelno >= logging.WARNING]
    for key_id in ("key-1", "key-2", "key-3"):
        assert key_id in warning.getMessage()
    assert "Bearer" not in caplog.text
    assert "impersonation-u" not in caplog.text


async def test_async_the_close_sweep_stops_at_the_first_failure_with_one_warning(
    async_credentialed: AsyncMemco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    for user in ("u1", "u2", "u3"):
        await async_credentialed.memory.start_session("coding", external_id=user)
    harness.admin.error = (grpc.StatusCode.UNAVAILABLE, "admin service down")
    with caplog.at_level(logging.WARNING, logger="memcoai"):
        await async_credentialed.close()
    assert len(ends(harness)) == 1
    (warning,) = [record for record in caplog.records if record.levelno >= logging.WARNING]
    for key_id in ("key-1", "key-2", "key-3"):
        assert key_id in warning.getMessage()
    assert "impersonation-u" not in caplog.text


async def test_a_cancelled_open_ends_the_key_it_goes_on_to_mint(
    async_credentialed: AsyncMemco, harness: Harness
):
    # Cancelled while the mint is in flight -- an open under wait_for that
    # timed out: the service mints the key anyway, and the cancelled open
    # never learns it. Left for the client's close, it would count against
    # the user's cap for as long as a long-lived client runs.
    hold = harness.admin.holds["ImpersonateExternalUser"] = Hold()
    opening = asyncio.create_task(
        async_credentialed.memory.start_session("coding", external_id=XID)
    )
    assert await asyncio.to_thread(hold.arrived.wait, 5)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    hold.released.set()
    await eventually(lambda: bool(ends(harness)))
    assert ends(harness) == [(XID, "key-1")]
    assert harness.memory.calls == []
    await async_credentialed.close()
    assert ends(harness) == [(XID, "key-1")]


async def test_a_close_cancelled_part_way_still_ends_the_keys(
    async_credentialed: AsyncMemco, harness: Harness
):
    # Cancelled while it waits for a call in flight -- a task group torn down
    # around it -- the close must still finish, or every later close() would
    # return at once with the keys still live and the channel open.
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    hold = harness.memory.holds["Search"] = Hold()
    held = asyncio.create_task(session.search("held"))
    assert await asyncio.to_thread(hold.arrived.wait, 5)
    closing = asyncio.create_task(async_credentialed.close())
    await asyncio.sleep(0.1)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    hold.released.set()
    assert (await held).session_id == "session-a"
    await async_credentialed.close()
    assert ends(harness) == [(XID, "key-1")]


# --- crosstalk -----------------------------------------------------------


async def test_concurrent_sessions_for_two_users_never_cross_keys(
    async_credentialed: AsyncMemco, harness: Harness
):
    async def act_for(user: str) -> None:
        async with async_credentialed.memory.with_session("coding", external_id=user) as session:
            await asyncio.gather(*(session.search(f"{user} query {n}") for n in range(10)))

    await asyncio.gather(act_for("u1"), act_for("u2"))
    assert_no_crosstalk(harness, ("u1", "u2"), 10)


def test_two_threads_acting_for_two_users_never_cross_keys(credentialed: Memco, harness: Harness):
    # Neither searches until both sessions are open. Otherwise one thread may
    # finish before the other starts, and a key kept on state the two share
    # would be right for each by luck.
    both_open = threading.Barrier(2)

    def act_for(user: str) -> None:
        with credentialed.memory.with_session("coding", external_id=user) as session:
            both_open.wait(timeout=10)
            for n in range(10):
                session.search(f"{user} query {n}")

    with futures.ThreadPoolExecutor(max_workers=2) as pool:
        for running in [pool.submit(act_for, user) for user in ("u1", "u2")]:
            running.result(timeout=10)
    assert_no_crosstalk(harness, ("u1", "u2"), 10)
