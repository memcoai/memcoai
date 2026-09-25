"""The asyncio client, against the same real in-process server."""

from __future__ import annotations

import asyncio
import logging

import grpc
import pytest
from grpc_health.v1 import health_pb2

import memcoai
from memcoai import AsyncMemco, errors, types
from memcoai._auth import Minted
from memcoai.memory.v1 import memory_pb2 as pb

from .conftest import TOKEN
from .fake_server import Harness, Hold

# --- auth ----------------------------------------------------------------


async def test_auth_metadata_is_bearer_with_a_capital_b(async_client: AsyncMemco, harness: Harness):
    await async_client.memory.list_domains()
    assert harness.memory.metadata[-1]["authorization"] == f"Bearer {TOKEN}"


async def test_auth_metadata_is_sent_on_every_method(async_client: AsyncMemco, harness: Harness):
    await async_client.memory.list_domains()
    await async_client.memory.start_session("coding")  # StartSession, then ListTools
    assert len(harness.memory.metadata) == 3
    for sent in harness.memory.metadata:
        assert sent["authorization"] == f"Bearer {TOKEN}"


async def test_each_call_carries_exactly_one_credential(async_client: AsyncMemco, harness: Harness):
    # Counted off the metadata as received, since a dict collapses repeats.
    await async_client.memory.list_domains()
    await async_client.memory.start_session("coding")  # StartSession and ListTools
    assert len(harness.memory.raw_metadata) == 3
    for sent in harness.memory.raw_metadata:
        assert [value for key, value in sent if key == "authorization"] == [f"Bearer {TOKEN}"]


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
    assert harness.memory.calls == ["ListDomains"]


async def test_the_fetched_limits_are_applied_to_later_calls(harness: Harness):
    harness.memory.responses["ListDomains"] = pb.ListDomainsResponse(
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
        caplog.at_level(logging.ERROR, logger="memcoai"),
        pytest.raises(errors.MemcoAuthenticationError),
    ):
        async with AsyncMemco(token=TOKEN, host=harness.address, tls=False):
            pass
    assert [record.name for record in caplog.records] == ["memcoai._aio"]
    assert TOKEN not in caplog.text


# --- error translation ---------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, errors.MemcoAuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, errors.MemcoPermissionError),
        (grpc.StatusCode.FAILED_PRECONDITION, errors.MemcoPreconditionFailedError),
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


async def test_a_call_landing_on_a_closed_channel_is_refused_unchained(async_client: AsyncMemco):
    # The race close() leaves open: a call past the closed check reaches the
    # channel as it is torn down. grpc's frames hold the metadata the call was
    # sent with, bearer and all, so its error is chained to nothing raised.
    async def torn_down(request: object, **sent: object) -> None:
        raise grpc.aio.UsageError("Channel is closed")

    with pytest.raises(errors.MemcoConfigError, match="closed") as caught:
        await async_client._send(torn_down, pb.ListDomainsRequest(), 1.0, Minted(TOKEN, 0.0))
    assert (caught.value.__cause__, caught.value.__context__) == (None, None)


async def test_memco_api_tls_false_dials_a_plaintext_server(harness: Harness):
    env = {"MEMCO_API_TOKEN": TOKEN, "MEMCO_API_TLS": "false"}
    async with AsyncMemco(host=harness.address, env=env):
        pass


async def test_validation_fires_before_any_rpc(async_client: AsyncMemco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        await async_client.memory.search("   ", domain="coding")
    assert harness.memory.calls == []


# --- the operations ------------------------------------------------------


async def test_every_operation_round_trips(async_client: AsyncMemco, harness: Harness):
    harness.memory.responses["ListDomains"] = pb.ListDomainsResponse(
        domains=[pb.DomainEntry(slug="coding")]
    )
    assert [d.slug for d in (await async_client.memory.list_domains()).domains] == ["coding"]

    session = await async_client.memory.start_session("coding")
    assert session.id == "session-a"

    assert (await async_client.memory.search("q", session_id=session.id)).session_id == "session-a"
    assert (await async_client.memory.get_memory("memory-a-1")).idx == "memory-a-1"

    created = await async_client.memory.create_memory(
        query="q", title="t", content="c", domain="coding"
    )
    assert created.operation_id == "create-a"

    enriched = await async_client.memory.enrich_memory(
        memory_idx="new", session_id=session.id, title="t", content="c"
    )
    assert enriched.operation_id == "enrich-a"

    rated = await async_client.memory.share_feedback(
        session_id=session.id,
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
        session_id=session.id,
    )
    assert [o.status for o in imported.results] == [types.ImportStatus.QUEUED]


# --- lifecycle -----------------------------------------------------------


async def test_context_manager_closes_the_channel(harness: Harness):
    async with AsyncMemco(token=TOKEN, host=harness.address, tls=False) as connected:
        await connected.memory.list_domains()
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        await connected.memory.list_domains()


async def test_double_close_is_safe(harness: Harness):
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    await connected.close()
    await connected.close()


async def test_a_failed_reconnect_returns_once_the_calls_it_waits_for_are_done(
    async_client: AsyncMemco, harness: Harness
):
    # A failed connect() drops the channel and waits for the calls still on it
    # before closing it. A call arriving meanwhile opens a fresh channel, and
    # must not leave connect() waiting on a signal nothing will ever give.
    session = await async_client.memory.start_session("coding")
    hold = harness.memory.holds["Search"] = Hold()
    held = asyncio.create_task(session.search("held"))
    assert await asyncio.to_thread(hold.arrived.wait, 5)
    harness.health.status = health_pb2.HealthCheckResponse.NOT_SERVING
    reconnecting = asyncio.create_task(async_client.connect())
    for _ in range(500):
        if async_client._channel is None:
            break
        await asyncio.sleep(0.01)
    harness.health.status = health_pb2.HealthCheckResponse.SERVING
    await session.get_memory("memory-a-1")
    hold.released.set()
    await held
    with pytest.raises(errors.MemcoUnhealthyError):
        await asyncio.wait_for(reconnecting, 5)


async def test_provenance_is_reachable(async_client: AsyncMemco):
    assert async_client.provenance().server_commit


async def test_the_user_agent_is_sent_by_the_async_client(
    async_client: AsyncMemco, harness: Harness
):
    await async_client.memory.list_domains()
    sent = harness.memory.metadata[-1]["user-agent"]
    # Prepended, not appended: gRPC's own token must survive or the transport
    # becomes unidentifiable.
    assert sent.startswith(f"memco-python/{memcoai.__version__}")
    # The asyncio transport identifies itself as grpc-python-asyncio, so match
    # the stem rather than the synchronous spelling.
    assert "grpc-python" in sent
