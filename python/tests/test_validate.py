"""Pre-flight argument checks, which must fire before any RPC."""

import grpc
import pytest

from memco import _validate as v
from memco.errors import MemcoInvalidRequestError
from memco.types import FeedbackRating


def test_query_cap():
    v.check_query("a" * v.MAX_QUERY)
    with pytest.raises(MemcoInvalidRequestError, match="query"):
        v.check_query("a" * (v.MAX_QUERY + 1))


def test_blank_query_rejected():
    with pytest.raises(MemcoInvalidRequestError, match="query"):
        v.check_query("   ")


@pytest.mark.parametrize("field", ["title", "content"])
def test_title_and_content_cap_independently(field):
    check = getattr(v, f"check_{field}")
    check("a" * v.MAX_TEXT)
    with pytest.raises(MemcoInvalidRequestError, match=field):
        check("a" * (v.MAX_TEXT + 1))


def test_idx_cap():
    v.check_idx("m" * v.MAX_IDX)
    with pytest.raises(MemcoInvalidRequestError, match="idx"):
        v.check_idx("m" * (v.MAX_IDX + 1))
    with pytest.raises(MemcoInvalidRequestError, match="idx"):
        v.check_idx("")


def test_sources_cap():
    v.check_sources(["s"] * v.MAX_SOURCES)
    with pytest.raises(MemcoInvalidRequestError, match="sources"):
        v.check_sources(["s"] * (v.MAX_SOURCES + 1))


def test_feedback_cap_and_comment_cap():
    ratings = [FeedbackRating(idx="i", relevant=True, correct=True)] * v.MAX_FEEDBACK
    v.check_feedback(ratings)
    with pytest.raises(MemcoInvalidRequestError, match="feedback"):
        v.check_feedback([*ratings, FeedbackRating(idx="i", relevant=True, correct=True)])
    with pytest.raises(MemcoInvalidRequestError, match="comment"):
        v.check_feedback(
            [FeedbackRating(idx="i", relevant=True, correct=True, comment="c" * (v.MAX_TEXT + 1))]
        )


def test_feedback_must_not_be_empty():
    with pytest.raises(MemcoInvalidRequestError, match="feedback"):
        v.check_feedback([])


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
    # "New" is not the sentinel; it is treated as a handle and length-checked.
    with pytest.raises(MemcoInvalidRequestError, match="memory_idx"):
        v.check_memory_idx("N" * (v.MAX_IDX + 1))


def test_errors_carry_invalid_argument_status():
    with pytest.raises(MemcoInvalidRequestError) as caught:
        v.check_query("")
    assert caught.value.code is grpc.StatusCode.INVALID_ARGUMENT
