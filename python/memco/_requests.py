"""Request construction and argument validation shared by both clients.

Keeping this apart from the transport means the synchronous and asynchronous
clients validate identically and build identical messages; they differ only in
how they await the response.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import cast

from memco.memory.v1 import memory_pb2 as _pb

from . import _validate
from .types import DataSource, FeedbackRating, Tag

__all__ = [
    "create_memory_request",
    "enrich_memory_request",
    "get_memory_request",
    "list_domains_request",
    "revert_memory_request",
    "search_request",
    "share_feedback_request",
    "start_session_request",
]


def _tags(tags: Iterable[Tag] | None) -> list[_pb.Tag]:
    """Convert public tags to wire messages.

    Args:
        tags: The tags supplied by the caller, if any. Consumed exactly once,
            so a generator is safe here.

    Returns:
        The wire messages, empty when no tags were given.
    """
    return [tag.to_proto() for tag in (tags or ())]


def _source(source: DataSource) -> _pb.DataSource:
    """Convert a public data source to its wire value.

    Args:
        source: The public enum member.

    Returns:
        The wire value. The generated enum is an ``int`` subclass, so this is a
        typing concern only and changes nothing at runtime.
    """
    return cast(_pb.DataSource, source.value)


def list_domains_request() -> _pb.ListDomainsRequest:
    """Build a ``ListDomains`` request.

    Returns:
        The request message. It carries no fields: the call is the answer to
        "which domain?", so it takes no domain of its own.
    """
    return _pb.ListDomainsRequest()


def revert_memory_request(operation_id: str) -> _pb.RevertMemoryRequest:
    """Validate and build a ``RevertMemory`` request.

    Args:
        operation_id: The operation id a create or enrich returned.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the operation id is blank or too long.
    """
    _validate.check_operation_id(operation_id)
    return _pb.RevertMemoryRequest(op_id=operation_id)


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
    return _pb.StartSessionRequest(domain=domain)


def search_request(
    query: str,
    *,
    domain: str | None,
    session_id: str | None,
    tags: Iterable[Tag] | None,
) -> _pb.SearchRequest:
    """Validate and build a ``Search`` request.

    Args:
        query: The task-based query.
        domain: The memory domain, if the search is not scoped by a session.
        session_id: The session to record this search under, if any.
        tags: Tags narrowing or boosting the results. Consumed exactly once,
            so a generator is safe.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the query is invalid, or if neither a
            domain nor a session was given.
    """
    _validate.check_query(query)
    _validate.check_scope(domain=domain, session_id=session_id)
    return _pb.SearchRequest(
        query=query, domain=domain or "", session_id=session_id or "", tags=_tags(tags)
    )


def get_memory_request(idx: str) -> _pb.GetMemoryRequest:
    """Validate and build a ``GetMemory`` request.

    Args:
        idx: The handle to fetch.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the handle is blank or too long.
    """
    _validate.check_idx(idx)
    return _pb.GetMemoryRequest(idx=idx)


def create_memory_request(
    *,
    query: str,
    title: str,
    content: str,
    domain: str | None,
    session_id: str | None,
    tags: Iterable[Tag] | None,
    source: DataSource,
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
    return _pb.CreateMemoryRequest(
        query=query,
        title=title,
        content=content,
        domain=domain or "",
        session_id=session_id or "",
        tags=_tags(tags),
        source=_source(source),
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

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If any field is invalid.
    """
    _validate.check_memory_idx(memory_idx)
    _validate.check_session_id(session_id)
    _validate.check_title(title)
    _validate.check_content(content)
    materialised = list(sources or ())
    _validate.check_sources(materialised)
    return _pb.EnrichMemoryRequest(
        memory_idx=memory_idx,
        session_id=session_id,
        title=title,
        content=content,
        tags=_tags(tags),
        sources=materialised,
        source=_source(source),
    )


def share_feedback_request(
    *, session_id: str, feedback: Sequence[FeedbackRating]
) -> _pb.ShareFeedbackRequest:
    """Validate and build a ``ShareFeedback`` request.

    Args:
        session_id: The session whose search is being rated.
        feedback: The ratings to record.

    Returns:
        The request message.

    Raises:
        MemcoInvalidRequestError: If the session or any rating is invalid.
    """
    _validate.check_session_id(session_id)
    _validate.check_feedback(feedback)
    return _pb.ShareFeedbackRequest(
        session_id=session_id, feedback=[rating.to_proto() for rating in feedback]
    )
