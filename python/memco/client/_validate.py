"""Client-side argument checks performed before any request is sent.

These limits mirror the ones the service enforces. Checking them here turns a
wasted round trip into an immediate error naming the offending field, and keeps
a malformed request from consuming the caller's rate-limit budget.

They are a snapshot of the contract as of this release, so a service that later
raises a limit needs a new SDK release before callers can use the extra room.
Structural checks — blank values, missing argument combinations, batch sizes —
carry no such risk.

Every failure raises :class:`~memco.client.errors.MemcoInvalidRequestError` with
an ``INVALID_ARGUMENT`` status, so a caller handles a local rejection and a
server-side one the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import grpc

from .errors import MemcoInvalidRequestError
from .types import FeedbackRating

MAX_QUERY = 1000
"""Maximum length of a search or memory query, in characters."""

MAX_TEXT = 5000
"""Maximum length of a title, a content body, or a feedback comment."""

MAX_IDX = 64
"""Maximum length of a handle such as an idx or a memory handle."""

MAX_SOURCES = 20
"""Maximum number of source handles on an enrichment."""

MAX_FEEDBACK = 10
"""Maximum number of ratings in a single feedback call."""

NEW_MEMORY = "new"
"""Sentinel opening a new memory instead of enriching an existing one.

Case-sensitive: ``"New"`` is treated as an ordinary handle, not the sentinel.
"""

__all__ = [
    "MAX_FEEDBACK",
    "MAX_IDX",
    "MAX_QUERY",
    "MAX_SOURCES",
    "MAX_TEXT",
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
    "check_title",
]


def _reject(message: str) -> MemcoInvalidRequestError:
    """Build the rejection raised by every check in this module.

    Args:
        message: Explanation naming the offending field.

    Returns:
        The exception to raise, carrying an ``INVALID_ARGUMENT`` status so it is
        indistinguishable from a server-side rejection.
    """
    return MemcoInvalidRequestError(grpc.StatusCode.INVALID_ARGUMENT, message)


def _check_text(value: str, field: str, limit: int) -> None:
    """Require a non-blank string within a length limit.

    Args:
        value: The value supplied by the caller.
        field: Field name, used verbatim in the error message.
        limit: Maximum permitted length in characters.

    Raises:
        MemcoInvalidRequestError: If the value is blank or too long.
    """
    if not value.strip():
        raise _reject(f"{field} must not be empty")
    if len(value) > limit:
        raise _reject(f"{field} is {len(value)} characters, which exceeds the limit of {limit}")


def check_query(query: str) -> None:
    """Validate a search or memory query.

    Args:
        query: The query text.

    Raises:
        MemcoInvalidRequestError: If it is blank or longer than :data:`MAX_QUERY`.
    """
    _check_text(query, "query", MAX_QUERY)


def check_title(title: str) -> None:
    """Validate a memory or insight title.

    Args:
        title: The title text.

    Raises:
        MemcoInvalidRequestError: If it is blank or longer than :data:`MAX_TEXT`.
    """
    _check_text(title, "title", MAX_TEXT)


def check_content(content: str) -> None:
    """Validate a memory or insight body.

    Args:
        content: The content text.

    Raises:
        MemcoInvalidRequestError: If it is blank or longer than :data:`MAX_TEXT`.
    """
    _check_text(content, "content", MAX_TEXT)


def check_idx(idx: str, field: str = "idx") -> None:
    """Validate a handle returned by a previous response.

    Args:
        idx: The handle, copied exactly from an earlier result.
        field: Name to report in the error. Pass the caller's own argument name
            so the message points at the argument the user actually wrote,
            rather than at this function's generic one.

    Raises:
        MemcoInvalidRequestError: If it is blank or longer than :data:`MAX_IDX`.
    """
    _check_text(idx, field, MAX_IDX)


def check_session_id(session_id: str) -> None:
    """Validate a session handle where one is required.

    Args:
        session_id: The session handle.

    Raises:
        MemcoInvalidRequestError: If it is blank or longer than :data:`MAX_IDX`.
    """
    _check_text(session_id, "session_id", MAX_IDX)


def check_domain(domain: str) -> None:
    """Validate a domain slug.

    Args:
        domain: The slug, as returned by ``list_domains``.

    Raises:
        MemcoInvalidRequestError: If it is blank or longer than :data:`MAX_IDX`.
    """
    check_idx(domain, "domain")


def check_operation_id(operation_id: str) -> None:
    """Validate the operation id addressing a previous write.

    Args:
        operation_id: The id a create or enrich returned.

    Raises:
        MemcoInvalidRequestError: If it is blank or longer than :data:`MAX_IDX`.
    """
    check_idx(operation_id, "operation_id")


def check_memory_idx(memory_idx: str) -> None:
    """Validate the target of an enrichment.

    Args:
        memory_idx: The memory to enrich, or :data:`NEW_MEMORY` to open one. The
            sentinel is case-sensitive.

    Raises:
        MemcoInvalidRequestError: If it is blank or, when not the sentinel,
            longer than :data:`MAX_IDX`.
    """
    if memory_idx == NEW_MEMORY:
        return
    _check_text(memory_idx, "memory_idx", MAX_IDX)


def check_scope(*, domain: str | None, session_id: str | None) -> None:
    """Require a domain, a session, or both.

    A session supplies the domain of the session it names, so either alone is
    sufficient. Passing both is allowed and left for the service to resolve.

    Args:
        domain: The memory domain, if one was given.
        session_id: The session handle, if one was given.

    Raises:
        MemcoInvalidRequestError: If neither was given, or if a value that was
            given is blank or too long.
    """
    if not (domain or session_id):
        raise _reject("pass a domain or a session_id: a request needs one of them to name a domain")
    if domain is not None:
        check_domain(domain)
    if session_id is not None:
        check_session_id(session_id)


def check_sources(sources: Iterable[str] | None) -> None:
    """Validate the source handles cited by an enrichment.

    Args:
        sources: Handles of the memories this addition draws on, if any.

    Raises:
        MemcoInvalidRequestError: If there are more than :data:`MAX_SOURCES`, or
            if any handle is blank or too long.
    """
    if sources is None:
        return
    items = list(sources)
    if len(items) > MAX_SOURCES:
        raise _reject(f"sources has {len(items)} entries, which exceeds the limit of {MAX_SOURCES}")
    for source in items:
        check_idx(source, "sources entry")


def check_feedback(feedback: Sequence[FeedbackRating]) -> None:
    """Validate a batch of ratings.

    Args:
        feedback: The ratings to record.

    Raises:
        MemcoInvalidRequestError: If the batch is empty, holds more than
            :data:`MAX_FEEDBACK` ratings, or contains a rating whose handle or
            comment is invalid.
    """
    if not feedback:
        raise _reject("feedback must contain at least one rating")
    if len(feedback) > MAX_FEEDBACK:
        raise _reject(
            f"feedback has {len(feedback)} entries, which exceeds the limit of {MAX_FEEDBACK}"
        )
    for rating in feedback:
        check_idx(rating.idx, "feedback idx")
        if rating.comment is not None and len(rating.comment) > MAX_TEXT:
            raise _reject(
                f"feedback comment is {len(rating.comment)} characters, "
                f"which exceeds the limit of {MAX_TEXT}"
            )
