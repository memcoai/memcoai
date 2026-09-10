"""Proto message to frozen dataclass conversion."""

from datetime import date
from typing import cast

import pytest

from memcoai import types
from memcoai._convert import (
    to_async_memory,
    to_async_search_result,
    to_domain_list,
    to_feedback_result,
    to_import_result,
    to_memory,
    to_revert_result,
    to_search_result,
    to_session,
    to_write_result,
)
from memcoai.memory.v1 import memory_pb2 as pb
from memcoai.operations import AsyncMemoryOperations, MemoryOperations

# A stand-in for an operations namespace: to_memory/to_search_result only ever
# store what they are given, so identity, not behaviour, is what these tests
# check. Cast rather than a real MemoryOperations, which needs a live stub.
_SENTINEL_SYNC = cast(MemoryOperations, object())
_SENTINEL_ASYNC = cast(AsyncMemoryOperations, object())


def test_enums_fold_unknown_wire_values_to_unspecified():
    assert types.DataSource.from_wire(1) is types.DataSource.USER
    assert types.DataSource.from_wire(999) is types.DataSource.UNSPECIFIED
    assert types.RevertOutcome.from_wire(4) is types.RevertOutcome.MERGED
    assert types.RevertOutcome.from_wire(999) is types.RevertOutcome.UNSPECIFIED
    assert types.ImportStatus.from_wire(4) is types.ImportStatus.DUPLICATE
    assert types.ImportStatus.from_wire(999) is types.ImportStatus.UNSPECIFIED


def test_session_converts_to_a_plain_id_and_instructions_pair():
    # operations.py builds the rich Session/AsyncSession from this tuple, since
    # _convert.py must not import operations.py (that would cycle).
    session_id, instructions = to_session(pb.StartSessionResponse(session_id="session-a"))
    assert session_id == "session-a"
    assert isinstance(instructions, types.Instructions)


def test_instructions_keep_empty_strings():
    # The contract documents an empty part as meaningful, so it stays a str.
    _, instructions = to_session(pb.StartSessionResponse(session_id="s"))
    assert instructions.content == ""
    assert instructions.policy == ""


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


def test_server_commit_reaches_the_caller():
    # It names the build that answered, which is what a bug report quotes. It is
    # not provenance().server_commit, which is the commit this wheel was built from.
    assert to_domain_list(pb.ListDomainsResponse(server_commit="8317b7b")).server_commit == (
        "8317b7b"
    )
    assert to_domain_list(pb.ListDomainsResponse()).server_commit == ""


def test_import_result_carries_one_typed_outcome_per_entry():
    # The indices are deliberately not their own positions: reading them off
    # enumerate() instead of off the field would pass an in-order fixture.
    result = to_import_result(
        [
            (
                0,
                pb.ImportMemoriesResponse(
                    results=[
                        pb.ImportOutcome(index=2, status=pb.IMPORT_STATUS_QUEUED),
                        pb.ImportOutcome(index=0, status=pb.IMPORT_STATUS_DUPLICATE),
                        pb.ImportOutcome(
                            index=1,
                            status=pb.IMPORT_STATUS_REJECTED,
                            errors=["queries must not be empty"],
                        ),
                    ]
                ),
            )
        ]
    )
    assert isinstance(result.results, tuple)
    assert [outcome.index for outcome in result.results] == [2, 0, 1]
    assert result.results[0].status is types.ImportStatus.QUEUED
    assert result.results[1].status is types.ImportStatus.DUPLICATE
    assert result.results[2].errors == ("queries must not be empty",)
    # An entry that was queued carries no errors, rather than a None.
    assert result.results[0].errors == ()


def test_split_groups_are_renumbered_against_the_whole_batch():
    # Each call numbers its own results from zero. Merging them without the
    # offset would report index 0 once per group and identify nothing.
    result = to_import_result(
        [
            (
                0,
                pb.ImportMemoriesResponse(
                    results=[
                        pb.ImportOutcome(index=0, status=pb.IMPORT_STATUS_QUEUED),
                        pb.ImportOutcome(index=1, status=pb.IMPORT_STATUS_QUEUED),
                    ],
                    instructions=pb.Instructions(content="first"),
                ),
            ),
            (
                2,
                pb.ImportMemoriesResponse(
                    results=[
                        pb.ImportOutcome(index=0, status=pb.IMPORT_STATUS_DUPLICATE),
                        pb.ImportOutcome(index=1, status=pb.IMPORT_STATUS_QUEUED),
                    ],
                    instructions=pb.Instructions(content="second"),
                ),
            ),
        ]
    )
    assert [outcome.index for outcome in result.results] == [0, 1, 2, 3]
    assert result.results[2].status is types.ImportStatus.DUPLICATE
    # The groups are one operation in one domain; one set of guidance is right.
    assert result.instructions.content == "first"


def test_an_imported_insight_converts_to_its_wire_message():
    message = types.ImportedInsight(title="T", content="C").to_proto()
    assert (message.title, message.content) == ("T", "C")


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


def test_to_memory_defaults_to_unbound():
    memory = to_memory(pb.MemoryResult(idx="m"))
    assert memory._operations is None
    assert memory._session_id == ""


def test_to_memory_attaches_the_operations_and_session_it_was_given():
    memory = to_memory(pb.MemoryResult(idx="m"), operations=_SENTINEL_SYNC, session_id="session-a")
    assert memory._operations is _SENTINEL_SYNC
    assert memory._session_id == "session-a"


def test_to_async_memory_attaches_the_operations_and_session_it_was_given():
    memory = to_async_memory(
        pb.MemoryResult(idx="m"), operations=_SENTINEL_ASYNC, session_id="session-a"
    )
    assert isinstance(memory, types.AsyncMemory)
    assert memory._operations is _SENTINEL_ASYNC
    assert memory._session_id == "session-a"


def test_to_search_result_binds_every_memory_to_the_response_s_own_session():
    response = pb.SearchResponse(
        session_id="session-a", memories=[pb.MemoryResult(idx="m1"), pb.MemoryResult(idx="m2")]
    )
    result = to_search_result(response, operations=_SENTINEL_SYNC)
    assert all(memory._operations is _SENTINEL_SYNC for memory in result.memories)
    assert all(memory._session_id == "session-a" for memory in result.memories)


def test_to_search_result_defaults_to_unbound_memories():
    response = pb.SearchResponse(session_id="session-a", memories=[pb.MemoryResult(idx="m1")])
    result = to_search_result(response)
    assert result.memories[0]._operations is None


def test_to_async_search_result_builds_async_memories():
    response = pb.SearchResponse(session_id="session-a", memories=[pb.MemoryResult(idx="m1")])
    result = to_async_search_result(response, operations=_SENTINEL_ASYNC)
    assert isinstance(result.memories[0], types.AsyncMemory)
    assert result.memories[0]._operations is _SENTINEL_ASYNC


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


# --- the hand-written enums must not drift from the contract -------------


def test_data_source_matches_the_contract():
    for member in types.DataSource:
        assert pb.DataSource.Name(member.value) == f"DATA_SOURCE_{member.name}"
    assert len(types.DataSource) == len(pb.DataSource.keys())


def test_revert_outcome_matches_the_contract():
    for member in types.RevertOutcome:
        assert pb.RevertOutcome.Name(member.value) == f"REVERT_OUTCOME_{member.name}"
    assert len(types.RevertOutcome) == len(pb.RevertOutcome.keys())


def test_import_status_matches_the_contract():
    for member in types.ImportStatus:
        assert pb.ImportStatus.Name(member.value) == f"IMPORT_STATUS_{member.name}"
    assert len(types.ImportStatus) == len(pb.ImportStatus.keys())
