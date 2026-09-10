"""A session opened once must reach every call made through the scope.

The point of the scope is that the id cannot be dropped: these tests read the
session id off the wire rather than off the scope, because the scope reporting
one it never sent is exactly the failure worth catching.
"""

from __future__ import annotations

import pytest

from memcoai import AsyncMemco, Memco, types
from memcoai.errors import MemcoInvalidRequestError

from .fake_server import Harness


def test_the_scope_carries_the_session_the_service_opened(client: Memco):
    assert client.memory.with_session("coding").id == "session-a"


def test_start_session_already_carries_every_bound_operation(client: Memco, harness: Harness):
    # The merge point: start_session's own result must work exactly like
    # with_session's, with no separate scope required.
    session = client.memory.start_session("coding")
    assert session.id == "session-a"
    session.search("how does X work")
    assert harness.memory.requests["Search"].session_id == "session-a"


def test_the_scope_exposes_the_instructions_the_open_returned(client: Memco):
    assert isinstance(client.memory.with_session("coding").instructions, types.Instructions)


def test_a_scoped_search_sends_the_session_id(client: Memco, harness: Harness):
    client.memory.with_session("coding").search("how does X work")
    assert harness.memory.requests["Search"].session_id == "session-a"
    # The session supplies the domain, so the scope must not send one too.
    assert harness.memory.requests["Search"].domain == ""


def test_a_scoped_create_sends_the_session_id(client: Memco, harness: Harness):
    client.memory.with_session("coding").create_memory(query="q", title="t", content="c")
    assert harness.memory.requests["CreateMemory"].session_id == "session-a"
    assert harness.memory.requests["CreateMemory"].domain == ""


def test_a_scoped_enrich_sends_the_session_id(client: Memco, harness: Harness):
    client.memory.with_session("coding").enrich_memory(memory_idx="new", title="t", content="c")
    assert harness.memory.requests["EnrichMemory"].session_id == "session-a"


def test_a_scoped_rating_sends_the_session_id(client: Memco, harness: Harness):
    client.memory.with_session("coding").share_feedback(
        feedback=[types.FeedbackRating(idx="memory-a-1", relevant=True, correct=True)]
    )
    assert harness.memory.requests["ShareFeedback"].session_id == "session-a"


def test_a_scoped_import_sends_the_session_id(client: Memco, harness: Harness):
    client.memory.with_session("coding").import_memories(
        [
            types.ImportedMemory(
                queries=["how does X work"],
                insights=[types.ImportedInsight(title="t", content="c")],
            )
        ]
    )
    assert harness.memory.requests["ImportMemories"].session_id == "session-a"
    assert harness.memory.requests["ImportMemories"].domain == ""


def test_the_scope_forwards_the_operations_that_carry_no_session(client: Memco):
    scope = client.memory.with_session("coding")
    assert scope.get_memory("memory-a-1").idx == "memory-a-1"
    assert scope.revert_memory("create-a").outcome is types.RevertOutcome.MERGED


def test_the_scope_still_rejects_a_bad_request_locally(client: Memco, harness: Harness):
    scope = client.memory.with_session("coding")
    harness.memory.calls.clear()
    with pytest.raises(MemcoInvalidRequestError):
        scope.search("")
    assert harness.memory.calls == []


def test_the_scope_can_be_used_as_a_context_manager(client: Memco, harness: Harness):
    with client.memory.with_session("coding") as scope:
        scope.search("how does X work")
    assert harness.memory.requests["Search"].session_id == "session-a"


async def test_the_async_scope_is_awaited(async_client: AsyncMemco, harness: Harness):
    scope = await async_client.memory.with_session("coding")
    await scope.search("how does X work")
    assert scope.id == "session-a"
    assert harness.memory.requests["Search"].session_id == "session-a"


async def test_the_async_scope_is_an_async_context_manager(
    async_client: AsyncMemco, harness: Harness
):
    # `async with await ...` would work too; that it is not needed is the point.
    async with async_client.memory.with_session("coding") as scope:
        await scope.create_memory(query="q", title="t", content="c")
    assert harness.memory.requests["CreateMemory"].session_id == "session-a"


async def test_the_async_scope_opens_nothing_until_it_is_awaited(
    async_client: AsyncMemco, harness: Harness
):
    # The opener is lazy, so a scope that is built and dropped costs no session.
    async_client.memory.with_session("coding")
    assert harness.memory.calls == []


async def test_the_async_scope_opens_one_session_however_often_it_is_reached(
    async_client: AsyncMemco, harness: Harness
):
    # Opening a second would be a silent write, and the caller could not reach
    # the first: the handle only ever hands back the session it already opened.
    opener = async_client.memory.with_session("coding")
    first = await opener
    async with opener as second:
        assert second is first
    assert harness.memory.calls == ["StartSession"]


async def test_the_async_scope_sends_the_session_id_on_an_import(
    async_client: AsyncMemco, harness: Harness
):
    async with async_client.memory.with_session("coding") as scope:
        await scope.import_memories(
            [
                types.ImportedMemory(
                    queries=["how does X work"],
                    insights=[types.ImportedInsight(title="t", content="c")],
                )
            ]
        )
    assert harness.memory.requests["ImportMemories"].session_id == "session-a"
    assert harness.memory.requests["ImportMemories"].domain == ""


async def test_an_awaited_async_scope_is_still_a_context_manager(
    async_client: AsyncMemco, harness: Harness
):
    # The synchronous scope is one, so this one is too, reached either way.
    scope = await async_client.memory.with_session("coding")
    async with scope as entered:
        await entered.search("how does X work")
    assert harness.memory.requests["Search"].session_id == "session-a"
