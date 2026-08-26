"""Client-side argument checks performed before any request is sent.

Only **structural** rules live here: a blank value, a missing required
combination of arguments, an empty batch. Those follow from the shape of the
contract and cannot go out of date.

Numeric limits — how long a query may be, how many ratings fit in one call —
are deliberately **not** checked here. The service owns those, and a value
compiled into the SDK goes stale the moment the service changes one: an older
client would keep rejecting requests the service would now accept, locally,
with no way for the caller to tell why. Until the limits are delivered by the
service itself they are its business alone, and an over-long field is reported
by the service rather than guessed at here.

Every failure raises :class:`~memco.errors.MemcoInvalidRequestError` with an
``INVALID_ARGUMENT`` status, so a caller handles a local rejection and a
server-side one the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import grpc

from memco.errors import MemcoInvalidRequestError
from memco.types import FeedbackRating, Tag

NEW_MEMORY = "new"
"""Sentinel opening a new memory instead of enriching an existing one.

Case-sensitive: ``"New"`` is treated as an ordinary handle, not the sentinel.
This is a value the contract defines, not a limit the service tunes.
"""

__all__ = [
    "NEW_MEMORY",
    "check_content",
    "check_domain",
    "check_feedback",
    "check_idx",
    "check_memory_idx",
    "check_operation_id",
    "check_query",
    "check_scope",
    "check_session_id",
    "check_sources",
    "check_tags",
    "check_title",
    "reject",
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
        domain: The slug, as returned by ``describe_domains``.

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


def check_tags(tags: Iterable[Tag] | None) -> list[Tag]:
    """Validate the tags on a search or a write, and materialise them.

    A blank tag is worth catching: a tag type that *filters* rather than boosts
    narrows a search to nothing, so an empty one returns no memories at all and
    is indistinguishable from "nothing is known about this".

    Args:
        tags: The tags supplied by the caller, if any.

    Returns:
        The tags as a list, empty when none were given.

    Raises:
        MemcoInvalidRequestError: If a tag's type or value is blank.
    """
    materialised = list(tags or ())
    for tag in materialised:
        _check_present(tag.type, "tag type")
        _check_present(tag.value, "tag value")
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


def check_feedback(feedback: Sequence[FeedbackRating]) -> None:
    """Validate a batch of ratings.

    Args:
        feedback: The ratings to record.

    Raises:
        MemcoInvalidRequestError: If the batch is empty or holds a rating whose
            handle is blank.
    """
    if not feedback:
        raise reject("feedback must contain at least one rating")
    for rating in feedback:
        check_idx(rating.idx, "feedback idx")
