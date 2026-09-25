"""Request construction and argument validation shared by both clients.

Keeping this apart from the transport means the synchronous and asynchronous
clients validate identically and build identical messages; they differ only in
how they await the response.
"""

from __future__ import annotations

import contextlib
import itertools
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import datetime
from typing import TypeVar, cast

import grpc
from google.protobuf.message import Message

from memcoai.admin.v1 import admin_pb2 as _admin_pb
from memcoai.auth.v1 import auth_pb2 as _auth_pb
from memcoai.memory.v1 import memory_pb2 as _pb

from . import _validate
from ._config import ClientConfig
from ._limits import Known
from .errors import MemcoNotFoundError
from .types import DataSource, FeedbackRating, ImportedInsight, ImportedMemory, Tag

_M = TypeVar("_M", bound=Message)

__all__ = [
    "add_network_group_request",
    "add_network_member_request",
    "create_external_user_key_request",
    "create_external_user_request",
    "create_memory_request",
    "create_network_request",
    "delete_external_user_key_request",
    "delete_external_user_request",
    "delete_network_request",
    "end_impersonation_request",
    "enrich_memory_request",
    "get_external_user_request",
    "get_memory_request",
    "impersonate_request",
    "import_memories_requests",
    "issue_token_request",
    "list_domains_request",
    "list_external_user_keys_request",
    "list_external_users_request",
    "list_group_members_request",
    "list_groups_request",
    "list_network_members_request",
    "list_networks_request",
    "list_tools_request",
    "remove_network_group_request",
    "remove_network_member_request",
    "require_memory",
    "revert_memory_request",
    "search_request",
    "share_feedback_request",
    "start_session_request",
    "update_external_user_request",
    "update_network_request",
]


def _built(build: Callable[[], _M]) -> _M:
    """Construct a request message, reporting a rejected value as a typed error.

    protobuf refuses text it cannot encode — a lone surrogate, which arrives
    routinely from a mis-decoded filename or scraped JSON — with a
    :class:`UnicodeEncodeError`. Message construction happens outside the RPC
    call, so that would escape untyped and defeat the guarantee that every
    failure is a :class:`~memcoai.errors.MemcoError`.

    Args:
        build: Callable constructing the message.

    Returns:
        The constructed message.

    Raises:
        MemcoInvalidRequestError: If a field value cannot be serialised.
    """
    try:
        return build()
    except (UnicodeError, ValueError) as exc:
        raise _validate.reject(f"a field value cannot be sent: {exc}") from exc


def _tags(tags: Iterable[Tag] | None, cap: int = 0) -> list[_pb.Tag]:
    """Convert public tags to wire messages.

    Call this from inside the ``_built`` callable, never before it. Building a
    tag constructs a protobuf message, so a tag carrying text protobuf cannot
    encode raises there — and outside the guard that escapes as a raw
    ``UnicodeEncodeError``. Validation failures are unaffected either way: they
    are not ``ValueError``, so the guard does not rewrite them.

    Args:
        tags: The tags supplied by the caller, if any. Consumed exactly once,
            so a generator is safe here.
        cap: The domain's tag cap. The service trims rather than refusing, so
            this trims too; zero means no cap was reported.

    Returns:
        The wire messages, empty when no tags were given.

    Raises:
        MemcoInvalidRequestError: If a tag's type or value is blank.
    """
    return [tag.to_proto() for tag in _validate.trim(_validate.check_tags(tags), cap, "tags")]


def _check_handle(value: str, field: str, known: Known | None) -> None:
    """Apply the reported cap on handle-shaped values.

    Args:
        value: The handle supplied by the caller.
        field: Field name, used verbatim in the error message.
        known: What the service has reported, if anything.

    Raises:
        MemcoInvalidRequestError: If a cap is known and the handle exceeds it.
    """
    if known and known.limits:
        _validate.check_within(value, field, known.limits.max_idx_characters)


def _check_text_together(
    title: str, content: str, caps: Known, field: str = "title and content"
) -> None:
    """Apply the reported cap on title and content, which it bounds jointly.

    Args:
        title: The title supplied by the caller.
        content: The content supplied by the caller.
        caps: What the service has reported.
        field: Field name, used verbatim in the error message. A call carrying
            more than one pair names which one this is.

    Raises:
        MemcoInvalidRequestError: If their combined length exceeds the cap.
    """
    limit = caps.limits.max_text_characters if caps.limits else 0
    if limit and len(title) + len(content) > limit:
        raise _validate.reject(
            f"{field} are {len(title) + len(content)} characters together, "
            f"which exceeds the combined limit of {limit}"
        )


def _source(source: DataSource) -> _pb.DataSource:
    """Convert a public data source to its wire value.

    Args:
        source: The public enum member.

    Returns:
        The wire value. The generated enum is an ``int`` subclass, so this is a
        typing concern only and changes nothing at runtime.
    """
    return cast(_pb.DataSource, source.value)


def issue_token_request(config: ClientConfig) -> _auth_pb.IssueTokenRequest:
    """Build the ``IssueToken`` request exchanging the client's credentials for a token.

    Args:
        config: Resolved client settings holding the client credentials.

    Returns:
        The request message. It names no scope, which takes everything the API
        client was granted, and a ``token_lifetime`` of ``None`` is sent as 0,
        which takes the service's default.

    Raises:
        MemcoInvalidRequestError: If a value cannot be sent, such as a
            ``token_lifetime`` too large for the wire.
    """
    with contextlib.suppress(UnicodeError, ValueError):
        return _auth_pb.IssueTokenRequest(
            grant_type="client_credentials",
            client_id=config.client_id,
            client_secret=config.client_secret,
            ttl_seconds=config.token_lifetime or 0,
        )
    # Raised outside the handler, unlike _built's, so the encoding error is not
    # even this one's context: it holds the text it refused, and here that may
    # be the secret.
    raise _validate.reject(
        "the client credentials or token_lifetime cannot be sent: a credential is "
        "not valid Unicode, or the lifetime is too large"
    )


def list_domains_request() -> _pb.ListDomainsRequest:
    """Build a ``ListDomains`` request.

    Returns:
        The request message. It carries no fields: the call is the answer to
        "which domain?", so it takes no domain of its own.
    """
    return _pb.ListDomainsRequest()


def list_tools_request() -> _pb.ListToolsRequest:
    """Build a ``ListTools`` request.

    Returns:
        The request message. It carries no fields: the catalog is the same for
        every caller, and only availability depends on who is asking.
    """
    return _pb.ListToolsRequest()


def revert_memory_request(operation_id: str, known: Known | None = None) -> _pb.RevertMemoryRequest:
    """Validate and build a ``RevertMemory`` request.

    Args:
        operation_id: The operation id a create or enrich returned.
        known: What the service has reported about its own limits, if anything.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the operation id is blank or too long.
    """
    _validate.check_operation_id(operation_id)
    _check_handle(operation_id, "operation_id", known)
    return _built(lambda: _pb.RevertMemoryRequest(op_id=operation_id))


def start_session_request(domain: str) -> _pb.StartSessionRequest:
    """Validate and build a ``StartSession`` request.

    Args:
        domain: The memory domain the session belongs to.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the domain is blank or too long.
    """
    _validate.check_domain(domain)
    return _built(lambda: _pb.StartSessionRequest(domain=domain))


def search_request(
    query: str,
    *,
    domain: str | None,
    session_id: str | None,
    tags: Iterable[Tag] | None,
    known: Known | None = None,
) -> _pb.SearchRequest:
    """Validate and build a ``Search`` request.

    Args:
        query: The task-based query.
        domain: The memory domain, if the search is not scoped by a session.
        session_id: The session to record this search under, if any.
        tags: Tags narrowing or boosting the results. Consumed exactly once,
            so a generator is safe.
        known: What the service has reported about its own limits, if anything.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the query is invalid, or if neither a
            domain nor a session was given.
    """
    _validate.check_query(query)
    _validate.check_scope(domain=domain, session_id=session_id)
    caps = known or Known()
    if caps.limits:
        _validate.check_within(query, "query", caps.limits.max_query_characters)
    cap = caps.max_tags(domain)
    return _built(
        lambda: _pb.SearchRequest(
            query=query,
            domain=domain or "",
            session_id=session_id or "",
            tags=_tags(tags, cap),
        )
    )


def get_memory_request(idx: str, known: Known | None = None) -> _pb.GetMemoryRequest:
    """Validate and build a ``GetMemory`` request.

    Args:
        idx: The handle to fetch.
        known: What the service has reported about its own limits, if anything.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the handle is blank or too long.
    """
    _validate.check_idx(idx)
    _check_handle(idx, "idx", known)
    return _built(lambda: _pb.GetMemoryRequest(idx=idx))


def create_memory_request(
    *,
    query: str,
    title: str,
    content: str,
    domain: str | None,
    session_id: str | None,
    tags: Iterable[Tag] | None,
    source: DataSource,
    known: Known | None = None,
) -> _pb.CreateMemoryRequest:
    """Validate and build a ``CreateMemory`` request.

    Args:
        query: What someone would search to find this memory.
        title: Short title for the memory.
        content: The knowledge being saved.
        domain: The memory domain, if the write is not scoped by a session.
        session_id: The session this memory was learned during, if any.
        tags: Tags describing the memory.
        source: Who produced the content.
        known: What the service has reported about its own limits, if anything.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If any field is invalid, or if neither a
            domain nor a session was given.
    """
    _validate.check_query(query)
    _validate.check_title(title)
    _validate.check_content(content)
    _validate.check_scope(domain=domain, session_id=session_id)
    caps = known or Known()
    if caps.limits:
        _validate.check_within(query, "query", caps.limits.max_query_characters)
        _check_text_together(title, content, caps)
    cap = caps.max_tags(domain)
    return _built(
        lambda: _pb.CreateMemoryRequest(
            query=query,
            title=title,
            content=content,
            domain=domain or "",
            session_id=session_id or "",
            tags=_tags(tags, cap),
            source=_source(source),
        )
    )


def enrich_memory_request(
    *,
    memory_idx: str,
    session_id: str,
    title: str,
    content: str,
    tags: Iterable[Tag] | None,
    sources: Iterable[str] | None,
    source: DataSource,
    known: Known | None = None,
) -> _pb.EnrichMemoryRequest:
    """Validate and build an ``EnrichMemory`` request.

    Args:
        memory_idx: The memory to enrich, or ``"new"`` to open one.
        session_id: The session the memory was returned under. Required, because
            it supplies the domain.
        title: Short title for the addition.
        content: The knowledge being added.
        tags: Tags describing the addition.
        sources: Handles of the memories this addition draws on. Consumed
            exactly once, so a generator is safe.
        source: Who produced the content.
        known: What the service has reported about its own limits, if anything.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If any field is invalid.
    """
    _validate.check_memory_idx(memory_idx)
    _validate.check_session_id(session_id)
    _validate.check_title(title)
    _validate.check_content(content)
    materialised = _validate.check_sources(sources)
    caps = known or Known()
    if caps.limits:
        _check_text_together(title, content, caps)
        _check_handle(memory_idx, "memory_idx", known)
        for entry in materialised:
            _check_handle(entry, "sources entry", known)
        # The service keeps the first max_sources and drops the rest, so raising
        # here would reject a call it would have accepted.
        materialised = _validate.trim(materialised, caps.limits.max_sources, "sources")
    return _built(
        lambda: _pb.EnrichMemoryRequest(
            memory_idx=memory_idx,
            session_id=session_id,
            title=title,
            content=content,
            tags=_tags(tags, caps.max_tags(None)),
            sources=materialised,
            source=_source(source),
        )
    )


def share_feedback_request(
    *, session_id: str, feedback: Iterable[FeedbackRating], known: Known | None = None
) -> _pb.ShareFeedbackRequest:
    """Validate and build a ``ShareFeedback`` request.

    Args:
        session_id: The session whose search is being rated.
        feedback: The ratings to record.
        known: What the service has reported about its own limits, if anything.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the session or any rating is invalid.
    """
    _validate.check_session_id(session_id)
    rated = _validate.check_feedback(feedback)
    caps = known or Known()
    if caps.limits:
        _validate.check_count(len(rated), "feedback", caps.limits.max_feedback_entries)
        for rating in rated:
            _check_handle(rating.idx, "feedback idx", known)
    return _built(
        lambda: _pb.ShareFeedbackRequest(
            session_id=session_id, feedback=[rating.to_proto() for rating in rated]
        )
    )


def import_memories_requests(
    memories: Iterable[ImportedMemory],
    *,
    domain: str | None,
    session_id: str | None,
    known: Known | None = None,
) -> Iterator[tuple[int, _pb.ImportMemoriesRequest]]:
    """Take the batch a group at a time, yielding the call that carries each.

    ``max_import_memories`` bounds one *call*, not one batch, and it refuses
    rather than trims — so a batch above it is taken in groups of that size and
    sent as several calls. A caller hands over whatever it has without having to
    learn the number or chunk against it. Nothing is dropped: the groups
    partition the batch in order. An unreported cap means the service rules, as
    everywhere else, so the batch goes out whole.

    Each group is materialised, validated and built as it is reached, and the
    caller sends it before the next is taken. So ``memories`` may be any
    iterable — a generator, a ``map``, a cursor — and only one group is ever
    held. The cost is that a bad entry half way through is found half way
    through, with the groups before it already written; resending the whole
    batch is the remedy, and is safe, because the service answers ``DUPLICATE``
    for what already landed.

    The three per-entry caps refuse too, and are checked against what the caller
    actually supplied. The per-domain tag cap still trims, and is applied after
    the refusing one so that the refusal stays reachable.

    Args:
        memories: The memories to contribute.
        domain: The memory domain, if the import is not scoped by a session.
        session_id: The session this knowledge was contributed during, if any.
        known: What the service has reported about its own limits, if anything.

    Yields:
        One ``(offset, request)`` per call to make, in order. The offset is the
        position the group's first memory held in the whole batch, which is what
        turns each response's own numbering back into the caller's.

    Raises:
        MemcoInvalidRequestError: If the batch or any entry is invalid, or if
            neither a domain nor a session was given.
    """
    _validate.check_scope(domain=domain, session_id=session_id)
    caps = known or Known()
    cap = caps.max_tags(domain)

    def one_call(taken: Sequence[ImportedMemory]) -> _pb.ImportMemoriesRequest:
        """Build the request carrying one group.

        Every message is constructed inside the guard, entries included:
        building them into a list first would put the construction most likely
        to hold unencodable text — a whole group of it — outside the only thing
        that turns protobuf's refusal into a typed error.

        Args:
            taken: The group's memories, already materialised and validated.

        Returns:
            The request message.
        """
        return _built(
            lambda: _pb.ImportMemoriesRequest(
                domain=domain or "",
                session_id=session_id or "",
                memories=[
                    _pb.ImportedMemory(
                        queries=list(memory.queries),
                        insights=[insight.to_proto() for insight in memory.insights],
                        tags=_tags(memory.tags, cap),
                    )
                    for memory in taken
                ],
            )
        )

    # An unreported cap is one group holding everything, so the batch goes out
    # whole rather than against a size the SDK made up.
    group = caps.limits.max_import_memories if caps.limits else 0
    pending = iter(memories)
    offset = 0
    while True:
        raw = list(itertools.islice(pending, group)) if group else list(pending)
        if not raw:
            break
        taken = _validate.check_import_memories(raw, offset)
        if caps.limits:
            limits = caps.limits
            for index, memory in enumerate(taken):
                where = f"memories[{offset + index}]"
                # check_import_memories materialised these. The dataclass
                # declares Iterable because that is what a caller may hand in;
                # only what comes back out of the check is known re-walkable.
                queries = cast("Sequence[str]", memory.queries)
                insights = cast("Sequence[ImportedInsight]", memory.insights)
                tags = cast("Sequence[Tag]", memory.tags or ())
                _validate.check_count(
                    len(queries), f"{where} queries", limits.max_import_queries_per_memory
                )
                _validate.check_count(
                    len(insights), f"{where} insights", limits.max_import_insights_per_memory
                )
                _validate.check_count(len(tags), f"{where} tags", limits.max_import_tags_per_memory)
                for at, insight in enumerate(insights):
                    _check_text_together(
                        insight.title,
                        insight.content,
                        caps,
                        f"{where} insights[{at}] title and content",
                    )
        yield offset, one_call(taken)
        offset += len(taken)
    if not offset:
        # Only knowable once nothing came out, since the batch is taken lazily.
        raise _validate.reject("memories must contain at least one memory")


def require_memory(response: _pb.GetMemoryResponse, idx: str) -> _pb.MemoryResult:
    """Return the memory a ``GetMemory`` response carries.

    A singular message field has no presence at the accessor, so an unset one
    reads back as a default instance. Converting that would hand the caller a
    memory with every field empty, indistinguishable from a real one, instead of
    reporting that the handle resolved to nothing.

    Args:
        response: The generated response.
        idx: The handle that was requested, for the error message.

    Returns:
        The memory message.

    Raises:
        MemcoNotFoundError: If the response carries no memory.
    """
    if not response.HasField("memory"):
        raise MemcoNotFoundError(grpc.StatusCode.NOT_FOUND, f"no memory was returned for {idx!r}")
    return response.memory


# -- administration -------------------------------------------------------
#
# An argument left as None is handed to the message as it is: protobuf reads a
# keyword given None as a field never set. That is what leaves an unused filter
# unsent, and what lets a patch tell "leave this alone" (None) from "clear
# this" ("") on the fields the contract marks optional.


def list_networks_request(
    *,
    name: str | None,
    scope: str | None,
    owner: str | None,
    domain: str | None,
    parent_id: str | None,
    ids: Iterable[str] | None,
    page: int | None,
    page_size: int | None,
) -> _admin_pb.ListNetworksRequest:
    """Validate and build a ``ListNetworks`` request.

    Args:
        name: Keeps networks with this name.
        scope: Keeps networks of this scope.
        owner: Keeps networks with this owner.
        domain: Keeps networks of this memory domain.
        parent_id: Keeps the children of this network.
        ids: Keeps only these networks. Consumed exactly once.
        page: The page to return.
        page_size: How many networks a page holds.

    Returns:
        The request message, carrying only the filters given.

    Raises:
        MemcoInvalidRequestError: If ``ids`` is a single string, or a value
            cannot be sent.
    """
    wanted = _validate.check_strings(ids, "ids")
    return _built(
        lambda: _admin_pb.ListNetworksRequest(
            name=name,
            scope=scope,
            owner=owner,
            domain=domain,
            parent_id=parent_id,
            ids=wanted,
            page=page,
            page_size=page_size,
        )
    )


def create_network_request(
    *,
    name: str,
    parent_id: str | None,
    domain: str | None,
    region: str | None,
    scope: str | None,
    owner: str | None,
    description: str | None,
) -> _admin_pb.CreateNetworkRequest:
    """Build a ``CreateNetwork`` request.

    Nothing is checked here: every field is one the service defaults or
    refuses on its own terms.

    Args:
        name: The network's name.
        parent_id: The network to create this one under.
        domain: The memory domain of a root network.
        region: The network's data residency.
        scope: The network's scope.
        owner: Who the network's knowledge belongs to.
        description: What the network is for.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If a value cannot be sent.
    """
    return _built(
        lambda: _admin_pb.CreateNetworkRequest(
            name=name,
            parent_id=parent_id,
            domain=domain,
            region=region,
            scope=scope,
            owner=owner,
            description=description,
        )
    )


def update_network_request(
    network_id: str,
    *,
    name: str | None,
    parent_id: str | None,
    scope: str | None,
    owner: str | None,
    description: str | None,
) -> _admin_pb.UpdateNetworkRequest:
    """Validate and build an ``UpdateNetwork`` request.

    Args:
        network_id: The network to change.
        name: The new name, or ``None`` to leave it.
        parent_id: The new parent, or ``None`` to leave it.
        scope: The new scope, or ``None`` to leave it.
        owner: The new owner, or ``None`` to leave it.
        description: The new description, or ``None`` to leave it.

    Returns:
        The request message, with only the fields given set.

    Raises:
        MemcoInvalidRequestError: If the network is blank, or a value cannot
            be sent.
    """
    _validate.check_idx(network_id, "network_id")
    return _built(
        lambda: _admin_pb.UpdateNetworkRequest(
            id=network_id,
            name=name,
            parent_id=parent_id,
            scope=scope,
            owner=owner,
            description=description,
        )
    )


def delete_network_request(network_id: str) -> _admin_pb.DeleteNetworkRequest:
    """Validate and build a ``DeleteNetwork`` request.

    Args:
        network_id: The network to delete.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the network is blank, or cannot be sent.
    """
    _validate.check_idx(network_id, "network_id")
    return _built(lambda: _admin_pb.DeleteNetworkRequest(id=network_id))


def list_network_members_request(
    network_id: str, *, search: str | None, page: int | None, page_size: int | None
) -> _admin_pb.ListNetworkMembersRequest:
    """Validate and build a ``ListNetworkMembers`` request.

    Args:
        network_id: The network whose members to list.
        search: Keeps members matching this text.
        page: The page to return.
        page_size: How many members a page holds.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the network is blank, or a value cannot
            be sent.
    """
    _validate.check_idx(network_id, "network_id")
    return _built(
        lambda: _admin_pb.ListNetworkMembersRequest(
            id=network_id, search=search, page=page, page_size=page_size
        )
    )


def add_network_member_request(
    network_id: str, user_id: str, *, force: bool
) -> _admin_pb.AddNetworkMemberRequest:
    """Validate and build an ``AddNetworkMember`` request.

    Args:
        network_id: The network to place the user in.
        user_id: The user to place.
        force: Whether to move a user already placed in another network of the
            same memory domain.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the network or the user is blank, or a
            value cannot be sent.
    """
    _validate.check_idx(network_id, "network_id")
    _validate.check_idx(user_id, "user_id")
    return _built(
        lambda: _admin_pb.AddNetworkMemberRequest(id=network_id, user_id=user_id, force=force)
    )


def remove_network_member_request(
    network_id: str, user_id: str
) -> _admin_pb.RemoveNetworkMemberRequest:
    """Validate and build a ``RemoveNetworkMember`` request.

    Args:
        network_id: The network to take the user out of.
        user_id: The user to take out.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the network or the user is blank, or a
            value cannot be sent.
    """
    _validate.check_idx(network_id, "network_id")
    _validate.check_idx(user_id, "user_id")
    return _built(lambda: _admin_pb.RemoveNetworkMemberRequest(id=network_id, user_id=user_id))


def list_groups_request(
    *,
    name: str | None,
    network_id: str | None,
    ids: Iterable[str] | None,
    page: int | None,
    page_size: int | None,
) -> _admin_pb.ListGroupsRequest:
    """Validate and build a ``ListGroups`` request.

    Args:
        name: Keeps groups with this name.
        network_id: Keeps groups bound to this network.
        ids: Keeps only these groups. Consumed exactly once.
        page: The page to return.
        page_size: How many groups a page holds.

    Returns:
        The request message, carrying only the filters given.

    Raises:
        MemcoInvalidRequestError: If ``ids`` is a single string, or a value
            cannot be sent.
    """
    wanted = _validate.check_strings(ids, "ids")
    return _built(
        lambda: _admin_pb.ListGroupsRequest(
            name=name, network_id=network_id, ids=wanted, page=page, page_size=page_size
        )
    )


def list_group_members_request(group_id: str) -> _admin_pb.ListGroupMembersRequest:
    """Validate and build a ``ListGroupMembers`` request.

    Args:
        group_id: The group whose members to list.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the group is blank, or cannot be sent.
    """
    _validate.check_idx(group_id, "group_id")
    return _built(lambda: _admin_pb.ListGroupMembersRequest(id=group_id))


def add_network_group_request(network_id: str, group_id: str) -> _admin_pb.AddNetworkGroupRequest:
    """Validate and build an ``AddNetworkGroup`` request.

    Args:
        network_id: The network to assign the group to.
        group_id: The group to assign.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the network or the group is blank, or a
            value cannot be sent.
    """
    _validate.check_idx(network_id, "network_id")
    _validate.check_idx(group_id, "group_id")
    return _built(lambda: _admin_pb.AddNetworkGroupRequest(id=network_id, group_id=group_id))


def remove_network_group_request(
    network_id: str, group_id: str
) -> _admin_pb.RemoveNetworkGroupRequest:
    """Validate and build a ``RemoveNetworkGroup`` request.

    Args:
        network_id: The network to take the group out of.
        group_id: The group to take out.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the network or the group is blank, or a
            value cannot be sent.
    """
    _validate.check_idx(network_id, "network_id")
    _validate.check_idx(group_id, "group_id")
    return _built(lambda: _admin_pb.RemoveNetworkGroupRequest(id=network_id, group_id=group_id))


def list_external_users_request(
    *, search: str | None, page: int | None, page_size: int | None
) -> _admin_pb.ListExternalUsersRequest:
    """Build a ``ListExternalUsers`` request.

    Args:
        search: Keeps users matching this text.
        page: The page to return.
        page_size: How many users a page holds.

    Returns:
        The request message, carrying only the filters given.

    Raises:
        MemcoInvalidRequestError: If a value cannot be sent.
    """
    return _built(
        lambda: _admin_pb.ListExternalUsersRequest(search=search, page=page, page_size=page_size)
    )


def get_external_user_request(external_id: str) -> _admin_pb.GetExternalUserRequest:
    """Validate and build a ``GetExternalUser`` request.

    Args:
        external_id: The user to fetch, by your own id for them.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the id is blank, or cannot be sent.
    """
    _validate.check_idx(external_id, "external_id")
    return _built(lambda: _admin_pb.GetExternalUserRequest(external_id=external_id))


def create_external_user_request(
    external_id: str, *, roles: Iterable[str], name: str | None, email: str | None
) -> _admin_pb.CreateExternalUserRequest:
    """Validate and build a ``CreateExternalUser`` request.

    Args:
        external_id: Your own id for the new user.
        roles: The roles the user holds. Consumed exactly once.
        name: The user's name.
        email: The user's email address.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the id is blank, ``roles`` is a single
            string or names no role, or a value cannot be sent.
    """
    _validate.check_idx(external_id, "external_id")
    held = _validate.check_roles(roles)
    return _built(
        lambda: _admin_pb.CreateExternalUserRequest(
            external_id=external_id, name=name, email=email, roles=held
        )
    )


def update_external_user_request(
    external_id: str, *, name: str | None, email: str | None, roles: Iterable[str] | None
) -> _admin_pb.UpdateExternalUserRequest:
    """Validate and build an ``UpdateExternalUser`` request.

    Args:
        external_id: The user to change.
        name: The new name, or ``None`` to leave it.
        email: The new email address, or ``None`` to leave it.
        roles: The roles to replace the user's with, or ``None`` to leave
            them. Consumed exactly once.

    Returns:
        The request message, with only the fields given set.

    Raises:
        MemcoInvalidRequestError: If the id is blank, ``roles`` is given as a
            single string or naming no role, or a value cannot be sent.
    """
    _validate.check_idx(external_id, "external_id")
    held = None if roles is None else _validate.check_roles(roles)
    return _built(
        lambda: _admin_pb.UpdateExternalUserRequest(
            external_id=external_id, name=name, email=email, roles=held
        )
    )


def delete_external_user_request(external_id: str) -> _admin_pb.DeleteExternalUserRequest:
    """Validate and build a ``DeleteExternalUser`` request.

    Args:
        external_id: The user to delete.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the id is blank, or cannot be sent.
    """
    _validate.check_idx(external_id, "external_id")
    return _built(lambda: _admin_pb.DeleteExternalUserRequest(external_id=external_id))


def list_external_user_keys_request(external_id: str) -> _admin_pb.ListExternalUserKeysRequest:
    """Validate and build a ``ListExternalUserKeys`` request.

    Args:
        external_id: The user whose keys to list.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the id is blank, or cannot be sent.
    """
    _validate.check_idx(external_id, "external_id")
    return _built(lambda: _admin_pb.ListExternalUserKeysRequest(external_id=external_id))


def create_external_user_key_request(
    external_id: str, *, preset: str, name: str | None, valid_until: datetime | None
) -> _admin_pb.CreateExternalUserKeyRequest:
    """Validate and build a ``CreateExternalUserKey`` request.

    Args:
        external_id: The user to create the key for.
        preset: The kind of key to create.
        name: The key's name.
        valid_until: When the key expires, or ``None`` for the service's
            default.

    Returns:
        The request message, with the expiry as Unix seconds.

    Raises:
        MemcoInvalidRequestError: If the id is blank, the expiry names no time
            zone, or a value cannot be sent.
    """
    _validate.check_idx(external_id, "external_id")
    _validate.check_aware(valid_until, "valid_until")
    return _built(
        lambda: _admin_pb.CreateExternalUserKeyRequest(
            external_id=external_id,
            name=name,
            preset=preset,
            valid_until=None if valid_until is None else int(valid_until.timestamp()),
        )
    )


def delete_external_user_key_request(
    external_id: str, key_id: str
) -> _admin_pb.DeleteExternalUserKeyRequest:
    """Validate and build a ``DeleteExternalUserKey`` request.

    Args:
        external_id: The user the key belongs to.
        key_id: The key to delete.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the id or the key is blank, or a value
            cannot be sent.
    """
    _validate.check_idx(external_id, "external_id")
    _validate.check_idx(key_id, "key_id")
    return _built(
        lambda: _admin_pb.DeleteExternalUserKeyRequest(external_id=external_id, key_id=key_id)
    )


def impersonate_request(external_id: str) -> _admin_pb.ImpersonateExternalUserRequest:
    """Build an ``ImpersonateExternalUser`` request minting a session's key.

    Args:
        external_id: The user the key acts as, already checked for blankness by
            the session opening it.

    Returns:
        The request message. It asks for no lifetime, which takes the
        service's default: the session renews its key before it expires, so a
        longer one would only leave a leaked key usable for longer.

    Raises:
        MemcoInvalidRequestError: If the id cannot be sent.
    """
    return _built(lambda: _admin_pb.ImpersonateExternalUserRequest(external_id=external_id))


def end_impersonation_request(external_id: str, key_id: str) -> _admin_pb.EndImpersonationRequest:
    """Build an ``EndImpersonation`` request revoking a session's key.

    Args:
        external_id: The user the key acts as.
        key_id: The key to revoke, as the service named it when minting it.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If a value cannot be sent.
    """
    return _built(lambda: _admin_pb.EndImpersonationRequest(external_id=external_id, key_id=key_id))
