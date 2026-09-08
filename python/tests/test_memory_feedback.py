"""``Memory.feedback()``: rate one search result directly, no ``FeedbackRating`` needed.

A memory from a session-bound search and one from a plain domain search both
work identically, because the search response carries a session id either
way. A memory fetched by ``client.memory.get_memory`` directly carries no
session id at all, so its ``feedback()`` fails through the SDK's own
``session_id`` validation rather than a bespoke error -- the same mechanism a
caller would hit calling ``share_feedback`` with a blank id by hand. Fetched
through a session instead (``session.get_memory``), it is rebound to that
session and rates normally.
"""

from __future__ import annotations

import pytest

from memco import AsyncMemco, Memco
from memco.errors import MemcoConfigError, MemcoInternalError, MemcoInvalidRequestError
from memco.memory.v1 import memory_pb2 as pb
from memco.types import AsyncMemory, Memory

from .conftest import TOKEN
from .fake_server import Harness


def _search_response(idx: str = "memory-a-1") -> pb.SearchResponse:
    return pb.SearchResponse(session_id="session-a", memories=[pb.MemoryResult(idx=idx)])


def _feedback_response(idx: str = "memory-a-1") -> pb.ShareFeedbackResponse:
    return pb.ShareFeedbackResponse(
        session_id="session-a",
        entries=[pb.FeedbackEntry(idx=idx, relevant=True, correct=True)],
    )


def test_feedback_from_a_session_bound_search_sends_the_right_ids(client: Memco, harness: Harness):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    session = client.memory.start_session("coding")
    memory = session.search("how does X work").memories[0]

    entry = memory.feedback(relevant=True, correct=True)

    assert harness.memory.requests["ShareFeedback"].session_id == "session-a"
    assert [r.idx for r in harness.memory.requests["ShareFeedback"].feedback] == ["memory-a-1"]
    assert entry.idx == "memory-a-1"


def test_feedback_from_a_plain_domain_search_also_works(client: Memco, harness: Harness):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    memory = client.memory.search("how does X work", domain="coding").memories[0]

    memory.feedback(relevant=True, correct=False)

    assert harness.memory.requests["ShareFeedback"].session_id == "session-a"


def test_feedback_forwards_a_comment(client: Memco, harness: Harness):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    memory = client.memory.search("q", domain="coding").memories[0]

    memory.feedback(relevant=True, correct=True, comment="answered directly")

    assert harness.memory.requests["ShareFeedback"].feedback[0].comment == "answered directly"


def test_feedback_from_get_memory_is_refused_locally(client: Memco, harness: Harness):
    memory = client.memory.get_memory("memory-a-1")
    harness.memory.calls.clear()

    with pytest.raises(MemcoInvalidRequestError, match="session_id"):
        memory.feedback(relevant=True, correct=True)
    assert harness.memory.calls == []


def test_feedback_can_be_called_more_than_once(client: Memco, harness: Harness):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    memory = client.memory.search("q", domain="coding").memories[0]

    memory.feedback(relevant=True, correct=True)
    memory.feedback(relevant=False, correct=False)

    assert harness.memory.calls == ["Search", "ShareFeedback", "ShareFeedback"]


def test_feedback_after_the_client_closes_raises_the_usual_config_error(harness: Harness):
    connected = Memco(token=TOKEN, host=harness.address, tls=False)
    harness.memory.responses["Search"] = _search_response()
    memory = connected.memory.search("q", domain="coding").memories[0]
    connected.close()

    with pytest.raises(MemcoConfigError, match="closed"):
        memory.feedback(relevant=True, correct=True)


def test_a_hand_built_memory_has_no_operations_to_delegate_to():
    memory = Memory(idx="m", kind="", times_served=0, intents=(), insights=(), reference=None)
    with pytest.raises(MemcoInvalidRequestError, match="no session"):
        memory.feedback(relevant=True, correct=True)


def test_feedback_through_a_session_scoped_get_memory_succeeds(client: Memco, harness: Harness):
    harness.memory.responses["GetMemory"] = pb.GetMemoryResponse(
        memory=pb.MemoryResult(idx="memory-a-1")
    )
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    session = client.memory.start_session("coding")

    memory = session.get_memory("memory-a-1")
    memory.feedback(relevant=True, correct=True)

    assert harness.memory.requests["ShareFeedback"].session_id == session.id


def test_feedback_from_a_response_with_no_entries_raises_a_typed_error(
    client: Memco, harness: Harness
):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = pb.ShareFeedbackResponse(session_id="session-a")
    memory = client.memory.search("q", domain="coding").memories[0]

    with pytest.raises(MemcoInternalError, match="no entries"):
        memory.feedback(relevant=True, correct=True)


def test_the_binding_does_not_affect_equality_hash_or_repr(client: Memco, harness: Harness):
    harness.memory.responses["Search"] = _search_response()
    bound = client.memory.search("q", domain="coding").memories[0]
    unbound = Memory(
        idx="memory-a-1", kind="", times_served=0, intents=(), insights=(), reference=None
    )
    assert bound == unbound
    assert hash(bound) == hash(unbound)
    assert "MemoryOperations" not in repr(bound)


# --- async mirror ----------------------------------------------------------


async def test_async_feedback_from_a_session_bound_search(
    async_client: AsyncMemco, harness: Harness
):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    session = await async_client.memory.start_session("coding")
    result = await session.search("how does X work")
    memory = result.memories[0]

    entry = await memory.feedback(relevant=True, correct=True)

    assert harness.memory.requests["ShareFeedback"].session_id == "session-a"
    assert entry.idx == "memory-a-1"


async def test_async_feedback_from_a_plain_domain_search_also_works(
    async_client: AsyncMemco, harness: Harness
):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    result = await async_client.memory.search("q", domain="coding")
    memory = result.memories[0]

    await memory.feedback(relevant=True, correct=False)

    assert harness.memory.requests["ShareFeedback"].session_id == "session-a"


async def test_async_feedback_from_get_memory_is_refused_locally(
    async_client: AsyncMemco, harness: Harness
):
    memory = await async_client.memory.get_memory("memory-a-1")
    harness.memory.calls.clear()

    with pytest.raises(MemcoInvalidRequestError, match="session_id"):
        await memory.feedback(relevant=True, correct=True)
    assert harness.memory.calls == []


async def test_async_feedback_can_be_awaited_more_than_once(
    async_client: AsyncMemco, harness: Harness
):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    result = await async_client.memory.search("q", domain="coding")
    memory = result.memories[0]

    await memory.feedback(relevant=True, correct=True)
    await memory.feedback(relevant=False, correct=False)

    assert harness.memory.calls == ["Search", "ShareFeedback", "ShareFeedback"]


async def test_async_feedback_after_the_client_closes_raises_the_usual_config_error(
    harness: Harness,
):
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    harness.memory.responses["Search"] = _search_response()
    result = await connected.memory.search("q", domain="coding")
    memory = result.memories[0]
    await connected.close()

    with pytest.raises(MemcoConfigError, match="closed"):
        await memory.feedback(relevant=True, correct=True)


async def test_async_hand_built_memory_has_no_operations_to_delegate_to():
    memory = AsyncMemory(idx="m", kind="", times_served=0, intents=(), insights=(), reference=None)
    with pytest.raises(MemcoInvalidRequestError, match="no session"):
        await memory.feedback(relevant=True, correct=True)


async def test_async_feedback_through_a_session_scoped_get_memory_succeeds(
    async_client: AsyncMemco, harness: Harness
):
    harness.memory.responses["GetMemory"] = pb.GetMemoryResponse(
        memory=pb.MemoryResult(idx="memory-a-1")
    )
    harness.memory.responses["ShareFeedback"] = _feedback_response()
    session = await async_client.memory.start_session("coding")

    memory = await session.get_memory("memory-a-1")
    await memory.feedback(relevant=True, correct=True)

    assert harness.memory.requests["ShareFeedback"].session_id == session.id


async def test_async_feedback_from_a_response_with_no_entries_raises_a_typed_error(
    async_client: AsyncMemco, harness: Harness
):
    harness.memory.responses["Search"] = _search_response()
    harness.memory.responses["ShareFeedback"] = pb.ShareFeedbackResponse(session_id="session-a")
    result = await async_client.memory.search("q", domain="coding")
    memory = result.memories[0]

    with pytest.raises(MemcoInternalError, match="no entries"):
        await memory.feedback(relevant=True, correct=True)
