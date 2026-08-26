"""Conversion from generated protobuf messages to the SDK's public types.

Kept in one place so the protobuf layer never leaks past the client methods.
Every function here takes a generated message and returns an immutable
dataclass from :mod:`memco.client.types`.

Two conventions apply throughout:

* An empty protobuf string becomes ``None`` where absence is meaningful — an
  un-minted operation id, a missing notice, reference or advice.
* An empty :class:`~memco.client.types.Instructions` part stays an empty string,
  because the contract documents "nothing to say" as a real state there.
"""

from __future__ import annotations

from datetime import date, datetime

from memco.memory.v1 import memory_pb2 as _pb

from .types import (
    DomainEntry,
    DomainList,
    FeedbackEntry,
    FeedbackResult,
    Insight,
    Instructions,
    Memory,
    RevertOutcome,
    RevertResult,
    SearchResult,
    Session,
    WriteResult,
)

__all__ = [
    "to_domain_list",
    "to_feedback_result",
    "to_memory",
    "to_revert_result",
    "to_search_result",
    "to_session",
    "to_write_result",
]


def _optional(value: str) -> str | None:
    """Map an empty protobuf string to ``None``.

    Args:
        value: The string as it arrived on the wire.

    Returns:
        The string, or ``None`` when it is empty.
    """
    return value or None


def _to_date(value: str) -> date | None:
    """Parse a ``YYYY-MM-DD`` string into a date.

    Args:
        value: The date string, which may be empty or malformed.

    Returns:
        The parsed date, or ``None`` when the value is empty or unparseable. A
        malformed date is never fatal: the rest of the insight is still useful.

    Note:
        Uses an explicit format rather than :meth:`date.fromisoformat`, whose
        accepted grammar widened in Python 3.11. Without this, identical server
        output would parse differently on 3.10 than on 3.11 and later.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_instructions(message: _pb.Instructions) -> Instructions:
    """Convert an ``Instructions`` message.

    Args:
        message: The generated message. An unset field yields empty strings,
            which is the correct representation here.

    Returns:
        The immutable equivalent.
    """
    return Instructions(
        content=message.content,
        policy=message.policy,
        adding=message.adding,
        rating=message.rating,
        next=message.next,
    )


def _to_insight(message: _pb.InsightResult) -> Insight:
    """Convert an ``InsightResult`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent, with ``updated`` parsed to a date.
    """
    return Insight(
        idx=message.idx,
        title=message.title,
        content=message.content,
        updated=_to_date(message.updated),
        times_served=message.times_served,
        endorsed=message.endorsed,
        disputed=message.disputed,
    )


def to_memory(message: _pb.MemoryResult) -> Memory:
    """Convert a ``MemoryResult`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent, with nested insights converted and an empty
        reference mapped to ``None``.
    """
    return Memory(
        idx=message.idx,
        kind=message.kind,
        times_served=message.times_served,
        intents=tuple(message.intents),
        insights=tuple(_to_insight(insight) for insight in message.insights),
        reference=_optional(message.reference),
    )


def _to_domain_entry(message: _pb.DomainEntry) -> DomainEntry:
    """Convert a ``DomainEntry`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent, with repeated fields as tuples.
    """
    return DomainEntry(
        slug=message.slug,
        title=message.title,
        summary=message.summary,
        when_to_search=message.when_to_search,
        when_to_save=message.when_to_save,
        what_not_to_save=message.what_not_to_save,
        tags_description=message.tags_description,
        filter_tag_types=tuple(message.filter_tag_types),
        version_tag_types=tuple(message.version_tag_types),
    )


def to_domain_list(message: _pb.ListDomainsResponse) -> DomainList:
    """Convert a ``ListDomainsResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent.
    """
    return DomainList(
        domains=tuple(_to_domain_entry(domain) for domain in message.domains),
        instructions=_to_instructions(message.instructions),
    )


def to_session(message: _pb.StartSessionResponse) -> Session:
    """Convert a ``StartSessionResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent.
    """
    return Session(
        session_id=message.session_id,
        instructions=_to_instructions(message.instructions),
    )


def to_search_result(message: _pb.SearchResponse) -> SearchResult:
    """Convert a ``SearchResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent, with an empty notice mapped to ``None``.
    """
    return SearchResult(
        session_id=message.session_id,
        memories=tuple(to_memory(memory) for memory in message.memories),
        notice=_optional(message.notice),
        instructions=_to_instructions(message.instructions),
    )


def to_write_result(
    message: _pb.CreateMemoryResponse | _pb.EnrichMemoryResponse,
) -> WriteResult:
    """Convert a ``CreateMemoryResponse`` or ``EnrichMemoryResponse``.

    The two messages are structurally identical, so one converter serves both.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent, with an empty operation id mapped to ``None``
        to mark an un-revertible write.
    """
    return WriteResult(
        operation_id=_optional(message.operation_id),
        instructions=_to_instructions(message.instructions),
    )


def _to_feedback_entry(message: _pb.FeedbackEntry) -> FeedbackEntry:
    """Convert a ``FeedbackEntry`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent, with empty advice mapped to ``None``.
    """
    return FeedbackEntry(
        idx=message.idx,
        relevant=message.relevant,
        correct=message.correct,
        advice=_optional(message.advice),
    )


def to_feedback_result(message: _pb.ShareFeedbackResponse) -> FeedbackResult:
    """Convert a ``ShareFeedbackResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent.
    """
    return FeedbackResult(
        session_id=message.session_id,
        entries=tuple(_to_feedback_entry(entry) for entry in message.entries),
        instructions=_to_instructions(message.instructions),
    )


def to_revert_result(message: _pb.RevertMemoryResponse) -> RevertResult:
    """Convert a ``RevertMemoryResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent, with the outcome as a typed enum. An outcome
        this SDK does not recognise folds to
        :attr:`~memco.client.types.RevertOutcome.UNSPECIFIED`.
    """
    return RevertResult(
        operation_id=_optional(message.operation_id),
        outcome=RevertOutcome.from_wire(message.outcome),
        instructions=_to_instructions(message.instructions),
    )
