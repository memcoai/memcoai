"""Conversion from generated protobuf messages to the SDK's public types.

Kept in one place so the protobuf layer never leaks past the client methods.
Every function here takes a generated message and returns an immutable
dataclass from :mod:`memcoai.types`.

Two conventions apply throughout:

* An empty protobuf string becomes ``None`` where absence is meaningful — an
  un-minted operation id, a missing notice, reference or advice.
* An empty :class:`~memcoai.types.Instructions` part stays an empty string,
  because the contract documents "nothing to say" as a real state there.
* An instant arrives as Unix seconds and becomes an aware UTC
  :class:`~datetime.datetime`, with zero -- no instant at all -- as ``None``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import grpc

from memcoai.admin.v1 import admin_pb2 as _admin_pb
from memcoai.memory.v1 import memory_pb2 as _pb

from .errors import MemcoInternalError
from .types import (
    AsyncMemory,
    CreatedKey,
    DeletedNetwork,
    DomainEntry,
    DomainList,
    ExternalUser,
    ExternalUserKey,
    ExternalUserList,
    FeedbackEntry,
    FeedbackResult,
    Group,
    GroupList,
    ImportOutcome,
    ImportResult,
    ImportStatus,
    Insight,
    Instructions,
    Limits,
    Member,
    MemberList,
    MemberPlacement,
    Memory,
    Network,
    NetworkList,
    RevertOutcome,
    RevertResult,
    SearchResult,
    ToolDescriptor,
    WriteResult,
)

if TYPE_CHECKING:  # pragma: no cover - avoids a cycle with operations.py
    from .operations import AsyncMemoryOperations, MemoryOperations

__all__ = [
    "to_async_memory",
    "to_async_search_result",
    "to_created_key",
    "to_deleted_network",
    "to_domain_list",
    "to_external_user",
    "to_external_user_keys",
    "to_external_user_list",
    "to_feedback_result",
    "to_group_list",
    "to_group_members",
    "to_import_result",
    "to_member_list",
    "to_member_placement",
    "to_memory",
    "to_network",
    "to_network_list",
    "to_revert_result",
    "to_search_result",
    "to_session",
    "to_tool_list",
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


def _to_instant(seconds: int) -> datetime | None:
    """Convert Unix seconds into an aware UTC datetime.

    Args:
        seconds: The instant as it arrived on the wire.

    Returns:
        The instant in UTC, or ``None`` for zero, which is how the wire says
        there is none. Aware, so comparing it with a local time cannot silently
        be off by the local offset.
    """
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds else None


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


def _to_tool_descriptor(message: _pb.ToolDescriptor) -> ToolDescriptor:
    """Convert a ``ToolDescriptor`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent.
    """
    return ToolDescriptor(
        name=message.name,
        description=message.description,
        available=message.available,
    )


def to_tool_list(message: _pb.ListToolsResponse) -> tuple[ToolDescriptor, ...]:
    """Convert a ``ListToolsResponse``.

    Args:
        message: The generated response.

    Returns:
        One descriptor per method the contract declares.
    """
    return tuple(_to_tool_descriptor(tool) for tool in message.tools)


def to_session(message: _pb.StartSessionResponse) -> tuple[str, Instructions]:
    """Convert a ``StartSessionResponse``.

    Returns the fields rather than a :class:`~memcoai.operations.Session`: that
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
        :attr:`~memcoai.types.RevertOutcome.UNSPECIFIED`.
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
        :attr:`~memcoai.types.ImportStatus.UNSPECIFIED`.
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
    caller submitted is what keeps :attr:`~memcoai.types.ImportOutcome.index`
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


def to_network(message: _admin_pb.Network) -> Network:
    """Convert a ``Network`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent, with an empty parent, scope or owner mapped
        to ``None``: each is absent rather than blank.
    """
    return Network(
        id=message.id,
        name=message.name,
        parent_id=_optional(message.parent_id),
        domain=message.domain,
        region=message.region,
        scope=_optional(message.scope),
        owner=_optional(message.owner),
        description=message.description,
    )


def to_network_list(message: _admin_pb.ListNetworksResponse) -> NetworkList:
    """Convert a ``ListNetworksResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent.
    """
    return NetworkList(
        networks=tuple(to_network(network) for network in message.networks),
        total_count=message.total_count,
    )


def to_deleted_network(message: _admin_pb.DeleteNetworkResponse) -> DeletedNetwork:
    """Convert a ``DeleteNetworkResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent. The removed counts arrive as a map, whose
        order the wire does not keep, so they are sorted by table to read the
        same way every time.
    """
    return DeletedNetwork(id=message.id, removed=tuple(sorted(message.removed.items())))


def _to_member(message: _admin_pb.Member) -> Member:
    """Convert a ``Member`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent.
    """
    return Member(user_id=message.user_id, email=message.email, name=message.name)


def to_member_list(message: _admin_pb.ListNetworkMembersResponse) -> MemberList:
    """Convert a ``ListNetworkMembersResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent.
    """
    return MemberList(
        members=tuple(_to_member(member) for member in message.members),
        total_count=message.total_count,
    )


def to_member_placement(message: _admin_pb.AddNetworkMemberResponse) -> MemberPlacement:
    """Convert an ``AddNetworkMemberResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent, with an empty ``moved_from`` mapped to
        ``None``: nobody was moved.
    """
    return MemberPlacement(
        network_id=message.id,
        user_id=message.user_id,
        moved_from=_optional(message.moved_from),
    )


def to_group_list(message: _admin_pb.ListGroupsResponse) -> GroupList:
    """Convert a ``ListGroupsResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent, with an empty network mapped to ``None``: the
        group is assigned to none.
    """
    return GroupList(
        groups=tuple(
            Group(
                id=group.id,
                name=group.name,
                memory_network_id=_optional(group.memory_network_id),
                member_count=group.member_count,
            )
            for group in message.groups
        ),
        total_count=message.total_count,
    )


def to_group_members(message: _admin_pb.ListGroupMembersResponse) -> tuple[Member, ...]:
    """Convert a ``ListGroupMembersResponse``.

    Args:
        message: The generated response.

    Returns:
        The group's members.
    """
    return tuple(_to_member(member) for member in message.members)


def to_external_user(message: _admin_pb.ExternalUser) -> ExternalUser:
    """Convert an ``ExternalUser`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent, with the roles as a tuple.
    """
    return ExternalUser(
        id=message.id,
        external_id=message.external_id,
        name=message.name,
        email=message.email,
        roles=tuple(message.roles),
        active=message.active,
    )


def to_external_user_list(message: _admin_pb.ListExternalUsersResponse) -> ExternalUserList:
    """Convert a ``ListExternalUsersResponse``.

    Args:
        message: The generated response.

    Returns:
        The immutable equivalent.
    """
    return ExternalUserList(
        external_users=tuple(to_external_user(user) for user in message.external_users),
        total_count=message.total_count,
    )


def _to_external_user_key(message: _admin_pb.ExternalUserKey) -> ExternalUserKey:
    """Convert an ``ExternalUserKey`` message.

    Args:
        message: The generated message.

    Returns:
        The immutable equivalent, with the expiry as an aware UTC datetime.
    """
    return ExternalUserKey(
        id=message.id,
        name=message.name,
        value_prefix=message.value_prefix,
        roles=tuple(message.roles),
        scopes=tuple(message.scopes),
        valid_until=_to_instant(message.valid_until),
    )


def to_external_user_keys(
    message: _admin_pb.ListExternalUserKeysResponse,
) -> tuple[ExternalUserKey, ...]:
    """Convert a ``ListExternalUserKeysResponse``.

    Args:
        message: The generated response.

    Returns:
        The user's keys, described without their values.
    """
    return tuple(_to_external_user_key(key) for key in message.keys)


def to_created_key(message: _admin_pb.CreateExternalUserKeyResponse) -> CreatedKey:
    """Convert a ``CreateExternalUserKeyResponse``.

    Args:
        message: The generated response.

    Returns:
        The key's description, and its value.

    Raises:
        MemcoInternalError: If the response carries no key. A singular message
            field has no presence at the accessor, so converting it anyway
            would describe a key with every field empty, as if it were real.
    """
    if not message.HasField("key"):
        # The value is left out of the message: it is a working credential,
        # and an error's text is exactly what reaches logs. It is cleared from
        # the response too, which this frame holds as the error is raised.
        message.ClearField("value")
        raise MemcoInternalError(
            grpc.StatusCode.INTERNAL,
            "the service returned a key value but no key; list the user's keys to "
            "see whether one was created",
        )
    return CreatedKey(key=_to_external_user_key(message.key), value=message.value)
