"""The asyncio client, against the same real in-process server."""

from __future__ import annotations

import logging

import grpc
import pytest
from grpc_health.v1 import health_pb2

import memco
from memco import AsyncMemco, errors, types
from memco.memory.v1 import memory_pb2 as pb

from .conftest import TOKEN
from .fake_server import Harness

# --- auth ----------------------------------------------------------------


async def test_auth_metadata_is_bearer_with_a_capital_b(async_client: AsyncMemco, harness: Harness):
    await async_client.memory.describe_domains()
    assert harness.memory.metadata[-1]["authorization"] == f"Bearer {TOKEN}"


async def test_auth_metadata_is_sent_on_every_method(async_client: AsyncMemco, harness: Harness):
    await async_client.memory.describe_domains()
    await async_client.memory.start_session("coding")
    assert len(harness.memory.metadata) == 2
    for sent in harness.memory.metadata:
        assert sent["authorization"] == f"Bearer {TOKEN}"


async def test_the_credential_is_withheld_from_the_health_probe(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.health.metadata, "the health probe did not run"
    assert "authorization" not in harness.health.metadata[-1]


# --- health gate ---------------------------------------------------------


async def test_connect_checks_health_with_the_empty_service_name(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.health.checked_services == [""]


async def test_not_serving_raises_unhealthy(harness: Harness):
    harness.health.status = health_pb2.HealthCheckResponse.NOT_SERVING
    with pytest.raises(errors.MemcoUnhealthyError):
        async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
            pass


async def test_unreachable_server_raises_unavailable():
    with pytest.raises(errors.MemcoUnavailableError):
        async with AsyncMemco(token=TOKEN, host="localhost:1", tls=False, timeout=2.0):
            pass


# --- limits are fetched on connect ---------------------------------------


async def test_connect_fetches_the_limits(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.memory.calls == ["DescribeDomains"]


async def test_the_fetched_limits_are_applied_to_later_calls(harness: Harness):
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
        limits=pb.Limits(max_query_characters=10)
    )
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False) as built:
        harness.memory.calls.clear()
        with pytest.raises(errors.MemcoInvalidRequestError, match="query"):
            await built.memory.search("x" * 11, domain="coding")
    assert harness.memory.calls == []


async def test_a_bad_token_surfaces_on_connect(harness: Harness):
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid or insufficient credentials")
    with pytest.raises(errors.MemcoAuthenticationError):
        async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
            pass


async def test_a_rejected_credential_is_logged(harness: Harness, caplog):
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid or insufficient credentials")
    with (
        caplog.at_level(logging.ERROR, logger="memco"),
        pytest.raises(errors.MemcoAuthenticationError),
    ):
        async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
            pass
    assert [record.name for record in caplog.records] == ["memco"]
    assert TOKEN not in caplog.text


# --- error translation ---------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, errors.MemcoAuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, errors.MemcoPermissionError),
        (grpc.StatusCode.RESOURCE_EXHAUSTED, errors.MemcoResourceExhaustedError),
        (grpc.StatusCode.INTERNAL, errors.MemcoInternalError),
    ],
)
async def test_server_errors_arrive_typed(
    async_client: AsyncMemco, harness: Harness, code, expected
):
    harness.memory.error = (code, "boom")
    with pytest.raises(expected) as caught:
        await async_client.memory.describe_domains()
    assert caught.value.code is code


async def test_validation_fires_before_any_rpc(async_client: AsyncMemco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        await async_client.memory.search("   ", domain="coding")
    assert harness.memory.calls == []


# --- the operations ------------------------------------------------------


async def test_every_operation_round_trips(async_client: AsyncMemco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
        domains=[pb.DomainEntry(slug="coding")]
    )
    assert [d.slug for d in (await async_client.memory.describe_domains()).domains] == ["coding"]

    session = await async_client.memory.start_session("coding")
    assert session.session_id == "session-a"

    assert (
        await async_client.memory.search("q", session_id=session.session_id)
    ).session_id == "session-a"
    assert (await async_client.memory.get_memory("memory-a-1")).idx == "memory-a-1"

    created = await async_client.memory.create_memory(
        query="q", title="t", content="c", domain="coding"
    )
    assert created.operation_id == "create-a"

    enriched = await async_client.memory.enrich_memory(
        memory_idx="new", session_id=session.session_id, title="t", content="c"
    )
    assert enriched.operation_id == "enrich-a"

    rated = await async_client.memory.share_feedback(
        session_id=session.session_id,
        feedback=[types.FeedbackRating(idx="memory-a-1", relevant=True, correct=True)],
    )
    assert rated.session_id == "session-a"

    reverted = await async_client.memory.revert_memory("create-a")
    assert reverted.outcome is types.RevertOutcome.MERGED

    imported = await async_client.memory.import_memories(
        [
            types.ImportedMemory(
                queries=["how does X work"],
                insights=[types.ImportedInsight(title="t", content="c")],
            )
        ],
        session_id=session.session_id,
    )
    assert [o.status for o in imported.results] == [types.ImportStatus.QUEUED]


# --- lifecycle -----------------------------------------------------------


async def test_context_manager_closes_the_channel(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False) as connected:
        await connected.memory.describe_domains()
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await connected.memory.describe_domains()


async def test_double_close_is_safe(harness: Harness):
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    await connected.close()
    await connected.close()


async def test_provenance_is_reachable(async_client: AsyncMemco):
    assert async_client.provenance().server_commit


async def test_the_user_agent_is_sent_by_the_async_client(
    async_client: AsyncMemco, harness: Harness
):
    await async_client.memory.describe_domains()
    sent = harness.memory.metadata[-1]["user-agent"]
    # Prepended, not appended: gRPC's own token must survive or the transport
    # becomes unidentifiable.
    assert sent.startswith(f"memco-python/{memco.__version__}")
    # The asyncio transport identifies itself as grpc-python-asyncio, so match
    # the stem rather than the synchronous spelling.
    assert "grpc-python" in sent
