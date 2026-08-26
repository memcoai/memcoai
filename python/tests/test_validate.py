"""Pre-flight argument checks, which must fire before any RPC."""

import grpc
import pytest

import memco
from memco import _validate as v
from memco.errors import MemcoInvalidRequestError
from memco.types import FeedbackRating


def test_blank_values_are_rejected():
    for check, field in (
        (v.check_query, "query"),
        (v.check_title, "title"),
        (v.check_content, "content"),
        (v.check_idx, "idx"),
        (v.check_session_id, "session_id"),
        (v.check_domain, "domain"),
        (v.check_operation_id, "operation_id"),
    ):
        with pytest.raises(MemcoInvalidRequestError, match=field):
            check("   ")


def test_length_is_not_checked_locally():
    # The service owns its limits. A value compiled in here would go stale the
    # moment the service changed one, and an older SDK would reject requests
    # the service would now accept.
    v.check_query("q" * 100_000)
    v.check_title("t" * 100_000)
    v.check_content("c" * 100_000)
    v.check_idx("i" * 100_000)


def test_batch_size_is_not_checked_locally():
    v.check_sources([f"src-{i}" for i in range(1000)])
    v.check_feedback([FeedbackRating(idx="i", relevant=True, correct=True)] * 1000)


def test_no_numeric_limit_is_exported():
    assert not [name for name in dir(memco) if name.startswith("MAX_")]
    assert not [name for name in dir(v) if name.startswith("MAX_")]


def test_feedback_must_not_be_empty():
    with pytest.raises(MemcoInvalidRequestError, match="feedback"):
        v.check_feedback([])


def test_feedback_entries_are_still_checked_for_structure():
    with pytest.raises(MemcoInvalidRequestError, match="feedback idx"):
        v.check_feedback([FeedbackRating(idx="", relevant=True, correct=True)])


def test_scope_requires_domain_or_session():
    v.check_scope(domain="coding", session_id=None)
    v.check_scope(domain=None, session_id="session-a")
    v.check_scope(domain="coding", session_id="session-a")  # server resolves the pair
    with pytest.raises(MemcoInvalidRequestError, match="domain"):
        v.check_scope(domain=None, session_id=None)


def test_required_session_id():
    v.check_session_id("session-a")
    with pytest.raises(MemcoInvalidRequestError, match="session_id"):
        v.check_session_id("")


def test_memory_idx_accepts_the_new_sentinel_and_a_handle():
    v.check_memory_idx("new")
    v.check_memory_idx("memory-a-1")
    with pytest.raises(MemcoInvalidRequestError, match="memory_idx"):
        v.check_memory_idx("")


def test_memory_idx_sentinel_is_case_sensitive():
    # "New" is not the sentinel; it is treated as an ordinary handle, which is
    # only rejected here when blank.
    v.check_memory_idx("New")


def test_errors_carry_invalid_argument_status():
    with pytest.raises(MemcoInvalidRequestError) as caught:
        v.check_query("")
    assert caught.value.code is grpc.StatusCode.INVALID_ARGUMENT
