"""Client-side argument checks performed before any request is sent.

Only **structural** rules live here: a blank value, a missing required
combination of arguments, an empty batch. Those follow from the shape of the
contract and cannot go out of date.

Numeric limits are never *compiled in* here. The service owns those, and a value
baked into the SDK goes stale the moment the service changes one: an older
client would keep rejecting requests the service had started accepting, locally,
with no way for the caller to tell why.

They are applied all the same, but only once the service has reported them on a
``ListDomains`` response — see :mod:`memcoai._limits`. The helpers below that
take a cap treat zero as "nothing was reported" and check nothing. Two of the
caps **trim** rather than refuse, because the service trims: raising would
reject a call it would have accepted.

Every failure raises :class:`~memcoai.errors.MemcoInvalidRequestError` with an
``INVALID_ARGUMENT`` status, so a caller handles a local rejection and a
server-side one the same way.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TypeVar

import grpc

from memcoai.errors import MemcoInvalidRequestError
from memcoai.types import FeedbackRating, ImportedMemory, Tag

_log = logging.getLogger(__name__)

_T = TypeVar("_T")

NEW_MEMORY = "new"
"""Sentinel opening a new memory instead of enriching an existing one.

Case-sensitive: ``"New"`` is treated as an ordinary handle, not the sentinel.
This is a value the contract defines, not a limit the service tunes.
"""

__all__ = [
    "NEW_MEMORY",
    "check_content",
    "check_count",
    "check_domain",
    "check_feedback",
    "check_idx",
    "check_import_memories",
    "check_memory_idx",
    "check_operation_id",
    "check_query",
    "check_scope",
    "check_session_id",
    "check_sources",
    "check_tags",
    "check_title",
    "check_within",
    "reject",
    "trim",
]


def reject(message: str) -> MemcoInvalidRequestError:
    """Build the rejection raised by every check in this module.

    Args:
        message: Explanation naming the offending field.

    Returns:
        The exception to raise, carrying an ``INVALID_ARGUMENT`` status so it is
        indistinguishable from a server-side rejection.
    """
    return MemcoInvalidRequestError(grpc.StatusCode.INVALID_ARGUMENT, message)


def _check_present(value: str | None, field: str) -> None:
    """Require a non-blank string.

    ``None`` is rejected explicitly rather than allowed to raise an
    :class:`AttributeError`: an un-revertible write reports its operation id as
    ``None``, and the documented flow feeds that straight back in.

    Args:
        value: The value supplied by the caller.
        field: Field name, used verbatim in the error message.

    Raises:
        MemcoInvalidRequestError: If the value is ``None`` or blank.
    """
    if value is None or not value.strip():
        raise reject(f"{field} must not be empty")


def check_query(query: str) -> None:
    """Validate a search or memory query.

    Args:
        query: The query text.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    _check_present(query, "query")


def check_title(title: str) -> None:
    """Validate a memory or insight title.

    Args:
        title: The title text.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    _check_present(title, "title")


def check_content(content: str) -> None:
    """Validate a memory or insight body.

    Args:
        content: The content text.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    _check_present(content, "content")


def check_idx(idx: str, field: str = "idx") -> None:
    """Validate a handle returned by a previous response.

    Args:
        idx: The handle, copied exactly from an earlier result.
        field: Name to report in the error. Pass the caller's own argument name
            so the message points at the argument the user actually wrote.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    _check_present(idx, field)


def check_session_id(session_id: str) -> None:
    """Validate a session handle where one is required.

    Args:
        session_id: The session handle.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    _check_present(session_id, "session_id")


def check_domain(domain: str) -> None:
    """Validate a domain slug.

    Args:
        domain: The slug, as returned by ``list_domains``.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    _check_present(domain, "domain")


def check_operation_id(operation_id: str) -> None:
    """Validate the operation id addressing a previous write.

    Args:
        operation_id: The id a create or enrich returned.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    _check_present(operation_id, "operation_id")


def check_memory_idx(memory_idx: str) -> None:
    """Validate the target of an enrichment.

    Args:
        memory_idx: The memory to enrich, or :data:`NEW_MEMORY` to open one. The
            sentinel is case-sensitive.

    Raises:
        MemcoInvalidRequestError: If it is blank.
    """
    if memory_idx == NEW_MEMORY:
        return
    _check_present(memory_idx, "memory_idx")


def check_scope(*, domain: str | None, session_id: str | None) -> None:
    """Require a domain, a session, or both.

    A session supplies the domain of the session it names, so either alone is
    sufficient. Passing both is allowed and left for the service to resolve.

    Args:
        domain: The memory domain, if one was given.
        session_id: The session handle, if one was given.

    Raises:
        MemcoInvalidRequestError: If neither was given, or if a value that was
            given is blank.
    """
    if not (domain or session_id):
        raise reject("pass a domain or a session_id: a request needs one of them to name a domain")
    if domain is not None:
        check_domain(domain)
    if session_id is not None:
        check_session_id(session_id)


def check_tags(tags: Iterable[Tag] | None, field: str = "tag") -> list[Tag]:
    """Validate the tags on a search or a write, and materialise them.

    A blank tag is worth catching: a tag type that *filters* rather than boosts
    narrows a search to nothing, so an empty one returns no memories at all and
    is indistinguishable from "nothing is known about this".

    Args:
        tags: The tags supplied by the caller, if any.
        field: Field name, used verbatim in the error message. A call carrying
            more than one set of tags names which one these are.

    Returns:
        The tags as a list, empty when none were given.

    Raises:
        MemcoInvalidRequestError: If a tag's type or value is blank.
    """
    materialised = list(tags or ())
    for tag in materialised:
        _check_present(tag.type, f"{field} type")
        _check_present(tag.value, f"{field} value")
    return materialised


def check_sources(sources: Iterable[str] | None) -> list[str]:
    """Validate the source handles cited by an enrichment, and materialise them.

    Materialising here rather than at the call site is what makes the bare-string
    guard below reachable, and it consumes a one-shot iterable exactly once.

    Args:
        sources: Handles of the memories this addition draws on, if any.

    Returns:
        The handles as a list, empty when none were given.

    Raises:
        MemcoInvalidRequestError: If a bare string was passed instead of a
            sequence, or if any handle is blank.
    """
    if isinstance(sources, str):
        # A str satisfies Iterable[str], so neither the annotation nor the type
        # checker catches this; iterating it would cite one memory per
        # character and attribute the write to handles that do not exist.
        raise reject("sources must be a sequence of handles, not a single string")
    materialised = list(sources or ())
    for source in materialised:
        check_idx(source, "sources entry")
    return materialised


def check_within(value: str, field: str, cap: int) -> None:
    """Reject a value longer than a cap the service reported.

    Args:
        value: The value supplied by the caller.
        field: Field name, used verbatim in the error message.
        cap: The reported cap. Zero means none was reported, so nothing is
            checked.

    Raises:
        MemcoInvalidRequestError: If the value exceeds the cap.
    """
    if cap and len(value) > cap:
        raise reject(f"{field} is {len(value)} characters, which exceeds the limit of {cap}")


def check_count(count: int, field: str, cap: int) -> None:
    """Reject a batch larger than a cap the service reported.

    Args:
        count: How many entries the caller supplied.
        field: Field name, used verbatim in the error message.
        cap: The reported cap. Zero means none was reported.

    Raises:
        MemcoInvalidRequestError: If the count exceeds the cap.
    """
    if cap and count > cap:
        raise reject(f"{field} has {count} entries, which exceeds the limit of {cap}")


def trim(values: list[_T], cap: int, field: str) -> list[_T]:
    """Trim a list to a cap the service applies by trimming.

    The service keeps the first ``cap`` entries and drops the rest, so a client
    that raised here would reject a call the service would have accepted.

    Args:
        values: The entries the caller supplied.
        cap: The reported cap. Zero means none was reported.
        field: Field name, used verbatim in the log record.

    Returns:
        The entries, trimmed if a cap applies.
    """
    if not (cap and len(values) > cap):
        return values
    # Dropping the caller's data is invisible in the result, so this record is
    # the only way to find out that it happened.
    _log.debug("%s trimmed from %d to %d by the service's cap", field, len(values), cap)
    return values[:cap]


def check_feedback(feedback: Iterable[FeedbackRating]) -> list[FeedbackRating]:
    """Validate a batch of ratings, and return it as a list.

    Materialised here and returned, as :func:`check_tags` and
    :func:`check_sources` are, because the caller walks the result a second time
    to build the request. Validating the caller's own object and then walking
    that again would send nothing at all when they passed a generator, and the
    call would report success.

    Args:
        feedback: The ratings to record.

    Returns:
        The ratings, as a list that can be walked again.

    Raises:
        MemcoInvalidRequestError: If the batch is empty or holds a rating whose
            handle is blank.
    """
    materialised = list(feedback or ())
    if not materialised:
        raise reject("feedback must contain at least one rating")
    for rating in materialised:
        check_idx(rating.idx, "feedback idx")
    return materialised


def check_import_memories(
    memories: Iterable[ImportedMemory], offset: int = 0
) -> list[ImportedMemory]:
    """Validate a group of memories to import, and return it materialised.

    Every collection the caller gave is copied into a list here, and it is that
    copy the request is built from. Validating the caller's own object and then
    walking it again to build would send nothing at all when they passed a
    generator, a ``map`` or a ``filter`` — and the call would report success,
    with an empty ``results`` the only sign they ever got.

    Every message names the position of the entry it is about. A batch gives the
    caller no handle to address one memory by, so the index is the only way to
    say which of five hundred entries is the problem.

    Args:
        memories: The memories to contribute.
        offset: Position the first of them holds in the caller's whole batch, so
            a group taken from the middle of one still names absolute indices.

    Returns:
        The memories, each with its queries, insights and tags materialised.

    Raises:
        MemcoInvalidRequestError: If the batch is empty, if an entry carries no
            query or no insight, if a bare string was passed as an entry's
            queries, or if any field of an entry is blank.
    """
    taken = list(memories)
    if not taken:
        raise reject("memories must contain at least one memory")
    materialised: list[ImportedMemory] = []
    for index, memory in enumerate(taken):
        where = f"memories[{offset + index}]"
        if isinstance(memory.queries, str):
            # A str satisfies Iterable[str], so neither the annotation nor the
            # type checker catches this; iterating it would file one query per
            # character and make the memory findable by nothing.
            raise reject(f"{where} queries must be a sequence of queries, not a single string")
        queries = list(memory.queries)
        insights = list(memory.insights)
        if not queries:
            raise reject(f"{where} must contain at least one query")
        if not insights:
            raise reject(f"{where} must contain at least one insight")
        for at, query in enumerate(queries):
            _check_present(query, f"{where} queries[{at}]")
        for at, insight in enumerate(insights):
            _check_present(insight.title, f"{where} insights[{at}] title")
            _check_present(insight.content, f"{where} insights[{at}] content")
        # Validated here rather than left to the request builder so the message
        # names the entry, and the checked list is what is carried forward.
        tags = check_tags(memory.tags, f"{where} tag")
        materialised.append(ImportedMemory(queries=queries, insights=insights, tags=tags))
    return materialised
