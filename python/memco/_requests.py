"""Request construction and argument validation shared by both clients.

Keeping this apart from the transport means the synchronous and asynchronous
clients validate identically and build identical messages; they differ only in
how they await the response.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar, cast

import grpc
from google.protobuf.message import Message

from memco.memory.v1 import memory_pb2 as _pb

from . import _validate
from ._limits import Known
from .errors import MemcoNotFoundError
from .types import DataSource, FeedbackRating, ImportedMemory, Tag

_M = TypeVar("_M", bound=Message)

__all__ = [
    "create_memory_request",
    "enrich_memory_request",
    "get_memory_request",
    "import_memories_requests",
    "list_domains_request",
    "require_memory",
    "revert_memory_request",
    "search_request",
    "share_feedback_request",
    "start_session_request",
]


def _built(build: Callable[[], _M]) -> _M:
    """Construct a request message, reporting a rejected value as a typed error.

    protobuf refuses text it cannot encode — a lone surrogate, which arrives
    routinely from a mis-decoded filename or scraped JSON — with a
    :class:`UnicodeEncodeError`. Message construction happens outside the RPC
    call, so that would escape untyped and defeat the guarantee that every
    failure is a :class:`~memco.errors.MemcoError`.

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
    return [tag.to_proto() for tag in _validate.trim(_validate.check_tags(tags), cap)]


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


def list_domains_request() -> _pb.ListDomainsRequest:
    """Build a ``ListDomains`` request.

    Returns:
        The request message. It carries no fields: the call is the answer to
        "which domain?", so it takes no domain of its own.
    """
    return _pb.ListDomainsRequest()


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
        materialised = _validate.trim(materialised, caps.limits.max_sources)
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
    *, session_id: str, feedback: Sequence[FeedbackRating], known: Known | None = None
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
    _validate.check_feedback(feedback)
    caps = known or Known()
    if caps.limits:
        _validate.check_count(len(feedback), "feedback", caps.limits.max_feedback_entries)
        for rating in feedback:
            _check_handle(rating.idx, "feedback idx", known)
    return _built(
        lambda: _pb.ShareFeedbackRequest(
            session_id=session_id, feedback=[rating.to_proto() for rating in feedback]
        )
    )


def import_memories_requests(
    memories: Sequence[ImportedMemory],
    *,
    domain: str | None,
    session_id: str | None,
    known: Known | None = None,
) -> list[tuple[int, _pb.ImportMemoriesRequest]]:
    """Validate a batch of memories and split it into the calls that carry it.

    ``max_import_memories`` bounds one *call*, not one batch, and it refuses
    rather than trims — so a batch above it is divided into groups of that size
    and sent as several calls. A caller hands over whatever it has without
    having to learn the number or chunk against it. Nothing is dropped: the
    groups partition the batch in order. An unreported cap means the service
    rules, as everywhere else, so the batch goes out whole.

    The three per-entry caps refuse too, and are checked against what the caller
    actually supplied. The per-domain tag cap still trims, and is applied after
    the refusing one so that the refusal stays reachable.

    Args:
        memories: The memories to contribute.
        domain: The memory domain, if the import is not scoped by a session.
        session_id: The session this knowledge was contributed during, if any.
        known: What the service has reported about its own limits, if anything.

    Returns:
        One ``(offset, request)`` per call to make, in order. The offset is the
        position the group's first memory held in the whole batch, which is what
        turns each response's own numbering back into the caller's.

    Raises:
        MemcoInvalidRequestError: If the batch or any entry is invalid, or if
            neither a domain nor a session was given.
    """
    _validate.check_import_memories(memories)
    _validate.check_scope(domain=domain, session_id=session_id)
    caps = known or Known()
    if caps.limits:
        limits = caps.limits
        for index, memory in enumerate(memories):
            where = f"memories[{index}]"
            _validate.check_count(
                len(memory.queries), f"{where} queries", limits.max_import_queries_per_memory
            )
            _validate.check_count(
                len(memory.insights), f"{where} insights", limits.max_import_insights_per_memory
            )
            _validate.check_count(
                len(memory.tags or ()), f"{where} tags", limits.max_import_tags_per_memory
            )
            for at, insight in enumerate(memory.insights):
                _check_text_together(
                    insight.title,
                    insight.content,
                    caps,
                    f"{where} insights[{at}] title and content",
                )
    cap = caps.max_tags(domain)

    def one_call(taken: Sequence[ImportedMemory]) -> _pb.ImportMemoriesRequest:
        """Build the request carrying one group.

        Every message is constructed inside the guard, entries included:
        building them into a list first would put the construction most likely
        to hold unencodable text — a whole group of it — outside the only thing
        that turns protobuf's refusal into a typed error.

        Args:
            taken: The group's memories.

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
    group = (caps.limits.max_import_memories if caps.limits else 0) or len(memories)
    return [
        (offset, one_call(memories[offset : offset + group]))
        for offset in range(0, len(memories), group)
    ]


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
