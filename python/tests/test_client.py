"""The synchronous client, against a real in-process server."""

from __future__ import annotations

import grpc
import pytest
from grpc_health.v1 import health_pb2

from memco import Memco, errors, types
from memco._auth import _merged
from memco.memory.v1 import memory_pb2 as pb

from .conftest import TOKEN
from .fake_server import Harness

# --- auth ----------------------------------------------------------------


def test_auth_metadata_is_bearer_with_a_capital_b(client: Memco, harness: Harness):
    # The server does a case-sensitive CutPrefix(v, "Bearer "), so the exact
    # spelling of this header is load-bearing.
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
    # Health bypasses auth server-side, so sending the token there buys nothing
    # and widens its exposure to load balancers and sidecars that log headers.
    with Memco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.health.metadata, "the health probe did not run"
    assert "authorization" not in harness.health.metadata[-1]


# --- health gate ---------------------------------------------------------


def test_construction_checks_health_with_the_empty_service_name(harness: Harness):
    with Memco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.health.checked_services == [""]


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


def test_check_health_false_skips_the_probe(harness: Harness):
    with Memco(token=TOKEN, host=harness.address, tls=False, check_health=False):
        pass
    assert harness.health.checked_services == []


# --- credential verification is opt-in -----------------------------------


def test_credentials_are_not_verified_by_default(harness: Harness):
    # ListDomains is rate-limited, so a construction probe would spend one of
    # the caller's per-minute tokens. It must not fire unless asked for.
    with Memco(token=TOKEN, host=harness.address, tls=False):
        pass
    assert harness.memory.calls == []


def test_verify_credentials_fires_list_domains(harness: Harness):
    with Memco(token=TOKEN, host=harness.address, tls=False, verify_credentials=True):
        pass
    assert harness.memory.calls == ["ListDomains"]


def test_verify_credentials_surfaces_a_bad_token(harness: Harness):
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "invalid or insufficient credentials")
    with pytest.raises(errors.MemcoAuthenticationError):
        Memco(token=TOKEN, host=harness.address, tls=False, verify_credentials=True)


# --- error translation ---------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, errors.MemcoAuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, errors.MemcoPermissionError),
        (grpc.StatusCode.INVALID_ARGUMENT, errors.MemcoInvalidRequestError),
        (grpc.StatusCode.NOT_FOUND, errors.MemcoNotFoundError),
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


def test_oversized_query_never_reaches_the_server(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.search("q" * 1001, domain="coding")
    assert harness.memory.calls == []


def test_missing_scope_never_reaches_the_server(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.search("fine")
    assert harness.memory.calls == []


def test_enrich_requires_a_session(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.enrich_memory(memory_idx="new", session_id="", title="t", content="c")
    assert harness.memory.calls == []


# --- the eight operations ------------------------------------------------


def test_list_domains(client: Memco, harness: Harness):
    harness.memory.responses["ListDomains"] = pb.ListDomainsResponse(
        domains=[pb.DomainEntry(slug="coding", title="Software Development")]
    )
    result = client.memory.list_domains()
    assert [d.slug for d in result.domains] == ["coding"]


def test_start_session(client: Memco):
    assert client.memory.start_session("coding").session_id == "session-a"


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
    with pytest.raises(errors.ClientConfigError, match="closed"):
        connected.memory.list_domains()


def test_double_close_is_safe(harness: Harness):
    connected = Memco(token=TOKEN, host=harness.address, tls=False)
    connected.close()
    connected.close()


def test_provenance_is_reachable_from_the_client(client: Memco):
    assert client.provenance().server_commit
