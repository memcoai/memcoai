"""Conversion from generated protobuf messages to the SDK's public types.

Kept in one place so the protobuf layer never leaks past the client methods.
Every function here takes a generated message and returns an immutable
dataclass from :mod:`memco.types`.

Two conventions apply throughout:

* An empty protobuf string becomes ``None`` where absence is meaningful — an
  un-minted operation id, a missing notice, reference or advice.
* An empty :class:`~memco.types.Instructions` part stays an empty string,
  because the contract documents "nothing to say" as a real state there.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import TYPE_CHECKING

from memco.memory.v1 import memory_pb2 as _pb

from .types import (
    AsyncMemory,
    DomainEntry,
    DomainList,
    FeedbackEntry,
    FeedbackResult,
    ImportOutcome,
    ImportResult,
    ImportStatus,
    Insight,
    Instructions,
    Limits,
    Memory,
    RevertOutcome,
    RevertResult,
    SearchResult,
    WriteResult,
)

if TYPE_CHECKING:  # pragma: no cover - avoids a cycle with operations.py
    from .operations import AsyncMemoryOperations, MemoryOperations

__all__ = [
    "to_async_memory",
    "to_async_search_result",
    "to_domain_list",
    "to_feedback_result",
    "to_import_result",
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
        return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007
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


def to_memory(
    message: _pb.MemoryResult,
    *,
    operations: MemoryOperations | None = None,
    session_id: str = "",
) -> Memory:
    """Convert a ``MemoryResult`` message.

    Args:
        message: The generated message.
        operations: The namespace ``feedback()`` submits through, or ``None``
            to build a memory with no feedback capability at all.
        session_id: The session ``feedback()`` records a rating under. An empty
            string is what a memory fetched by ``get_memory`` carries: nothing
            recorded it under a session, so a rating has nowhere to attach.

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
        _operations=operations,
        _session_id=session_id,
    )


def to_async_memory(
    message: _pb.MemoryResult,
    *,
    operations: AsyncMemoryOperations | None = None,
    session_id: str = "",
) -> AsyncMemory:
    """Convert a ``MemoryResult`` message, for the asyncio client.

    Mirrors :func:`to_memory`; see it for what each argument means.

    Args:
        message: The generated message.
        operations: The namespace ``feedback()`` submits through.
        session_id: The session ``feedback()`` records a rating under.

    Returns:
        The immutable equivalent.
    """
    return AsyncMemory(
        idx=message.idx,
        kind=message.kind,
        times_served=message.times_served,
        intents=tuple(message.intents),
        insights=tuple(_to_insight(insight) for insight in message.insights),
        reference=_optional(message.reference),
        _operations=operations,
        _session_id=session_id,
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
        max_tags_per_query=message.max_tags_per_query,
    )


def _to_limits(message: _pb.ListDomainsResponse) -> Limits | None:
    """Convert the limits a response carries, if it carries any.

    Args:
        message: The generated response.

    Returns:
        The limits, or ``None`` when the service reported none. ``None`` means
        "validate nothing": reading an absent message as zeros would reject
        every call before it was sent.
    """
    if not message.HasField("limits"):
        return None
    limits = message.limits
    return Limits(
        max_query_characters=limits.max_query_characters,
        max_text_characters=limits.max_text_characters,
        max_idx_characters=limits.max_idx_characters,
        max_sources=limits.max_sources,
        max_feedback_entries=limits.max_feedback_entries,
        max_import_memories=limits.max_import_memories,
        max_import_queries_per_memory=limits.max_import_queries_per_memory,
        max_import_insights_per_memory=limits.max_import_insights_per_memory,
        max_import_tags_per_memory=limits.max_import_tags_per_memory,
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
        limits=_to_limits(message),
        deprecated=message.deprecated,
        deprecation_message=message.deprecation_message,
        sunset_date=_to_date(message.sunset_date),
        server_commit=message.server_commit,
    )


def to_session(message: _pb.StartSessionResponse) -> tuple[str, Instructions]:
    """Convert a ``StartSessionResponse``.

    Returns the fields rather than a :class:`~memco.operations.Session`: that
    class holds a live reference to the namespace that opened it, and this
    module must stay a pure function of the wire message -- ``operations.py``
    is where the rich object is assembled.

    Args:
        message: The generated response.

    Returns:
        The session id, and the guidance that came with opening it.
    """
    return message.session_id, _to_instructions(message.instructions)


def to_search_result(
    message: _pb.SearchResponse, *, operations: MemoryOperations | None = None
) -> SearchResult[Memory]:
    """Convert a ``SearchResponse``.

    Args:
        message: The generated response.
        operations: The namespace each memory's ``feedback()`` submits
            through, bound to this response's own session id.

    Returns:
        The immutable equivalent, with an empty notice mapped to ``None``.
    """
    return SearchResult(
        session_id=message.session_id,
        memories=tuple(
            to_memory(memory, operations=operations, session_id=message.session_id)
            for memory in message.memories
        ),
        notice=_optional(message.notice),
        instructions=_to_instructions(message.instructions),
    )


def to_async_search_result(
    message: _pb.SearchResponse, *, operations: AsyncMemoryOperations | None = None
) -> SearchResult[AsyncMemory]:
    """Convert a ``SearchResponse``, for the asyncio client.

    Mirrors :func:`to_search_result`; see it for what each argument means.

    Args:
        message: The generated response.
        operations: The namespace each memory's ``feedback()`` submits
            through.

    Returns:
        The immutable equivalent.
    """
    return SearchResult(
        session_id=message.session_id,
        memories=tuple(
            to_async_memory(memory, operations=operations, session_id=message.session_id)
            for memory in message.memories
        ),
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
        :attr:`~memco.types.RevertOutcome.UNSPECIFIED`.
    """
    return RevertResult(
        operation_id=_optional(message.operation_id),
        outcome=RevertOutcome.from_wire(message.outcome),
        instructions=_to_instructions(message.instructions),
    )


def _to_import_outcome(message: _pb.ImportOutcome, offset: int) -> ImportOutcome:
    """Convert one entry of an import response.

    Args:
        message: The generated outcome.
        offset: The position the group's first memory held in the whole batch.

    Returns:
        The immutable equivalent, with the status as a typed enum and the index
        moved from the group's own numbering to the caller's. A status this SDK
        does not recognise folds to
        :attr:`~memco.types.ImportStatus.UNSPECIFIED`.
    """
    return ImportOutcome(
        index=offset + message.index,
        status=ImportStatus.from_wire(message.status),
        errors=tuple(message.errors),
    )


def to_import_result(
    answered: Sequence[tuple[int, _pb.ImportMemoriesResponse]],
) -> ImportResult:
    """Convert the responses to the calls one import took.

    A batch above the service's per-call cap is sent as several calls, and each
    numbers its own results from zero. Re-numbering them against the batch the
    caller submitted is what keeps :attr:`~memco.types.ImportOutcome.index`
    meaning what it says — without it a split batch reports position 0 once per
    group and identifies nothing.

    Args:
        answered: One ``(offset, response)`` per call made, in order. Never
            empty: an empty batch is refused before any call is built.

    Returns:
        The immutable equivalent, one outcome per memory submitted, in the order
        they were sent.
    """
    return ImportResult(
        results=tuple(
            _to_import_outcome(outcome, offset)
            for offset, message in answered
            for outcome in message.results
        ),
        # The calls are one operation in one domain, so their guidance is the
        # same; the first is as good as any and there is always one.
        instructions=_to_instructions(answered[0][1].instructions),
    )
