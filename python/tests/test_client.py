"""The synchronous client, against a real in-process server."""

from __future__ import annotations

import logging

import grpc
import pytest
from grpc_health.v1 import health_pb2

import memcoai
from memcoai import Memco, errors, types
from memcoai._auth import _merged
from memcoai.memory.v1 import memory_pb2 as pb

from .conftest import TOKEN
from .fake_server import Harness

# --- auth ----------------------------------------------------------------


def test_auth_metadata_is_bearer_with_a_capital_b(client: Memco, harness: Harness):
    # The scheme prefix is matched case-sensitively, so the exact spelling of
    # this header is load-bearing.
    client.memory.list_domains()
    assert harness.memory.metadata[-1]["authorization"] == f"Bearer {TOKEN}"


def test_auth_metadata_is_sent_on_every_method(client: Memco, harness: Harness):
    client.memory.list_domains()
    client.memory.start_session("coding")
    client.memory.get_memory("memory-a-1")
    assert len(harness.memory.metadata) == 3
    for sent in harness.memory.metadata:
        assert sent["authorization"] == f"Bearer {TOKEN}"


def test_caller_metadata_cannot_displace_the_credential(client: Memco, harness: Harness):
    # gRPC allows repeated keys and servers disagree about which wins, so a
    # caller-supplied credential must be dropped rather than sent alongside.
    merged = _merged([("authorization", "Bearer ATTACKER"), ("x-other", "keep")], "real-token")
    assert merged == [("x-other", "keep"), ("authorization", "Bearer real-token")]


def test_the_credential_is_withheld_from_the_health_probe(harness: Harness):
    # The health endpoint takes no credential, so sending one there buys
    # nothing and widens its exposure to anything that logs request headers.
    with Memco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.health.metadata, "the health probe did not run"
    assert "authorization" not in harness.health.metadata[-1]


# --- health gate ---------------------------------------------------------


def test_construction_checks_health_with_the_empty_service_name(harness: Harness):
    with Memco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.health.checked_services == [""]
    # The probe is a real call and must identify itself too.
    assert "memco-python/" in harness.health.metadata[-1]["user-agent"]


def test_not_serving_raises_unhealthy(harness: Harness):
    harness.health.status = health_pb2.HealthCheckResponse.NOT_SERVING
    with pytest.raises(errors.MemcoUnhealthyError):
        Memco(token=TOKEN, host=harness.address, tls=False)


def test_unhealthy_is_catchable_as_unavailable(harness: Harness):
    harness.health.status = health_pb2.HealthCheckResponse.SERVICE_UNKNOWN
    with pytest.raises(errors.MemcoUnavailableError):
        Memco(token=TOKEN, host=harness.address, tls=False)


def test_unreachable_server_raises_unavailable():
    with pytest.raises(errors.MemcoUnavailableError):
        Memco(token=TOKEN, host="localhost:1", tls=False, timeout=2.0)


# --- limits are fetched on construction ----------------------------------


def test_construction_fetches_the_limits(harness: Harness):
    # The service owns its caps and publishes them here. Spending the round
    # trip once at construction is what lets every later call check locally.
    with Memco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.memory.calls == ["ListDomains"]


def test_the_fetched_limits_are_applied_to_later_calls(harness: Harness):
    harness.memory.responses["ListDomains"] = pb.ListDomainsResponse(
        limits=pb.Limits(max_query_characters=10)
    )
    with Memco(token=TOKEN, host=harness.address, tls=False) as built:
        harness.memory.calls.clear()
        with pytest.raises(errors.MemcoInvalidRequestError, match="query"):
            built.memory.search("x" * 11, domain="coding")
    assert harness.memory.calls == []


def test_a_bad_token_surfaces_at_construction(harness: Harness):
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid or insufficient credentials")
    with pytest.raises(errors.MemcoAuthenticationError):
        Memco(token=TOKEN, host=harness.address, tls=False)


def test_a_rejected_credential_is_logged(harness: Harness, caplog):
    # A client is often built deep inside a framework, where the traceback
    # reaches nobody, so the rejection is logged as well as raised. Capturing
    # on the parent `memcoai` logger while the record comes from `memcoai._sync`
    # is the point: configuring the one name governs the whole tree.
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid or insufficient credentials")
    with (
        caplog.at_level(logging.ERROR, logger="memcoai"),
        pytest.raises(errors.MemcoAuthenticationError),
    ):
        Memco(token=TOKEN, host=harness.address, tls=False)
    assert [record.name for record in caplog.records] == ["memcoai._sync"]
    # A credential must never reach a log.
    assert TOKEN not in caplog.text


# --- error translation ---------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, errors.MemcoAuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, errors.MemcoPermissionError),
        (grpc.StatusCode.INVALID_ARGUMENT, errors.MemcoInvalidRequestError),
        (grpc.StatusCode.NOT_FOUND, errors.MemcoNotFoundError),
        (grpc.StatusCode.FAILED_PRECONDITION, errors.MemcoPreconditionFailedError),
        (grpc.StatusCode.RESOURCE_EXHAUSTED, errors.MemcoResourceExhaustedError),
        (grpc.StatusCode.INTERNAL, errors.MemcoInternalError),
    ],
)
def test_server_errors_arrive_typed(client: Memco, harness: Harness, code, expected):
    harness.memory.error = (code, "boom")
    with pytest.raises(expected) as caught:
        client.memory.list_domains()
    assert caught.value.code is code


def test_no_raw_grpc_error_escapes(client: Memco, harness: Harness):
    harness.memory.error = (grpc.StatusCode.ABORTED, "nope")
    with pytest.raises(errors.MemcoError):
        client.memory.list_domains()


# --- validation fires before any RPC -------------------------------------


def test_blank_query_never_reaches_the_server(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.search("   ", domain="coding")
    assert harness.memory.calls == []


def test_an_over_long_query_is_left_for_the_service_to_judge(client: Memco, harness: Harness):
    # The SDK must not invent a limit: it would go stale against the service.
    client.memory.search("q" * 100_000, domain="coding")
    assert harness.memory.calls == ["Search"]


def test_missing_scope_never_reaches_the_server(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.search("fine")
    assert harness.memory.calls == []


def test_enrich_requires_a_session(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.enrich_memory(memory_idx="new", session_id="", title="t", content="c")
    assert harness.memory.calls == []


# --- the operations ------------------------------------------------------


def test_list_domains(client: Memco, harness: Harness):
    harness.memory.responses["ListDomains"] = pb.ListDomainsResponse(
        domains=[pb.DomainEntry(slug="coding", title="Software Development")]
    )
    result = client.memory.list_domains()
    assert [d.slug for d in result.domains] == ["coding"]


def test_start_session(client: Memco):
    assert client.memory.start_session("coding").id == "session-a"


def test_search_sends_query_domain_and_tags(client: Memco, harness: Harness):
    client.memory.search(
        "how does X work",
        domain="coding",
        tags=[types.Tag(type="language", value="python", version="3.12")],
    )
    assert harness.memory.calls == ["Search"]


def test_get_memory(client: Memco):
    assert client.memory.get_memory("memory-a-1").idx == "memory-a-1"


def test_create_memory(client: Memco):
    result = client.memory.create_memory(query="q", title="t", content="c", domain="coding")
    assert result.operation_id == "create-a"


def test_enrich_memory(client: Memco):
    result = client.memory.enrich_memory(
        memory_idx="new", session_id="session-a", title="t", content="c"
    )
    assert result.operation_id == "enrich-a"


def test_share_feedback(client: Memco):
    result = client.memory.share_feedback(
        session_id="session-a",
        feedback=[types.FeedbackRating(idx="memory-a-1", relevant=True, correct=True)],
    )
    assert result.session_id == "session-a"


def test_import_memories(client: Memco, harness: Harness):
    result = client.memory.import_memories(
        [
            types.ImportedMemory(
                queries=["how does X work", "and how does X fail"],
                insights=[types.ImportedInsight(title="first", content="one")],
            ),
            types.ImportedMemory(
                queries=["how does Y work"],
                insights=[types.ImportedInsight(title="second", content="two")],
            ),
        ],
        domain="coding",
    )
    # An import mints no handle, so an outcome is addressed by the position its
    # memory held in the request. Assert the batch off the wire: the outcomes
    # come back in arrival order whatever was sent, so reading them alone would
    # pass even if the entries had been reordered, dropped or flattened.
    sent = harness.memory.requests["ImportMemories"]
    assert sent.domain == "coding"
    assert [list(memory.queries) for memory in sent.memories] == [
        ["how does X work", "and how does X fail"],
        ["how does Y work"],
    ]
    assert [(i.title, i.content) for m in sent.memories for i in m.insights] == [
        ("first", "one"),
        ("second", "two"),
    ]
    assert [outcome.index for outcome in result.results] == [0, 1]
    assert all(o.status is types.ImportStatus.QUEUED for o in result.results)


def test_import_requires_a_domain_or_a_session(client: Memco, harness: Harness):
    harness.memory.calls.clear()
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.import_memories(
            [
                types.ImportedMemory(
                    queries=["q"], insights=[types.ImportedInsight(title="t", content="c")]
                )
            ]
        )
    assert harness.memory.calls == []


def test_revert_memory_reports_an_outcome_rather_than_raising(client: Memco):
    result = client.memory.revert_memory("create-a")
    assert result.outcome is types.RevertOutcome.MERGED


def test_revert_not_found_is_a_value_not_an_exception(client: Memco, harness: Harness):
    harness.memory.responses["RevertMemory"] = pb.RevertMemoryResponse(
        operation_id="create-a", outcome=pb.REVERT_OUTCOME_NOT_FOUND
    )
    assert client.memory.revert_memory("create-a").outcome is types.RevertOutcome.NOT_FOUND


# --- lifecycle -----------------------------------------------------------


def test_context_manager_closes_the_channel(harness: Harness):
    with Memco(token=TOKEN, host=harness.address, tls=False) as connected:
        connected.memory.list_domains()
    with pytest.raises(errors.MemcoConfigError, match="closed"):
        connected.memory.list_domains()


def test_double_close_is_safe(harness: Harness):
    connected = Memco(token=TOKEN, host=harness.address, tls=False)
    connected.close()
    connected.close()


def test_provenance_is_reachable_from_the_client(client: Memco):
    assert client.provenance().server_commit


# --- the client identifies itself to the service -------------------------


def test_the_user_agent_names_the_sdk_and_its_version(client: Memco, harness: Harness):
    # Asserted from what the server actually received, not from the channel
    # options, so a value that never leaves the client cannot pass this.
    client.memory.list_domains()
    sent = harness.memory.metadata[-1]["user-agent"]
    # A single stable product token: the service matches deprecation rules
    # against it, so a second term could break that match.
    assert sent.startswith(f"memco-python/{memcoai.__version__} ")


def test_the_user_agent_prepends_rather_than_replaces(client: Memco, harness: Harness):
    # gRPC's own token must survive, or the transport becomes unidentifiable.
    client.memory.list_domains()
    sent = harness.memory.metadata[-1]["user-agent"]
    assert sent.startswith("memco-python/")
    assert "grpc-python/" in sent


def test_the_user_agent_is_sent_on_every_method(client: Memco, harness: Harness):
    client.memory.list_domains()
    client.memory.start_session("coding")
    client.memory.get_memory("memory-a-1")
    # Without the count, this cannot distinguish "all three carried it" from
    # "nothing reached the wire at all".
    assert len(harness.memory.metadata) == 3
    for sent in harness.memory.metadata:
        assert "memco-python/" in sent["user-agent"]
