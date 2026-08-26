"""The memory operations, reached as ``client.memory``.

Held apart from the client, which owns the channel, the credential and the
health gate; this module owns only the calls.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from . import _convert, _deprecation, _limits, _requests
from .types import (
    DataSource,
    DomainList,
    FeedbackRating,
    FeedbackResult,
    Memory,
    RevertResult,
    SearchResult,
    Session,
    Tag,
    WriteResult,
)

__all__ = ["AsyncMemoryOperations", "MemoryOperations"]


class MemoryOperations:
    """The eight memory operations, on a synchronous client.

    Reached as :attr:`~memco.Memco.memory`; not constructed directly.

    Example:
        >>> with Memco() as client:
        ...     session = client.memory.start_session("coding")
        ...     result = client.memory.search("how does X work",
        ...                                   session_id=session.session_id)
    """

    def __init__(self, stub: Any, call: Callable[..., Any]) -> None:
        """Bind the namespace to its client.

        Args:
            stub: The generated service stub.
            call: The owning client's invoker, which applies the deadline and
                translates failures into typed exceptions.
        """
        self._stub = stub
        self._call = call
        self._known = _limits.Known()
        """What the service has reported about its own limits, once it has."""

    def describe_domains(self, *, timeout: float | None = None) -> DomainList:
        """List the memory domains this credential may name.

        Takes no domain itself: it is the answer to "which domain?". Call it
        before a first search or write to learn the available domains and the
        tag vocabulary each one uses.

        Args:
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The available domains and accompanying guidance.

        Raises:
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> for domain in client.memory.describe_domains().domains:
            ...     print(domain.slug, "-", domain.summary)
        """
        response = self._call(
            self._stub.DescribeDomains, _requests.describe_domains_request(), timeout
        )
        described = _convert.to_domain_list(response)
        self._known.update(described.limits, described.domains)
        _deprecation.warn_once(described.deprecation_message, described.sunset_date)
        return described

    def start_session(self, domain: str, *, timeout: float | None = None) -> Session:
        """Open a session in one memory domain.

        Every search made under a session is recorded as one series, which is
        what relates the searches made for a single task. Reuse the returned
        handle for subsequent searches, writes and ratings.

        Args:
            domain: Slug of the domain, as returned by :meth:`describe_domains`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The open session.

        Raises:
            MemcoInvalidRequestError: If the domain is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> session = client.memory.start_session("coding")
            >>> session.session_id
            'session-z2ye39'
        """
        return _convert.to_session(
            self._call(self._stub.StartSession, _requests.start_session_request(domain), timeout)
        )

    def search(
        self,
        query: str,
        *,
        domain: str | None = None,
        session_id: str | None = None,
        tags: Sequence[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search for memories answering a task-based query.

        Pass either a domain or a session: a session supplies the domain of the
        session it names. Searching under a session also records the search so
        its results can be rated afterwards with :meth:`share_feedback`.

        Args:
            query: A question, statement or task description, in plain language.
                Keyword and semantic search are both applied, so one
                concept per query works best. The service caps its length.
            domain: Slug of the domain to search. Required unless ``session_id``
                is given.
            session_id: An open session to record this search under.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain; :meth:`describe_domains` describes
                them.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The memories selected, with guidance on adding to and rating them.

        Raises:
            MemcoInvalidRequestError: If the query is blank, or
                if neither a domain nor a session was given.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = client.memory.search(
            ...     "how should a client authenticate against the memory API",
            ...     domain="coding",
            ...     tags=[Tag(type="language", value="python", version="3.12")],
            ... )
            >>> for memory in result.memories:
            ...     for insight in memory.insights:
            ...         print(insight.title, insight.updated)
        """
        request = _requests.search_request(
            query, domain=domain, session_id=session_id, tags=tags, known=self._known
        )
        return _convert.to_search_result(self._call(self._stub.Search, request, timeout))

    def get_memory(self, idx: str, *, timeout: float | None = None) -> Memory:
        """Fetch the memory behind a handle a search returned.

        Use this for a result a search returned as a reference rather than in
        full, which happens when an earlier search in the same session already
        delivered it.

        Args:
            idx: A handle copied exactly from a search result. An insight's
                handle returns the memory holding it.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The memory and its insights.

        Raises:
            MemcoInvalidRequestError: If the handle is blank.
            MemcoNotFoundError: If the handle resolves to nothing visible.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> memory = client.memory.get_memory("memory-9fg6vc-1")
            >>> [insight.title for insight in memory.insights]
        """
        response = self._call(
            self._stub.GetMemory, _requests.get_memory_request(idx, self._known), timeout
        )
        return _convert.to_memory(_requests.require_memory(response, idx))

    def create_memory(
        self,
        *,
        query: str,
        title: str,
        content: str,
        domain: str | None = None,
        session_id: str | None = None,
        tags: Sequence[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge.

        The write is accepted asynchronously, so the result addresses the
        operation rather than the memory it will become. Use the returned
        operation id with :meth:`revert_memory` to undo it.

        Args:
            query: What someone would search to find this memory later.
            title: Short title.
            content: The knowledge itself. Be specific:
                exact names, values and procedures are what make an entry worth
                reading.
            domain: Slug of the domain to write to. Required unless
                ``session_id`` is given.
            session_id: The session this was learned during.
            tags: Tags describing the subject and context.
            source: Who produced the content. Defaults to
                :attr:`~memco.types.DataSource.AGENT`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The accepted write, whose ``operation_id`` is ``None`` if the write
            was accepted but cannot be undone.

        Raises:
            MemcoInvalidRequestError: If a field is blank, or
                if neither a domain nor a session was given.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = client.memory.create_memory(
            ...     query="how do I authenticate against the memory API",
            ...     title="Memory API takes a Bearer token",
            ...     content="The prefix is case-sensitive: lowercase 'bearer' is rejected.",
            ...     session_id=session.session_id,
            ...     tags=[Tag(type="language", value="python")],
            ... )
            >>> result.operation_id
            'create-8fj2k1'
        """
        request = _requests.create_memory_request(
            query=query,
            title=title,
            content=content,
            domain=domain,
            session_id=session_id,
            tags=tags,
            source=source,
            known=self._known,
        )
        return _convert.to_write_result(self._call(self._stub.CreateMemory, request, timeout))

    def enrich_memory(
        self,
        *,
        memory_idx: str,
        session_id: str,
        title: str,
        content: str,
        tags: Sequence[Tag] | None = None,
        sources: Sequence[str] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Add to a memory a search returned, or open a new one.

        Use this when a search almost answered the question: the addition lands
        alongside the existing insights rather than as a separate memory.

        Args:
            memory_idx: The memory to enrich, copied from a search result, or
                the literal ``"new"`` to open one. The sentinel is
                case-sensitive.
            session_id: The session the memory was returned under. Required: it
                supplies the domain.
            title: Short title for the addition.
            content: The knowledge being added. Say
                only what is not already there.
            tags: Tags describing the addition.
            sources: Handles of the memories this addition draws on.
            source: Who produced the content. Defaults to
                :attr:`~memco.types.DataSource.AGENT`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The accepted write.

        Raises:
            MemcoInvalidRequestError: If a field is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.memory.enrich_memory(
            ...     memory_idx="memory-9fg6vc-2",
            ...     session_id=session.session_id,
            ...     title="A connection check does not prove the credential works",
            ...     content="That check carries no credential, so a bad token surfaces later.",
            ... )
        """
        request = _requests.enrich_memory_request(
            memory_idx=memory_idx,
            session_id=session_id,
            title=title,
            content=content,
            tags=tags,
            sources=sources,
            source=source,
            known=self._known,
        )
        return _convert.to_write_result(self._call(self._stub.EnrichMemory, request, timeout))

    def share_feedback(
        self,
        *,
        session_id: str,
        feedback: Sequence[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the results of one search.

        Ratings are what move the reliability signal on an insight, and are the
        only way the service learns whether a result actually answered the
        query.

        Args:
            session_id: The session whose search is being rated.
            feedback: One rating per result. Each handle must be copied exactly
                from a search result; a memory's own handle rates every insight
                under it.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The ratings that were recorded, each with any advice it earned.

        Raises:
            MemcoInvalidRequestError: If the batch is empty or holds an invalid
                rating.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.memory.share_feedback(
            ...     session_id=session.session_id,
            ...     feedback=[
            ...         FeedbackRating(idx="memory-9fg6vc-2-insight-1",
            ...                        relevant=True, correct=True),
            ...     ],
            ... )
        """
        request = _requests.share_feedback_request(
            session_id=session_id, feedback=feedback, known=self._known
        )
        return _convert.to_feedback_result(self._call(self._stub.ShareFeedback, request, timeout))

    def revert_memory(self, operation_id: str, *, timeout: float | None = None) -> RevertResult:
        """Undo one of this caller's own writes.

        Every outcome is a successful call. An operation that was not found, has
        expired, or is under moderation is reported through
        :attr:`~memco.types.RevertResult.outcome` rather than raised,
        because each describes caller-visible state rather than a failure.

        Args:
            operation_id: The operation id a create or enrich returned, as
                carried by :attr:`~memco.types.WriteResult.operation_id`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            What the revert actually removed.

        Raises:
            MemcoInvalidRequestError: If the operation id is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = client.memory.revert_memory("create-8fj2k1")
            >>> if result.outcome is RevertOutcome.EXPIRED:
            ...     print("outside the revert window")
        """
        request = _requests.revert_memory_request(operation_id, self._known)
        return _convert.to_revert_result(self._call(self._stub.RevertMemory, request, timeout))


class AsyncMemoryOperations:
    """The eight memory operations, on an asyncio client.

    Reached as :attr:`~memco.AsyncMemco.memory`; not constructed
    directly. Mirrors :class:`MemoryOperations` method for method.

    Example:
        >>> async with AsyncMemco() as client:
        ...     session = await client.memory.start_session("coding")
        ...     result = await client.memory.search("how does X work",
        ...                                         session_id=session.session_id)
    """

    def __init__(self, stub: Any, call: Callable[..., Any]) -> None:
        """Bind the namespace to its client.

        Args:
            stub: The generated service stub.
            call: The owning client's invoker, which applies the deadline and
                translates failures into typed exceptions.
        """
        self._stub = stub
        self._call = call
        self._known = _limits.Known()
        """What the service has reported about its own limits, once it has."""

    async def describe_domains(self, *, timeout: float | None = None) -> DomainList:
        """List the memory domains this credential may name.

        Takes no domain itself: it is the answer to "which domain?". Call it
        before a first search or write to learn the available domains and the
        tag vocabulary each one uses.

        Args:
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The available domains and accompanying guidance.

        Raises:
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> for domain in (await client.memory.describe_domains()).domains:
            ...     print(domain.slug, "-", domain.summary)
        """
        response = await self._call(
            self._stub.DescribeDomains, _requests.describe_domains_request(), timeout
        )
        described = _convert.to_domain_list(response)
        self._known.update(described.limits, described.domains)
        _deprecation.warn_once(described.deprecation_message, described.sunset_date)
        return described

    async def start_session(self, domain: str, *, timeout: float | None = None) -> Session:
        """Open a session in one memory domain.

        Every search made under a session is recorded as one series, which is
        what relates the searches made for a single task. Reuse the returned
        handle for subsequent searches, writes and ratings.

        Args:
            domain: Slug of the domain, as returned by :meth:`describe_domains`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The open session.

        Raises:
            MemcoInvalidRequestError: If the domain is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> session = await client.memory.start_session("coding")
            >>> session.session_id
            'session-z2ye39'
        """
        response = await self._call(
            self._stub.StartSession, _requests.start_session_request(domain), timeout
        )
        return _convert.to_session(response)

    async def search(
        self,
        query: str,
        *,
        domain: str | None = None,
        session_id: str | None = None,
        tags: Sequence[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search for memories answering a task-based query.

        Pass either a domain or a session: a session supplies the domain of the
        session it names. Searching under a session also records the search so
        its results can be rated afterwards with :meth:`share_feedback`.

        Args:
            query: A question, statement or task description, in plain language.
                Keyword and semantic search are both applied, so one
                concept per query works best. The service caps its length.
            domain: Slug of the domain to search. Required unless ``session_id``
                is given.
            session_id: An open session to record this search under.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain; :meth:`describe_domains` describes
                them.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The memories selected, with guidance on adding to and rating them.

        Raises:
            MemcoInvalidRequestError: If the query is blank, or
                if neither a domain nor a session was given.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = await client.memory.search(
            ...     "how should a client authenticate against the memory API",
            ...     domain="coding",
            ...     tags=[Tag(type="language", value="python", version="3.12")],
            ... )
            >>> for memory in result.memories:
            ...     for insight in memory.insights:
            ...         print(insight.title, insight.updated)
        """
        request = _requests.search_request(
            query, domain=domain, session_id=session_id, tags=tags, known=self._known
        )
        return _convert.to_search_result(await self._call(self._stub.Search, request, timeout))

    async def get_memory(self, idx: str, *, timeout: float | None = None) -> Memory:
        """Fetch the memory behind a handle a search returned.

        Use this for a result a search returned as a reference rather than in
        full, which happens when an earlier search in the same session already
        delivered it.

        Args:
            idx: A handle copied exactly from a search result. An insight's
                handle returns the memory holding it.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The memory and its insights.

        Raises:
            MemcoInvalidRequestError: If the handle is blank.
            MemcoNotFoundError: If the handle resolves to nothing visible.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> memory = await client.memory.get_memory("memory-9fg6vc-1")
            >>> [insight.title for insight in memory.insights]
        """
        response = await self._call(
            self._stub.GetMemory, _requests.get_memory_request(idx, self._known), timeout
        )
        return _convert.to_memory(_requests.require_memory(response, idx))

    async def create_memory(
        self,
        *,
        query: str,
        title: str,
        content: str,
        domain: str | None = None,
        session_id: str | None = None,
        tags: Sequence[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge.

        The write is accepted asynchronously, so the result addresses the
        operation rather than the memory it will become. Use the returned
        operation id with :meth:`revert_memory` to undo it.

        Args:
            query: What someone would search to find this memory later.
            title: Short title.
            content: The knowledge itself. Be specific:
                exact names, values and procedures are what make an entry worth
                reading.
            domain: Slug of the domain to write to. Required unless
                ``session_id`` is given.
            session_id: The session this was learned during.
            tags: Tags describing the subject and context.
            source: Who produced the content. Defaults to
                :attr:`~memco.types.DataSource.AGENT`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The accepted write, whose ``operation_id`` is ``None`` if the write
            was accepted but cannot be undone.

        Raises:
            MemcoInvalidRequestError: If a field is blank, or
                if neither a domain nor a session was given.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = await client.memory.create_memory(
            ...     query="how do I authenticate against the memory API",
            ...     title="Memory API takes a Bearer token",
            ...     content="The prefix is case-sensitive: lowercase 'bearer' is rejected.",
            ...     session_id=session.session_id,
            ...     tags=[Tag(type="language", value="python")],
            ... )
            >>> result.operation_id
            'create-8fj2k1'
        """
        request = _requests.create_memory_request(
            query=query,
            title=title,
            content=content,
            domain=domain,
            session_id=session_id,
            tags=tags,
            source=source,
            known=self._known,
        )
        return _convert.to_write_result(await self._call(self._stub.CreateMemory, request, timeout))

    async def enrich_memory(
        self,
        *,
        memory_idx: str,
        session_id: str,
        title: str,
        content: str,
        tags: Sequence[Tag] | None = None,
        sources: Sequence[str] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Add to a memory a search returned, or open a new one.

        Use this when a search almost answered the question: the addition lands
        alongside the existing insights rather than as a separate memory.

        Args:
            memory_idx: The memory to enrich, copied from a search result, or
                the literal ``"new"`` to open one. The sentinel is
                case-sensitive.
            session_id: The session the memory was returned under. Required: it
                supplies the domain.
            title: Short title for the addition.
            content: The knowledge being added. Say
                only what is not already there.
            sources: Handles of the memories this addition draws on.
            tags: Tags describing the addition.
            source: Who produced the content. Defaults to
                :attr:`~memco.types.DataSource.AGENT`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The accepted write.

        Raises:
            MemcoInvalidRequestError: If a field is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.memory.enrich_memory(
            ...     memory_idx="memory-9fg6vc-2",
            ...     session_id=session.session_id,
            ...     title="A connection check does not prove the credential works",
            ...     content="That check carries no credential, so a bad token surfaces later.",
            ... )
        """
        request = _requests.enrich_memory_request(
            memory_idx=memory_idx,
            session_id=session_id,
            title=title,
            content=content,
            tags=tags,
            sources=sources,
            source=source,
            known=self._known,
        )
        return _convert.to_write_result(await self._call(self._stub.EnrichMemory, request, timeout))

    async def share_feedback(
        self,
        *,
        session_id: str,
        feedback: Sequence[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the results of one search.

        Ratings are what move the reliability signal on an insight, and are the
        only way the service learns whether a result actually answered the
        query.

        Args:
            session_id: The session whose search is being rated.
            feedback: One rating per result. Each handle must be copied exactly
                from a search result; a memory's own handle rates every insight
                under it.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The ratings that were recorded, each with any advice it earned.

        Raises:
            MemcoInvalidRequestError: If the batch is empty or holds an invalid
                rating.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.memory.share_feedback(
            ...     session_id=session.session_id,
            ...     feedback=[
            ...         FeedbackRating(idx="memory-9fg6vc-2-insight-1",
            ...                        relevant=True, correct=True),
            ...     ],
            ... )
        """
        request = _requests.share_feedback_request(
            session_id=session_id, feedback=feedback, known=self._known
        )
        return _convert.to_feedback_result(
            await self._call(self._stub.ShareFeedback, request, timeout)
        )

    async def revert_memory(
        self, operation_id: str, *, timeout: float | None = None
    ) -> RevertResult:
        """Undo one of this caller's own writes.

        Every outcome is a successful call. An operation that was not found, has
        expired, or is under moderation is reported through
        :attr:`~memco.types.RevertResult.outcome` rather than raised,
        because each describes caller-visible state rather than a failure.

        Args:
            operation_id: The operation id a create or enrich returned, as
                carried by :attr:`~memco.types.WriteResult.operation_id`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            What the revert actually removed.

        Raises:
            MemcoInvalidRequestError: If the operation id is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = await client.memory.revert_memory("create-8fj2k1")
            >>> if result.outcome is RevertOutcome.EXPIRED:
            ...     print("outside the revert window")
        """
        request = _requests.revert_memory_request(operation_id, self._known)
        return _convert.to_revert_result(
            await self._call(self._stub.RevertMemory, request, timeout)
        )
