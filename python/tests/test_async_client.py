"""The asyncio client, against the same real in-process server."""

from __future__ import annotations

import grpc
import pytest
from grpc_health.v1 import health_pb2

from memco import AsyncMemco, errors, types
from memco.memory.v1 import memory_pb2 as pb

from .conftest import TOKEN
from .fake_server import Harness

# --- auth ----------------------------------------------------------------


async def test_auth_metadata_is_bearer_with_a_capital_b(async_client: AsyncMemco, harness: Harness):
    await async_client.memory.list_domains()
    assert harness.memory.metadata[-1]["authorization"] == f"Bearer {TOKEN}"


async def test_auth_metadata_is_sent_on_every_method(async_client: AsyncMemco, harness: Harness):
    await async_client.memory.list_domains()
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


async def test_check_health_false_skips_the_probe(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False, check_health=False):
        pass
    assert harness.health.checked_services == []


async def test_credentials_are_not_verified_by_default(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.memory.calls == []


async def test_verify_credentials_fires_list_domains(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False, verify_credentials=True):
        pass
    assert harness.memory.calls == ["ListDomains"]


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
        await async_client.memory.list_domains()
    assert caught.value.code is code


async def test_validation_fires_before_any_rpc(async_client: AsyncMemco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        await async_client.memory.search("q" * 1001, domain="coding")
    assert harness.memory.calls == []


# --- the eight operations ------------------------------------------------


async def test_all_eight_operations_round_trip(async_client: AsyncMemco, harness: Harness):
    harness.memory.responses["ListDomains"] = pb.ListDomainsResponse(
        domains=[pb.DomainEntry(slug="coding")]
    )
    assert [d.slug for d in (await async_client.memory.list_domains()).domains] == ["coding"]

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


# --- lifecycle -----------------------------------------------------------


async def test_context_manager_closes_the_channel(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False) as connected:
        await connected.memory.list_domains()
    with pytest.raises(errors.ClientConfigError, match="closed"):
        await connected.memory.list_domains()


async def test_double_close_is_safe(harness: Harness):
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    await connected.close()
    await connected.close()


async def test_provenance_is_reachable(async_client: AsyncMemco):
    assert async_client.provenance().server_commit
