"""Proto message to frozen dataclass conversion."""

import dataclasses
from datetime import date

import pytest

from memco import types
from memco._convert import (
    to_domain_list,
    to_feedback_result,
    to_memory,
    to_revert_result,
    to_search_result,
    to_session,
    to_write_result,
)
from memco.memory.v1 import memory_pb2 as pb


def test_enums_fold_unknown_wire_values_to_unspecified():
    assert types.DataSource.from_wire(1) is types.DataSource.USER
    assert types.DataSource.from_wire(999) is types.DataSource.UNSPECIFIED
    assert types.RevertOutcome.from_wire(4) is types.RevertOutcome.MERGED
    assert types.RevertOutcome.from_wire(999) is types.RevertOutcome.UNSPECIFIED


def test_results_are_frozen():
    session = to_session(pb.StartSessionResponse(session_id="session-a"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        session.session_id = "other"  # type: ignore[misc]


def test_instructions_keep_empty_strings():
    # The contract documents an empty part as meaningful, so it stays a str.
    session = to_session(pb.StartSessionResponse(session_id="s"))
    assert session.instructions.content == ""
    assert session.instructions.policy == ""


def test_repeated_fields_become_tuples():
    result = to_domain_list(
        pb.ListDomainsResponse(
            domains=[pb.DomainEntry(slug="coding", filter_tag_types=["language"])]
        )
    )
    assert isinstance(result.domains, tuple)
    assert isinstance(result.domains[0].filter_tag_types, tuple)
    assert result.domains[0].filter_tag_types == ("language",)


def test_insight_updated_parses_to_a_date():
    memory = to_memory(
        pb.MemoryResult(idx="memory-a-1", insights=[pb.InsightResult(updated="2026-08-26")])
    )
    assert memory.insights[0].updated == date(2026, 8, 26)


@pytest.mark.parametrize("raw", ["", "not-a-date", "2026-13-99"])
def test_unparseable_updated_becomes_none(raw):
    memory = to_memory(pb.MemoryResult(idx="m", insights=[pb.InsightResult(updated=raw)]))
    assert memory.insights[0].updated is None


def test_empty_optional_strings_become_none():
    search = to_search_result(pb.SearchResponse(session_id="s", notice=""))
    assert search.notice is None
    memory = to_memory(pb.MemoryResult(idx="m", reference=""))
    assert memory.reference is None
    feedback = to_feedback_result(
        pb.ShareFeedbackResponse(session_id="s", entries=[pb.FeedbackEntry(idx="i", advice="")])
    )
    assert feedback.entries[0].advice is None


def test_populated_optional_strings_are_kept():
    search = to_search_result(pb.SearchResponse(session_id="s", notice="heads up"))
    assert search.notice == "heads up"


def test_empty_operation_id_becomes_none():
    # The contract documents an empty operation id as an un-revertible write.
    assert to_write_result(pb.CreateMemoryResponse(operation_id="")).operation_id is None
    assert to_write_result(pb.CreateMemoryResponse(operation_id="op-1")).operation_id == "op-1"


def test_revert_result_carries_a_typed_outcome():
    result = to_revert_result(
        pb.RevertMemoryResponse(operation_id="op-1", outcome=pb.REVERT_OUTCOME_MEMORY_REMOVED)
    )
    assert result.outcome is types.RevertOutcome.MEMORY_REMOVED


def test_search_result_nests_memories_and_insights():
    response = pb.SearchResponse(
        session_id="session-a",
        memories=[
            pb.MemoryResult(
                idx="memory-a-1",
                kind="insight",
                times_served=3,
                intents=["why does X happen"],
                insights=[
                    pb.InsightResult(
                        idx="memory-a-1-insight-1",
                        title="T",
                        content="C",
                        updated="2026-08-26",
                        times_served=2,
                        endorsed=1,
                        disputed=0,
                    )
                ],
            )
        ],
    )
    result = to_search_result(response)
    assert result.session_id == "session-a"
    memory = result.memories[0]
    assert (memory.idx, memory.kind, memory.times_served) == ("memory-a-1", "insight", 3)
    assert memory.intents == ("why does X happen",)
    insight = memory.insights[0]
    assert (insight.title, insight.endorsed, insight.disputed) == ("T", 1, 0)


def test_tag_round_trips_through_the_wire():
    tag = types.Tag(type="language", value="python", version="3.12")
    assert tag.to_proto().version == "3.12"
    bare = types.Tag(type="task", value="implementation")
    assert not bare.to_proto().HasField("version")


def test_feedback_rating_round_trips_through_the_wire():
    rating = types.FeedbackRating(idx="memory-a-1", relevant=True, correct=False, comment="note")
    message = rating.to_proto()
    assert (message.idx, message.relevant, message.correct) == ("memory-a-1", True, False)
    assert message.comment == "note"
    assert (
        not types.FeedbackRating(idx="i", relevant=True, correct=True)
        .to_proto()
        .HasField("comment")
    )
