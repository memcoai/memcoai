"""Sessions that act as one external user: impersonate, list domains, start, end the key.

Which credential each call carried is read off the wire, call by call. The
failures worth catching are a call sent with the wrong credential -- the
client's token where the user's key belongs, or one user's key on another
user's call -- and a session that reported the right key while sending another
would pass anything read off the SDK.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import warnings
from collections.abc import Awaitable, Callable
from concurrent import futures
from typing import Any

import grpc
import pytest

import memcoai
from memcoai import AsyncMemco, Memco, _auth, errors, types
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

TOKEN_EXPIRES_AT = 3600
"""Seconds after issue that the fake's default client token expires."""


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


def soon(done: Callable[[], bool]) -> None:
    """Wait for something a thread left running will do, failing if it never does."""
    for _ in range(500):
        if done():
            return
        time.sleep(0.01)
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


SDK_DIRECTORY = os.path.dirname(memcoai.__file__) + os.sep


def frames_holding(error: BaseException, secret: str) -> list[tuple[str, str]]:
    """The locals, by SDK frame, of the error's own traceback that show the secret.

    Those frames are the other thing an error tracker capturing locals ships,
    and it shows each local as its repr. The test's own frame is left out: it
    holds whatever the test itself put there. SDK frames are told apart by the
    package's own directory, not by a path segment named after it: a checkout
    can sit under a directory called memcoai too, as CI's does.
    """
    return [
        (frame.f_code.co_name, name)
        for frame, _ in traceback.walk_tb(error.__traceback__)
        if frame.f_code.co_filename.startswith(SDK_DIRECTORY)
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
    # Still honoured, so the call that sets the renewal off carries it; the
    # old key is ended once the new one lands.
    session.search("after")
    soon(lambda: ends(harness) == [(XID, "key-1")])
    session.search("later")
    assert harness.admin.calls == [
        "ImpersonateExternalUser",
        "ImpersonateExternalUser",
        "EndImpersonation",
    ]
    assert carried(harness, "Search") == [KEY_1, KEY_1, KEY_2]


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
    await eventually(lambda: ends(harness) == [(XID, "key-1")])
    await session.search("later")
    assert carried(harness, "Search") == [KEY_1, KEY_1, KEY_2]


def test_renewal_never_ends_a_key_under_a_call_still_using_it(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    hold = harness.memory.holds["Search"] = Hold()
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(session.search, "held")
        assert hold.arrived.wait(5)
        # Expired, so the next call waits for the new key.
        clock.advance(KEY_LIFETIME)
        session.get_memory("memory-a-1")
        # The new key serves new calls, but the old one still carries the
        # held search, so ending it now would fail that search.
        assert ends(harness) == []
        hold.released.set()
        held.result(timeout=5)
    soon(lambda: ends(harness) == [(XID, "key-1")])
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
    clock.advance(KEY_LIFETIME)
    await session.get_memory("memory-a-1")
    assert ends(harness) == []
    hold.released.set()
    await held
    await eventually(lambda: ends(harness) == [(XID, "key-1")])
    assert carried(harness, "Search") == [KEY_1]
    assert carried(harness, "GetMemory") == [KEY_2]


def test_a_key_whose_renewal_is_refused_stays_in_use_until_it_expires(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # The user's live keys are at the service's cap, so no renewal can be
    # minted until one is ended; the key held still works until it expires.
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.key_cap = 1
    clock.advance(0.85 * KEY_LIFETIME)
    session.search("still honoured")
    assert carried(harness, "Search") == [KEY_1]
    clock.advance(0.15 * KEY_LIFETIME)
    with pytest.raises(errors.MemcoResourceExhaustedError):
        session.search("expired")
    assert carried(harness, "Search") == [KEY_1]


async def test_async_a_key_whose_renewal_is_refused_stays_in_use_until_it_expires(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.key_cap = 1
    clock.advance(0.85 * KEY_LIFETIME)
    await session.search("still honoured")
    assert carried(harness, "Search") == [KEY_1]
    clock.advance(0.15 * KEY_LIFETIME)
    with pytest.raises(errors.MemcoResourceExhaustedError):
        await session.search("expired")
    assert carried(harness, "Search") == [KEY_1]


def test_a_key_due_for_renewal_serves_calls_while_it_renews(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # The key held still works, so a slow mint holds up no call.
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    minting = harness.admin.holds["ImpersonateExternalUser"] = Hold()
    clock.advance(0.85 * KEY_LIFETIME)
    try:
        with futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(session.search, "during", timeout=0.5).result(timeout=2)
        assert minting.arrived.wait(5)
    finally:
        minting.released.set()
    soon(lambda: ends(harness) == [(XID, "key-1")])
    session.search("after")
    assert carried(harness, "Search") == [KEY_1, KEY_2]


async def test_async_a_key_due_for_renewal_serves_calls_while_it_renews(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    minting = harness.admin.holds["ImpersonateExternalUser"] = Hold()
    clock.advance(0.85 * KEY_LIFETIME)
    try:
        await asyncio.wait_for(session.search("during", timeout=0.5), 2)
        assert await asyncio.to_thread(minting.arrived.wait, 5)
    finally:
        minting.released.set()
    await eventually(lambda: ends(harness) == [(XID, "key-1")])
    await session.search("after")
    assert carried(harness, "Search") == [KEY_1, KEY_2]


def test_the_call_that_renews_an_expired_key_does_not_wait_for_the_old_one_to_end(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # It has to wait for the new key, but not for the service to confirm the
    # old one ended: the old one is ended either way, and the new is current.
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    clock.advance(KEY_LIFETIME)
    try:
        with futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(session.search, "after", timeout=1).result(timeout=2)
        assert ending.arrived.wait(5)
    finally:
        ending.released.set()
    assert carried(harness, "Search") == [KEY_2]
    soon(lambda: ends(harness) == [(XID, "key-1")])


async def test_async_the_call_that_renews_an_expired_key_does_not_wait_for_the_old_one_to_end(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    clock.advance(KEY_LIFETIME)
    try:
        await asyncio.wait_for(session.search("after", timeout=1), 2)
        assert await asyncio.to_thread(ending.arrived.wait, 5)
    finally:
        ending.released.set()
    assert carried(harness, "Search") == [KEY_2]
    await eventually(lambda: ends(harness) == [(XID, "key-1")])


def test_the_last_call_on_a_replaced_key_does_not_wait_for_it_to_end(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    searching = harness.memory.holds["Search"] = Hold()
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    try:
        with futures.ThreadPoolExecutor(max_workers=1) as pool:
            held = pool.submit(session.search, "held")
            assert searching.arrived.wait(5)
            clock.advance(KEY_LIFETIME)
            session.get_memory("memory-a-1")
            searching.released.set()
            held.result(timeout=2)
        assert ending.arrived.wait(5)
    finally:
        searching.released.set()
        ending.released.set()
    assert carried(harness, "GetMemory") == [KEY_2]
    soon(lambda: ends(harness) == [(XID, "key-1")])


async def test_async_the_last_call_on_a_replaced_key_does_not_wait_for_it_to_end(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_lifetime = KEY_LIFETIME
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    searching = harness.memory.holds["Search"] = Hold()
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    try:
        held = asyncio.create_task(session.search("held"))
        assert await asyncio.to_thread(searching.arrived.wait, 5)
        clock.advance(KEY_LIFETIME)
        await session.get_memory("memory-a-1")
        searching.released.set()
        await asyncio.wait_for(held, 2)
        assert await asyncio.to_thread(ending.arrived.wait, 5)
    finally:
        searching.released.set()
        ending.released.set()
    assert carried(harness, "GetMemory") == [KEY_2]
    await eventually(lambda: ends(harness) == [(XID, "key-1")])


def test_an_open_keeps_to_its_own_timeout_when_the_key_is_slow_to_mint(
    credentialed: Memco, harness: Harness
):
    # The key is minted all the same, and ended once it arrives, since
    # nothing is left to use it.
    minting = harness.admin.holds["ImpersonateExternalUser"] = Hold()
    try:
        with futures.ThreadPoolExecutor(max_workers=1) as pool:
            opening = pool.submit(
                credentialed.memory.start_session, "coding", external_id=XID, timeout=0.3
            )
            with pytest.raises(errors.MemcoTimeoutError):
                opening.result(timeout=2)
    finally:
        minting.released.set()
    soon(lambda: ends(harness) == [(XID, "key-1")])
    assert harness.memory.calls == []


async def test_async_an_open_keeps_to_its_own_timeout_when_the_key_is_slow_to_mint(
    async_credentialed: AsyncMemco, harness: Harness
):
    minting = harness.admin.holds["ImpersonateExternalUser"] = Hold()
    try:
        with pytest.raises(errors.MemcoTimeoutError):
            await asyncio.wait_for(
                async_credentialed.memory.start_session("coding", external_id=XID, timeout=0.3), 2
            )
    finally:
        minting.released.set()
    await eventually(lambda: ends(harness) == [(XID, "key-1")])
    assert harness.memory.calls == []


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


def test_a_failed_end_is_retried_before_the_next_key_for_the_same_user(
    credentialed: Memco, harness: Harness
):
    # Left for the client's close, it would hold one of the user's live keys
    # for as long as a long-lived client runs, or until it expired.
    session = credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ]
    session.close()
    _other = credentialed.memory.start_session("coding", external_id="someone-else")
    assert ends(harness) == [(XID, "key-1")]
    _again = credentialed.memory.start_session("coding", external_id=XID)
    assert ends(harness) == [(XID, "key-1"), (XID, "key-1")]
    assert harness.admin.calls[-2:] == ["EndImpersonation", "ImpersonateExternalUser"]
    assert list(credentialed._live) == ["key-2", "key-3"]


async def test_async_a_failed_end_is_retried_before_the_next_key_for_the_same_user(
    async_credentialed: AsyncMemco, harness: Harness
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ]
    await session.close()
    _other = await async_credentialed.memory.start_session("coding", external_id="someone-else")
    assert ends(harness) == [(XID, "key-1")]
    _again = await async_credentialed.memory.start_session("coding", external_id=XID)
    assert ends(harness) == [(XID, "key-1"), (XID, "key-1")]
    assert harness.admin.calls[-2:] == ["EndImpersonation", "ImpersonateExternalUser"]
    assert list(async_credentialed._live) == ["key-2", "key-3"]


def test_retrying_failed_ends_stops_at_the_first_the_service_still_refuses(
    credentialed: Memco, harness: Harness
):
    # A service that cannot end one key now will refuse the next too, and
    # asking for each would hold up the mint by a round trip apiece.
    first = credentialed.memory.start_session("coding", external_id=XID)
    second = credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ] * 3
    first.close()
    second.close()
    _third = credentialed.memory.start_session("coding", external_id=XID)
    assert ends(harness) == [(XID, "key-1"), (XID, "key-2"), (XID, "key-1")]
    assert [key.failed for key in credentialed._live.values()] == [True, True, False]


async def test_async_retrying_failed_ends_stops_at_the_first_the_service_still_refuses(
    async_credentialed: AsyncMemco, harness: Harness
):
    first = await async_credentialed.memory.start_session("coding", external_id=XID)
    second = await async_credentialed.memory.start_session("coding", external_id=XID)
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ] * 3
    await first.close()
    await second.close()
    _third = await async_credentialed.memory.start_session("coding", external_id=XID)
    assert ends(harness) == [(XID, "key-1"), (XID, "key-2"), (XID, "key-1")]
    assert [key.failed for key in async_credentialed._live.values()] == [True, True, False]


def test_a_key_that_has_expired_is_forgotten_at_the_next_mint(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # The registry would otherwise keep every key whose end failed for the
    # life of the client, and close() would end each, long after the service
    # stopped honouring it.
    session = credentialed.memory.start_session("coding", external_id="u1")
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ]
    session.close()
    clock.advance(3600)
    _left_open = credentialed.memory.start_session("coding", external_id="u2")
    assert list(credentialed._live) == ["key-2"]
    credentialed.close()
    assert ends(harness) == [("u1", "key-1"), ("u2", "key-2")]


async def test_async_a_key_that_has_expired_is_forgotten_at_the_next_mint(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    session = await async_credentialed.memory.start_session("coding", external_id="u1")
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ]
    await session.close()
    clock.advance(3600)
    _left_open = await async_credentialed.memory.start_session("coding", external_id="u2")
    assert list(async_credentialed._live) == ["key-2"]
    await async_credentialed.close()
    assert ends(harness) == [("u1", "key-1"), ("u2", "key-2")]


def test_the_close_sweep_skips_keys_that_have_expired(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    _left_open = credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(3600)
    credentialed.close()
    assert ends(harness) == []


async def test_async_the_close_sweep_skips_keys_that_have_expired(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    _left_open = await async_credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(3600)
    await async_credentialed.close()
    assert ends(harness) == []


# --- closing the client --------------------------------------------------


def test_closing_the_client_ends_the_keys_of_sessions_left_open(
    credentialed: Memco, harness: Harness
):
    # Held, not dropped: a session garbage-collected unclosed has its key
    # ended by the client's next call instead.
    _left_open = [
        credentialed.memory.start_session("coding", external_id=user) for user in ("u1", "u2")
    ]
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
    _left_open = [
        await async_credentialed.memory.start_session("coding", external_id=user)
        for user in ("u1", "u2")
    ]
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
    _left_open = [
        credentialed.memory.start_session("coding", external_id=user) for user in ("u1", "u2")
    ]
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
    _left_open = [
        await async_credentialed.memory.start_session("coding", external_id=user)
        for user in ("u1", "u2")
    ]
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


# Work left on an event loop that has gone -- one closed without cancelling
# its tasks, or torn down by asyncio.run() with a call in flight -- can never
# finish. Each script runs its own fake server, in a process of its own, and
# prints what the next loop managed to do.
LOOP_PRELUDE = """
import asyncio, gc, logging, sys, time, warnings

warnings.simplefilter("ignore", ResourceWarning)
from memcoai import AsyncMemco, _auth
from tests.conftest import CLIENT_ID, CLIENT_SECRET, FakeClock
from tests.fake_server import Harness, Hold

clock = FakeClock()
_auth.monotonic, _auth.time = clock.monotonic, clock.time
harness = Harness()
harness.admin.clock = clock.time
client = AsyncMemco(
    client_id=CLIENT_ID, client_secret=CLIENT_SECRET, host=harness.address, tls=False,
    log_level="none",
)


def job(coro):
    # A fresh loop per job, closed without cancelling what it left pending.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def bounded(coro):
    return await asyncio.wait_for(coro, 5)
"""

RENEWAL_LEFT_ON_A_CLOSED_LOOP = """
harness.tokens.expires_in = 100
job(client.connect())
clock.advance(85)
issuing = harness.tokens.holds["IssueToken"] = Hold()
job(client.networks.list())
issuing.released.set()
del harness.tokens.holds["IssueToken"]
clock.advance(20)
job(bounded(client.networks.list()))
print("issued", harness.tokens.calls.count("IssueToken"))
job(bounded(client.close()))
print("closed")
"""

END_LEFT_ON_A_CLOSED_LOOP = """
async def open_and_drop():
    await client.connect()
    await client.memory.start_session("coding", external_id="u1")
    gc.collect()

job(open_and_drop())
ending = harness.admin.holds["EndImpersonation"] = Hold()
job(client.networks.list())
ending.released.set()
del harness.admin.holds["EndImpersonation"]
job(bounded(client.memory.start_session("coding", external_id="u1")))
print("opened")
job(bounded(client.close()))
print("closed")
"""

CALL_CUT_SHORT_BY_ASYNCIO_RUN = """
harness.admin.key_lifetime = 600
kept = []

async def first_run():
    await client.connect()
    session = await client.memory.start_session("coding", external_id="u1")
    kept.append(session)
    clock.advance(0.85 * 600)
    searching = harness.memory.holds["Search"] = Hold()
    harness.admin.holds["EndImpersonation"] = Hold()
    asyncio.ensure_future(session.search("left in flight"))
    await asyncio.to_thread(searching.arrived.wait, 5)
    while "key-2" not in client._live:
        await asyncio.sleep(0.01)

asyncio.run(first_run())
harness.memory.holds.pop("Search").released.set()
harness.admin.holds.pop("EndImpersonation").released.set()

async def second_run():
    await bounded(client.memory.start_session("coding", external_id="u1"))
    print("opened")
    await bounded(client.close())
    print("closed")

asyncio.run(second_run())
"""

END_CUT_SHORT_BY_ASYNCIO_RUN = """
from tests.fake_server import FakeAdminService
from memcoai.admin.v1 import admin_pb2

ends_as_called = FakeAdminService.EndImpersonation

def lost_while_held(self, request, context):
    # The end cut short with its run never reaches the service: the held one
    # is lost, as a real service would not commit it once its caller went.
    hold = self.holds.get("EndImpersonation")
    if hold is None:
        return ends_as_called(self, request, context)
    self._arrive("EndImpersonation", context, request)
    hold.released.wait(5)
    return admin_pb2.EndImpersonationResponse()

FakeAdminService.EndImpersonation = lost_while_held
# Rebuilt, since a server binds its handlers as it is built.
harness.stop()
harness = Harness()
harness.admin.clock = clock.time
client = AsyncMemco(
    client_id=CLIENT_ID, client_secret=CLIENT_SECRET, host=harness.address, tls=False,
    log_level="none",
)
kept = []

async def first_run():
    await client.connect()
    kept.append(await client.memory.start_session("coding", external_id="other"))
    await client.memory.start_session("coding", external_id="u1")
    gc.collect()

asyncio.run(first_run())
ending = harness.admin.holds["EndImpersonation"] = Hold()

async def second_run():
    await kept[0].search("the next call starts ending u1's dropped key")
    await asyncio.to_thread(ending.arrived.wait, 5)

asyncio.run(second_run())
ending.released.set()
time.sleep(0.3)
del harness.admin.holds["EndImpersonation"]
harness.admin.key_cap = 1

async def third_run():
    await bounded(client.memory.start_session("coding", external_id="u1"))
    print("opened")

asyncio.run(third_run())
"""


MINT_CUT_SHORT_BY_ASYNCIO_RUN = """
from memcoai._logging import set_level

set_level("warning")
harness.admin.key_lifetime = 600
kept = []

async def first_run():
    await client.connect()
    kept.append(await client.memory.start_session("coding", external_id="u1"))

asyncio.run(first_run())
clock.advance(0.85 * 600)
minting = harness.admin.holds["ImpersonateExternalUser"] = Hold()

async def second_run():
    await kept[0].search("served by the key held, as the next is minted")
    await asyncio.to_thread(minting.arrived.wait, 5)

asyncio.run(second_run())
minting.released.set()
print("done")
"""


def run_loop_script(tmp_path: Any, body: str) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "script.py"
    source.write_text(LOOP_PRELUDE + body)
    return subprocess.run(  # noqa: S603 - the interpreter and script are ours
        [sys.executable, str(source)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # noqa: PTH120, PTH100
    )


def test_a_renewal_left_on_a_closed_loop_is_started_afresh_on_the_next(tmp_path):
    finished = run_loop_script(tmp_path, RENEWAL_LEFT_ON_A_CLOSED_LOOP)
    assert finished.returncode == 0, finished.stderr
    assert "issued 3" in finished.stdout
    assert "closed" in finished.stdout


def test_an_end_left_on_a_closed_loop_holds_up_no_later_open_for_its_user(tmp_path):
    finished = run_loop_script(tmp_path, END_LEFT_ON_A_CLOSED_LOOP)
    assert finished.returncode == 0, finished.stderr
    assert "opened" in finished.stdout
    assert "closed" in finished.stdout


def test_a_call_cut_short_by_asyncio_run_leaves_the_next_run_working(tmp_path):
    finished = run_loop_script(tmp_path, CALL_CUT_SHORT_BY_ASYNCIO_RUN)
    assert finished.returncode == 0, finished.stderr
    assert "opened" in finished.stdout
    assert "closed" in finished.stdout


def test_a_mint_cut_short_by_asyncio_run_is_reported(tmp_path):
    # The service may have issued the key, but its id never arrived, so
    # nothing can end it: all that can be done is to say so.
    finished = run_loop_script(tmp_path, MINT_CUT_SHORT_BY_ASYNCIO_RUN)
    assert finished.returncode == 0, finished.stderr
    assert "done" in finished.stdout
    assert "may be left live" in finished.stderr
    assert "impersonation-" not in finished.stderr


def test_an_end_cut_short_by_asyncio_run_is_retried_before_the_user_s_next_key(tmp_path):
    finished = run_loop_script(tmp_path, END_CUT_SHORT_BY_ASYNCIO_RUN)
    assert finished.returncode == 0, finished.stderr
    assert "opened" in finished.stdout


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


def test_a_client_closed_as_a_session_ends_its_key_ends_it_once(
    credentialed: Memco, harness: Harness, monkeypatch: pytest.MonkeyPatch
):
    # The session's thread is preempted between its end returning and the key
    # being deregistered. A close arriving then must still wait for it, or it
    # finds the key registered and ends it a second time.
    session = credentialed.memory.start_session("coding", external_id=XID)
    call = credentialed._call
    ended = threading.Event()

    def preempted_after_an_end(
        method: Any, request: Any, timeout: float | None, credential: Any = None
    ) -> Any:
        response = call(method, request, timeout, credential)
        if isinstance(request, admin_pb.EndImpersonationRequest):
            ended.set()
            time.sleep(0.2)
        return response

    monkeypatch.setattr(credentialed, "_call", preempted_after_an_end)
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        closing = pool.submit(session.close)
        assert ended.wait(5)
        credentialed.close()
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


def test_closing_waits_for_a_token_its_sweep_set_renewing_before_closing_the_channel(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # The sweep carries the token held, still honoured, but sets its renewal
    # off; closing the channel under that exchange would take the process
    # down, so the close waits for it.
    harness.admin.key_lifetime = 10 * 3600
    _left_open = credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(0.85 * TOKEN_EXPIRES_AT)
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    try:
        with futures.ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(credentialed.close)
            assert issuing.arrived.wait(5)
            soon(lambda: ends(harness) == [(XID, "key-1")])
            _, waiting = futures.wait([closing], timeout=0.2)
            assert closing in waiting
            issuing.released.set()
            closing.result(timeout=5)
    finally:
        issuing.released.set()
    assert bearer(harness.admin.metadata[-1]) == CLIENT_TOKEN


async def test_async_closing_with_a_token_due_reports_nothing(
    clock: FakeClock,
    async_credentialed: AsyncMemco,
    harness: Harness,
    caplog: pytest.LogCaptureFixture,
):
    # The renewal the sweep sets off is cut short by the channel closing,
    # which is no failure worth a warning.
    harness.admin.key_lifetime = 10 * 3600
    _left_open = await async_credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(0.85 * TOKEN_EXPIRES_AT)
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    try:
        with caplog.at_level(logging.WARNING):
            await async_credentialed.close()
            assert await asyncio.to_thread(issuing.arrived.wait, 5)
            await asyncio.sleep(0.1)
    finally:
        issuing.released.set()
    assert ends(harness) == [(XID, "key-1")]
    assert caplog.records == []


def test_the_close_sweep_stops_at_the_first_failure_with_one_warning(
    credentialed: Memco, harness: Harness, caplog: pytest.LogCaptureFixture
):
    _left_open = [
        credentialed.memory.start_session("coding", external_id=user) for user in ("u1", "u2", "u3")
    ]
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
    _left_open = [
        await async_credentialed.memory.start_session("coding", external_id=user)
        for user in ("u1", "u2", "u3")
    ]
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


async def test_async_a_session_close_cancelled_before_its_end_is_sent_still_ends_the_key(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    # The end waits on the client's own token, which has expired and is
    # being renewed, so the cancel lands before EndImpersonation is sent.
    harness.admin.key_lifetime = 10 * 3600
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(TOKEN_EXPIRES_AT)
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    closing = asyncio.create_task(session.close())
    assert await asyncio.to_thread(issuing.arrived.wait, 5)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    issuing.released.set()
    await eventually(lambda: bool(ends(harness)))
    assert ends(harness) == [(XID, "key-1")]
    assert async_credentialed._live == {}


async def test_async_an_open_cancelled_twice_still_ends_its_key(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    # anyio cancels again at every await inside a cancelled scope, so the
    # cleanup of an open that timed out is itself cancelled as it ends the key.
    harness.admin.key_lifetime = 10 * 3600
    starting = harness.memory.holds["StartSession"] = Hold()
    opening = asyncio.create_task(
        async_credentialed.memory.start_session("coding", external_id=XID)
    )
    assert await asyncio.to_thread(starting.arrived.wait, 5)
    clock.advance(TOKEN_EXPIRES_AT)
    issuing = harness.tokens.holds["IssueToken"] = Hold()
    opening.cancel()
    assert await asyncio.to_thread(issuing.arrived.wait, 5)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    issuing.released.set()
    starting.released.set()
    await eventually(lambda: bool(ends(harness)))
    assert ends(harness) == [(XID, "key-1")]
    assert async_credentialed._live == {}


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


# --- sessions dropped without closing ------------------------------------


def test_a_dropped_session_s_key_is_ended_at_the_client_s_next_call(
    credentialed: Memco, harness: Harness
):
    session = credentialed.memory.start_session("coding", external_id=XID)
    with pytest.warns(ResourceWarning, match="holding key key-1 was garbage-collected") as caught:
        del session
    # Named by its id, which revokes nothing, and never by its value.
    assert [
        str(warning.message) for warning in caught if "impersonation-" in str(warning.message)
    ] == []
    # Never from the collector itself, which may run on any thread, holding
    # any lock.
    assert ends(harness) == []
    # Started by it, on a thread of its own: the call is unrelated to the key.
    credentialed.networks.list()
    soon(lambda: ends(harness) == [(XID, "key-1")])
    soon(lambda: credentialed._live == {})
    assert [
        bearer(metadata)
        for name, _, metadata in recorded(harness.admin)
        if name == "EndImpersonation"
    ] == [CLIENT_TOKEN]


async def test_async_a_dropped_session_s_key_is_ended_at_the_client_s_next_call(
    async_credentialed: AsyncMemco, harness: Harness
):
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    with pytest.warns(ResourceWarning, match="without being closed"):
        del session
    assert ends(harness) == []
    # Started by it, not waited for: the call is unrelated to the key.
    await async_credentialed.networks.list()
    await eventually(lambda: ends(harness) == [(XID, "key-1")])
    await eventually(lambda: async_credentialed._live == {})


def test_a_closed_session_leaves_nothing_to_its_finalizer(credentialed: Memco, harness: Harness):
    with credentialed.memory.with_session("coding", external_id=XID) as session:
        session.search("how does X work")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del session
        gc.collect()
        credentialed.networks.list()
    assert [warning for warning in caught if "without being closed" in str(warning.message)] == []
    assert ends(harness) == [(XID, "key-1")]


def test_a_session_dropped_after_its_client_closed_needs_nothing_more(
    credentialed: Memco, harness: Harness
):
    # The client's close ended the key, and there is no next call to end it.
    session = credentialed.memory.start_session("coding", external_id=XID)
    credentialed.close()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del session
        gc.collect()
    assert [warning for warning in caught if "without being closed" in str(warning.message)] == []
    assert not credentialed._orphans
    assert ends(harness) == [(XID, "key-1")]


def test_ending_dropped_sessions_keys_stops_at_the_first_the_service_refuses(
    credentialed: Memco, harness: Harness
):
    # A service that cannot end one key now will refuse the next too; the
    # rest are left for the next key minted for their user.
    sessions = [
        credentialed.memory.start_session("coding", external_id=user) for user in ("u1", "u2")
    ]
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ] * 2
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del sessions
        gc.collect()
    credentialed.networks.list()
    soon(lambda: [key.failed for key in credentialed._live.values()] == [True, True])
    assert len(ends(harness)) == 1
    assert credentialed._ending == {}


def test_ends_of_dropped_sessions_cut_short_strand_no_claim(
    credentialed: Memco, harness: Harness, monkeypatch: pytest.MonkeyPatch
):
    # Whatever stops them part-way, a key minted for their user must not wait
    # forever on the ends they had claimed.
    sessions = [credentialed.memory.start_session("coding", external_id=XID) for _ in range(2)]
    send = credentialed._send

    def cut_short(method: Any, request: Any, deadline: float, bearer: Any) -> Any:
        if method is credentialed._admin.EndImpersonation:
            raise KeyboardInterrupt
        return send(method, request, deadline, bearer)

    monkeypatch.setattr(credentialed, "_send", cut_short)
    monkeypatch.setattr(threading, "excepthook", lambda _: None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del sessions
        gc.collect()
    credentialed.networks.list()
    soon(lambda: [key.failed for key in credentialed._live.values()] == [True, True])
    assert credentialed._ending == {}
    monkeypatch.setattr(credentialed, "_send", send)
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        _reopened = pool.submit(
            credentialed.memory.start_session, "coding", external_id=XID
        ).result(timeout=5)
    assert ends(harness) == [(XID, "key-1"), (XID, "key-2")]


def test_ends_whose_thread_cannot_start_strand_no_claim(
    credentialed: Memco, harness: Harness, monkeypatch: pytest.MonkeyPatch
):
    # An interrupt landing as the thread is started, or a thread that cannot
    # be: the keys claimed for it are released, and marked to be tried again.
    _dropped = credentialed.memory.start_session("coding", external_id=XID)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del _dropped
        gc.collect()
    apart = _auth.apart

    def refused(work: Any, *args: Any) -> None:
        if getattr(work, "__name__", "") == "_end_each":
            raise KeyboardInterrupt
        apart(work, *args)

    monkeypatch.setattr(_auth, "apart", refused)
    with pytest.raises(KeyboardInterrupt):
        credentialed.networks.list()
    assert credentialed._ending == {}
    assert [key.failed for key in credentialed._live.values()] == [True]
    monkeypatch.setattr(_auth, "apart", apart)
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        _reopened = pool.submit(
            credentialed.memory.start_session, "coding", external_id=XID
        ).result(timeout=5)
    assert ends(harness) == [(XID, "key-1")]


class Interrupted(Exception):  # noqa: N818 - named for what the signal did
    """What the test's signal handler raises, as a time limit's handler would."""


def raise_interrupted(signum: int, frame: Any) -> None:
    raise Interrupted


def test_an_interrupt_during_a_call_leaves_no_bearer_in_any_frame(
    credentialed: Memco, harness: Harness
):
    # A signal handler raising while grpc blocks -- a task's soft time limit,
    # a worker's timeout -- passes through grpc's frames, whose locals hold
    # the metadata the call carries; an error tracker would ship them.
    session = credentialed.memory.start_session("coding", external_id=XID)
    searching = harness.memory.holds["Search"] = Hold()
    previous = signal.signal(signal.SIGALRM, raise_interrupted)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    try:
        with pytest.raises(Interrupted) as caught:
            session.search("held")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        searching.released.set()
    held = [
        (frame.f_code.co_filename, frame.f_code.co_name, name)
        for frame, _ in traceback.walk_tb(caught.value.__traceback__)
        if frame.f_code.co_filename != __file__
        for name, value in frame.f_locals.items()
        if "impersonation-" in repr(value) or "client-token-" in repr(value)
    ]
    assert held == []


def test_a_close_interrupted_part_way_is_finished_by_the_next(
    credentialed: Memco, harness: Harness
):
    # Interrupted while it waits for a call in flight, the close has ended
    # nothing yet; closing again finishes it rather than returning at once.
    session = credentialed.memory.start_session("coding", external_id=XID)
    searching = harness.memory.holds["Search"] = Hold()
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(session.search, "held")
        assert searching.arrived.wait(5)
        previous = signal.signal(signal.SIGALRM, raise_interrupted)
        signal.setitimer(signal.ITIMER_REAL, 0.2)
        try:
            with pytest.raises(Interrupted):
                credentialed.close()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            searching.released.set()
        held.result(timeout=5)
    assert ends(harness) == []
    credentialed.close()
    assert ends(harness) == [(XID, "key-1")]
    assert credentialed._shut


@pytest.fixture
def skewed(harness: Harness) -> None:
    """Put this host's clock two hours ahead of a service that sends no ``expires_in``."""
    harness.admin.clock = lambda: time.time() - 7200
    harness.admin.sends_expires_in = False


def test_a_key_this_host_reads_as_already_expired_is_refused_naming_the_clock(
    skewed: None, credentialed: Memco, harness: Harness
):
    # Without the service's own count, expires_at is read against this host's
    # clock. Not "this session is closed": nothing was closed, and what is
    # wrong is this host's clock. The key it cannot use is ended.
    with pytest.raises(errors.MemcoConfigError, match="clock") as caught:
        credentialed.memory.start_session("coding", external_id=XID)
    assert "closed" not in str(caught.value)
    assert "impersonation-" not in str(caught.value)
    soon(lambda: ends(harness) == [(XID, "key-1")])


async def test_async_a_key_this_host_reads_as_already_expired_is_refused_naming_the_clock(
    skewed: None, async_credentialed: AsyncMemco, harness: Harness
):
    with pytest.raises(errors.MemcoConfigError, match="clock") as caught:
        await async_credentialed.memory.start_session("coding", external_id=XID)
    assert "closed" not in str(caught.value)
    await eventually(lambda: ends(harness) == [(XID, "key-1")])


def test_a_key_timed_by_the_service_s_count_ignores_this_host_s_clock(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # expires_in is the seconds left by the service's own count, timed here
    # from before the mint was asked for, as the client token is: no wall
    # clock comes into it, so a host two hours ahead works as any other.
    harness.admin.clock = lambda: clock.time() - 7200
    harness.admin.key_lifetime = KEY_LIFETIME
    session = credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(0.8 * KEY_LIFETIME - 1)
    session.search("before")
    assert harness.admin.calls == ["ImpersonateExternalUser"]
    clock.advance(2)
    session.search("after")
    soon(lambda: ends(harness) == [(XID, "key-1")])
    session.search("later")
    assert carried(harness, "Search") == [KEY_1, KEY_1, KEY_2]


async def test_async_a_key_timed_by_the_service_s_count_ignores_this_host_s_clock(
    clock: FakeClock, async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.clock = lambda: clock.time() - 7200
    harness.admin.key_lifetime = KEY_LIFETIME
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    clock.advance(0.8 * KEY_LIFETIME - 1)
    await session.search("before")
    assert harness.admin.calls == ["ImpersonateExternalUser"]
    clock.advance(2)
    await session.search("after")
    await eventually(lambda: ends(harness) == [(XID, "key-1")])
    await session.search("later")
    assert carried(harness, "Search") == [KEY_1, KEY_1, KEY_2]


def test_the_registry_times_a_key_by_the_service_s_count(
    clock: FakeClock, credentialed: Memco, harness: Harness
):
    # Neither forgotten at the next mint nor skipped by the close sweep as
    # expired, however far ahead this host's clock runs.
    harness.admin.clock = lambda: clock.time() - 7200
    _left_open = credentialed.memory.start_session("coding", external_id="u1")
    _other = credentialed.memory.start_session("coding", external_id="u2")
    assert sorted(credentialed._live) == ["key-1", "key-2"]
    credentialed.close()
    assert sorted(ends(harness)) == [("u1", "key-1"), ("u2", "key-2")]


async def test_async_dropped_sessions_keys_are_ended_together_without_holding_up_the_call(
    async_credentialed: AsyncMemco, harness: Harness
):
    sessions = [
        await async_credentialed.memory.start_session("coding", external_id=user)
        for user in ("u1", "u2")
    ]
    # Asynchronously they are all sent at once, so a service that cannot end
    # them costs the calls nothing; each refused one is left for the next key
    # minted for its user.
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    harness.admin.transient_errors["EndImpersonation"] = [
        (grpc.StatusCode.UNAVAILABLE, "admin service down")
    ] * 2
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del sessions
        gc.collect()
    try:
        await asyncio.wait_for(async_credentialed.networks.list(), 1)
    finally:
        ending.released.set()
    await eventually(lambda: len(ends(harness)) == 2)
    await eventually(
        lambda: [key.failed for key in async_credentialed._live.values()] == [True, True]
    )


def test_a_mint_waits_for_the_end_of_a_dropped_session_s_key_in_flight(
    credentialed: Memco, harness: Harness
):
    # Another call is ending the dropped key when a session opens for the same
    # user. Until that end lands, the service counts the dropped key against
    # the user's cap, so asking for a new one first is refused.
    harness.admin.key_cap = 1
    session = credentialed.memory.start_session("coding", external_id=XID)
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del session
        gc.collect()
    try:
        with futures.ThreadPoolExecutor(max_workers=2) as pool:
            reaping = pool.submit(credentialed.networks.list)
            assert ending.arrived.wait(5)
            opening = pool.submit(credentialed.memory.start_session, "coding", external_id=XID)
            time.sleep(0.2)
            ending.released.set()
            reaping.result(timeout=5)
            _reopened = opening.result(timeout=5)
    finally:
        ending.released.set()
    assert harness.admin.calls.count("ImpersonateExternalUser") == 2


async def test_async_a_mint_waits_for_the_end_of_a_dropped_session_s_key_in_flight(
    async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_cap = 1
    session = await async_credentialed.memory.start_session("coding", external_id=XID)
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del session
        gc.collect()
    try:
        reaping = asyncio.create_task(async_credentialed.networks.list())
        assert await asyncio.to_thread(ending.arrived.wait, 5)
        opening = asyncio.create_task(
            async_credentialed.memory.start_session("coding", external_id=XID)
        )
        await asyncio.sleep(0.2)
        ending.released.set()
        await asyncio.wait_for(reaping, 5)
        _reopened = await asyncio.wait_for(opening, 5)
    finally:
        ending.released.set()
    assert harness.admin.calls.count("ImpersonateExternalUser") == 2


async def test_async_a_mint_waits_for_a_closing_session_s_end_for_the_same_user(
    async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_cap = 1
    first = await async_credentialed.memory.start_session("coding", external_id=XID)
    ending = harness.admin.holds["EndImpersonation"] = Hold()
    try:
        closing = asyncio.create_task(first.close())
        assert await asyncio.to_thread(ending.arrived.wait, 5)
        opening = asyncio.create_task(
            async_credentialed.memory.start_session("coding", external_id=XID)
        )
        await asyncio.sleep(0.2)
        ending.released.set()
        await asyncio.wait_for(closing, 5)
        _second = await asyncio.wait_for(opening, 5)
    finally:
        ending.released.set()
    assert harness.admin.calls.count("ImpersonateExternalUser") == 2


def open_and_drop(client: Memco) -> None:
    """Open a session for the user, use it, and drop it without closing it."""
    client.memory.start_session("coding", external_id=XID).search("how does X work")
    gc.collect()


def test_dropped_sessions_never_exhaust_the_user_s_live_key_cap(
    credentialed: Memco, harness: Harness
):
    # A long-lived client rarely closes, so its close ending the keys is no
    # help: the 21st session would be refused, well-behaved ones included.
    harness.admin.key_cap = 20
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        for _ in range(25):
            open_and_drop(credentialed)
    with credentialed.memory.with_session("coding", external_id=XID) as session:
        session.search("how does X work")


async def test_async_dropped_sessions_never_exhaust_the_user_s_live_key_cap(
    async_credentialed: AsyncMemco, harness: Harness
):
    harness.admin.key_cap = 20
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        for _ in range(25):
            session = await async_credentialed.memory.start_session("coding", external_id=XID)
            await session.search("how does X work")
            del session
            gc.collect()
    async with async_credentialed.memory.with_session("coding", external_id=XID) as session:
        await session.search("how does X work")


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
