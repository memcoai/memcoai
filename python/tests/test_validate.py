"""Pre-flight argument checks, which must fire before any RPC."""

import grpc
import pytest

import memcoai
from memcoai import _validate as v
from memcoai.errors import MemcoInvalidRequestError
from memcoai.types import FeedbackRating, ImportedInsight, ImportedMemory, Tag


def imported(**overrides) -> ImportedMemory:
    """One valid imported memory, with fields swapped out per test."""
    fields = {
        "queries": ["how does X work"],
        "insights": [ImportedInsight(title="T", content="C")],
    }
    return ImportedMemory(**{**fields, **overrides})


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
    assert not [name for name in dir(memcoai) if name.startswith("MAX_")]
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


# --- import batches: a bad entry must be locatable in the batch -----------


def test_an_import_must_not_be_empty():
    with pytest.raises(MemcoInvalidRequestError, match="memories"):
        v.check_import_memories([])


def test_an_imported_memory_needs_a_query_and_an_insight():
    with pytest.raises(MemcoInvalidRequestError, match=r"memories\[0\].*query"):
        v.check_import_memories([imported(queries=[])])
    with pytest.raises(MemcoInvalidRequestError, match=r"memories\[0\].*insight"):
        v.check_import_memories([imported(insights=[])])


def test_a_bare_string_is_not_a_sequence_of_queries():
    # A str satisfies Sequence[str], so neither the annotation nor the type
    # checker catches this; iterating it would file one query per character.
    with pytest.raises(MemcoInvalidRequestError, match="single string"):
        v.check_import_memories([imported(queries="how does X work")])


def test_a_blank_field_names_the_entry_it_is_in():
    # A batch gives the caller no other way to find the offending entry.
    with pytest.raises(MemcoInvalidRequestError, match=r"memories\[1\] queries\[0\]"):
        v.check_import_memories([imported(), imported(queries=["  "])])
    with pytest.raises(MemcoInvalidRequestError, match=r"memories\[1\] insights\[0\] title"):
        v.check_import_memories(
            [imported(), imported(insights=[ImportedInsight(title=" ", content="C")])]
        )
    with pytest.raises(MemcoInvalidRequestError, match=r"memories\[1\] insights\[0\] content"):
        v.check_import_memories(
            [imported(), imported(insights=[ImportedInsight(title="T", content="")])]
        )
    with pytest.raises(MemcoInvalidRequestError, match=r"memories\[1\] tag value"):
        v.check_import_memories([imported(), imported(tags=[Tag(type="language", value="")])])


def test_import_batch_size_is_not_checked_locally():
    # As everywhere else: the service owns the numbers.
    v.check_import_memories([imported()] * 1000)
    v.check_import_memories([imported(queries=[f"q{n}" for n in range(1000)])])


def test_a_one_shot_iterable_survives_rather_than_being_silently_dropped():
    # Validation used to walk the caller's own object, and the request builder
    # then walked it again to size and build. A generator was empty by the
    # second pass, so the call sent nothing at all and reported success — and an
    # import mints no handle, so `results` coming back empty was the caller's
    # only signal. Materialising here, and building from what comes back, is
    # what makes a lazy source safe rather than refused.
    batch = v.check_import_memories(imported() for _ in range(3))
    assert len(batch) == 3

    entry = v.check_import_memories([imported(queries=(f"q{n}" for n in range(3)))])[0]
    assert list(entry.queries) == ["q0", "q1", "q2"]

    entry = v.check_import_memories(
        [imported(insights=(ImportedInsight(title=f"t{n}", content="c") for n in range(2)))]
    )[0]
    assert [insight.title for insight in entry.insights] == ["t0", "t1"]

    entry = v.check_import_memories(
        [imported(tags=(Tag(type="language", value=f"v{n}") for n in range(2)))]
    )[0]
    assert [tag.value for tag in entry.tags or ()] == ["v0", "v1"]

    rated = v.check_feedback(
        FeedbackRating(idx=f"memory-a-{n}", relevant=True, correct=True) for n in range(3)
    )
    assert [rating.idx for rating in rated] == ["memory-a-0", "memory-a-1", "memory-a-2"]


def test_an_entry_taken_from_the_middle_of_a_batch_names_its_absolute_position():
    # A group is validated as it is reached, so without the offset every message
    # would say memories[0] whatever the caller actually sent.
    with pytest.raises(MemcoInvalidRequestError, match=r"memories\[7\] must contain at least one"):
        v.check_import_memories([imported(queries=[])], offset=7)
