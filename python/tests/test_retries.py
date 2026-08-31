"""A blip is absorbed only where replaying the call cannot change anything.

gRPC's configurable retries are at-least-once, so a retry sent after the server
committed produces a duplicate. The policy therefore covers the two calls that
mint nothing, and these tests count the attempts the server actually saw rather
than inspecting the policy. Search is excluded despite being the hot path: see
RETRYABLE_METHODS for why.
"""

from __future__ import annotations

import grpc
import pytest

from memco import AsyncMemco, Memco
from memco.errors import MemcoUnavailableError
from memco.types import FeedbackRating, ImportedInsight, ImportedMemory

from .fake_server import Harness

BLIP = (grpc.StatusCode.UNAVAILABLE, "try again")


def test_a_read_survives_one_blip(client: Memco, harness: Harness):
    harness.memory.transient_errors["GetMemory"] = [BLIP]
    assert client.memory.get_memory("memory-a-1").idx == "memory-a-1"
    assert harness.memory.calls == ["GetMemory", "GetMemory"]


def test_searching_is_not_retried(client: Memco, harness: Harness):
    # A search with no session_id opens one, so replaying it orphans a session;
    # replaying a scoped one can come back as bare references. Both are worse
    # than the error the caller gets instead.
    harness.memory.transient_errors["Search"] = [BLIP]
    with pytest.raises(MemcoUnavailableError):
        client.memory.search("how does X work", domain="coding")
    assert harness.memory.calls == ["Search"]


def test_a_write_is_not_retried(client: Memco, harness: Harness):
    harness.memory.transient_errors["CreateMemory"] = [BLIP]
    with pytest.raises(MemcoUnavailableError):
        client.memory.create_memory(query="q", title="t", content="c", domain="coding")
    # The whole point: one attempt, so a committed write cannot be duplicated.
    assert harness.memory.calls == ["CreateMemory"]


def test_an_import_is_not_retried(client: Memco, harness: Harness):
    # The service dedupes by content, so a replay would not write twice — but it
    # would report DUPLICATE for what this call itself queued, which is the one
    # thing an importer reads the outcomes to learn.
    harness.memory.transient_errors["ImportMemories"] = [BLIP]
    with pytest.raises(MemcoUnavailableError):
        client.memory.import_memories(
            [ImportedMemory(queries=["q"], insights=[ImportedInsight(title="t", content="c")])],
            domain="coding",
        )
    assert harness.memory.calls == ["ImportMemories"]


def test_opening_a_session_is_not_retried(client: Memco, harness: Harness):
    # StartSession mints a session id, so it is a write like any other.
    harness.memory.transient_errors["StartSession"] = [BLIP]
    with pytest.raises(MemcoUnavailableError):
        client.memory.start_session("coding")
    assert harness.memory.calls == ["StartSession"]


def test_a_read_gives_up_rather_than_retrying_for_ever(client: Memco, harness: Harness):
    harness.memory.transient_errors["GetMemory"] = [BLIP] * 10
    with pytest.raises(MemcoUnavailableError):
        client.memory.get_memory("memory-a-1")
    # maxAttempts, exactly. A looser bound would also pass with retries off.
    assert len(harness.memory.calls) == 3


async def test_a_read_survives_one_blip_on_the_async_client(
    async_client: AsyncMemco, harness: Harness
):
    harness.memory.transient_errors["GetMemory"] = [BLIP]
    assert (await async_client.memory.get_memory("memory-a-1")).idx == "memory-a-1"
    assert harness.memory.calls == ["GetMemory", "GetMemory"]


async def test_a_write_is_not_retried_on_the_async_client(
    async_client: AsyncMemco, harness: Harness
):
    harness.memory.transient_errors["ShareFeedback"] = [BLIP]
    rating = FeedbackRating(idx="memory-a-1", relevant=True, correct=True)
    with pytest.raises(MemcoUnavailableError):
        await async_client.memory.share_feedback(session_id="session-a", feedback=[rating])
    assert harness.memory.calls == ["ShareFeedback"]


def test_connecting_survives_a_blip_on_the_health_probe(harness: Harness):
    # Construction gates on the probe, so without the health entry in the
    # policy a blip there is the one transient failure the SDK cannot absorb.
    harness.health.transient_errors = [BLIP]
    with Memco(token="t", host=harness.address, tls=False) as connected:
        assert connected.memory.describe_domains() is not None
    assert harness.health.checked_services == ["", ""]
