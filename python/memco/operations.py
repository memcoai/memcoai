"""The memory operations, reached as ``client.memory``.

Held apart from the client, which owns the channel, the credential and the
health gate; this module owns only the calls.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Any

from . import _convert, _deprecation, _limits, _requests
from .types import (
    DataSource,
    DomainList,
    FeedbackRating,
    FeedbackResult,
    ImportedMemory,
    ImportResult,
    Instructions,
    Memory,
    RevertResult,
    SearchResult,
    Session,
    Tag,
    WriteResult,
)

if TYPE_CHECKING:  # pragma: no cover - agent imports this module, so this cannot be eager
    from .agent import AsyncToolset, Toolset

__all__ = [
    "AsyncMemoryOperations",
    "AsyncSessionOpener",
    "AsyncSessionScope",
    "MemoryOperations",
    "SessionScope",
]


class MemoryOperations:
    """The memory operations, on a synchronous client.

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

    def with_session(self, domain: str, *, timeout: float | None = None) -> SessionScope:
        """Open a session and apply it to every call made through the result.

        The same as :meth:`start_session`, except that the id is bound rather
        than handed back to be threaded through each call by hand. Prefer this
        wherever the session outlives a line or two: a call that silently drops
        the id is still a valid call, it just stops being part of the series.

        Args:
            domain: Slug of the domain, as returned by :meth:`describe_domains`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The operations, with the opened session applied.

        Raises:
            MemcoInvalidRequestError: If the domain is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> with client.memory.with_session("coding") as session:
            ...     result = session.search("how does X work")
        """
        return SessionScope(self, self.start_session(domain, timeout=timeout))

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

    def import_memories(
        self,
        memories: Sequence[ImportedMemory],
        *,
        domain: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> ImportResult:
        """Contribute many memories in one call.

        Each becomes an ordinary memory, evaluated on the way in exactly as
        :meth:`create_memory` is, so this is a way to write a lot at once rather
        than a way to write differently. Every memory is judged on its own, so a
        refused entry does not stop the others.

        A batch of any length is accepted. The service caps how many memories
        one call may carry, so a longer batch is divided into groups of that
        size and sent as several calls; the outcomes come back numbered against
        the batch as submitted, not against the group each was sent in.

        A batch mints no operation id, so there is no handle that undoes an
        import. Resending one is safe: an import is written under an identity
        derived from its own content, so a memory that already landed comes back
        as :attr:`~memco.types.ImportStatus.DUPLICATE` rather than being written
        twice. That is also what to do if a call partway through a long batch
        fails — resend the whole thing, and what already landed costs nothing.

        Args:
            memories: The memories to contribute. Each needs at least one query
                and at least one insight.
            domain: Slug of the domain to import into. Required unless
                ``session_id`` is given. A batch cannot span domains.
            session_id: The session this knowledge was contributed during.
                Omitting it files the memories under no session, which is what a
                standalone upload wants.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            One outcome per memory submitted, in the order they were sent.

        Raises:
            MemcoInvalidRequestError: If the batch is empty, if an entry is
                invalid or exceeds a reported per-entry cap, or if neither a
                domain nor a session was given.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = client.memory.import_memories(
            ...     [
            ...         ImportedMemory(
            ...             queries=["how do I authenticate against the memory API"],
            ...             insights=[ImportedInsight(title="Bearer is case-sensitive",
            ...                                       content="Lowercase 'bearer' is rejected.")],
            ...             tags=[Tag(type="language", value="python")],
            ...         )
            ...     ],
            ...     domain="coding",
            ... )
            >>> [(o.index, o.status.name) for o in result.results]
            [(0, 'QUEUED')]
        """
        batches = _requests.import_memories_requests(
            memories, domain=domain, session_id=session_id, known=self._known
        )
        return _convert.to_import_result(
            [
                (offset, self._call(self._stub.ImportMemories, request, timeout))
                for offset, request in batches
            ]
        )


class AsyncMemoryOperations:
    """The memory operations, on an asyncio client.

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

    def with_session(self, domain: str, *, timeout: float | None = None) -> AsyncSessionOpener:
        """Open a session and apply it to every call made through the result.

        The same as :meth:`start_session`, except that the id is bound rather
        than handed back to be threaded through each call by hand. Prefer this
        wherever the session outlives a line or two: a call that silently drops
        the id is still a valid call, it just stops being part of the series.

        The result is both awaitable and an async context manager, so ``await``
        and ``async with`` both reach the scope. Nothing is sent until one of
        them opens the session, unlike the synchronous form, which opens it as
        it is called.

        Args:
            domain: Slug of the domain, as returned by :meth:`describe_domains`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            A handle that opens the session and yields the scope, on ``await``
            or on entering it.

        Raises:
            MemcoInvalidRequestError: If the domain is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> async with client.memory.with_session("coding") as session:
            ...     result = await session.search("how does X work")
        """
        return AsyncSessionOpener(self, domain, timeout)

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

    async def import_memories(
        self,
        memories: Sequence[ImportedMemory],
        *,
        domain: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> ImportResult:
        """Contribute many memories in one call.

        Each becomes an ordinary memory, evaluated on the way in exactly as
        :meth:`create_memory` is, so this is a way to write a lot at once rather
        than a way to write differently. Every memory is judged on its own, so a
        refused entry does not stop the others.

        A batch of any length is accepted. The service caps how many memories
        one call may carry, so a longer batch is divided into groups of that
        size and sent as several calls; the outcomes come back numbered against
        the batch as submitted, not against the group each was sent in.

        A batch mints no operation id, so there is no handle that undoes an
        import. Resending one is safe: an import is written under an identity
        derived from its own content, so a memory that already landed comes back
        as :attr:`~memco.types.ImportStatus.DUPLICATE` rather than being written
        twice. That is also what to do if a call partway through a long batch
        fails — resend the whole thing, and what already landed costs nothing.

        Args:
            memories: The memories to contribute. Each needs at least one query
                and at least one insight.
            domain: Slug of the domain to import into. Required unless
                ``session_id`` is given. A batch cannot span domains.
            session_id: The session this knowledge was contributed during.
                Omitting it files the memories under no session, which is what a
                standalone upload wants.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            One outcome per memory submitted, in the order they were sent.

        Raises:
            MemcoInvalidRequestError: If the batch is empty, if an entry is
                invalid or exceeds a reported per-entry cap, or if neither a
                domain nor a session was given.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = await client.memory.import_memories(
            ...     [
            ...         ImportedMemory(
            ...             queries=["how do I authenticate against the memory API"],
            ...             insights=[ImportedInsight(title="Bearer is case-sensitive",
            ...                                       content="Lowercase 'bearer' is rejected.")],
            ...             tags=[Tag(type="language", value="python")],
            ...         )
            ...     ],
            ...     domain="coding",
            ... )
            >>> [(o.index, o.status.name) for o in result.results]
            [(0, 'QUEUED')]
        """
        batches = _requests.import_memories_requests(
            memories, domain=domain, session_id=session_id, known=self._known
        )
        return _convert.to_import_result(
            [
                (offset, await self._call(self._stub.ImportMemories, request, timeout))
                for offset, request in batches
            ]
        )


class SessionScope:
    """The memory operations with one session already applied.

    Returned by :meth:`MemoryOperations.with_session`; not constructed directly.
    Every call made through it is recorded under the session it holds, so the id
    cannot be dropped, mistyped, or invented further down a call stack. The
    session supplies the domain too, which is why no operation here takes one.

    Usable as a context manager, which releases nothing: the contract has no
    call that ends a session, and a session id stays usable for as long as it is
    named. The block bounds the scope for the reader rather than managing a
    resource.

    Attributes:
        session_id: The session every call through this scope names.
        instructions: What the service said when the session was opened.

    Example:
        >>> with client.memory.with_session("coding") as session:
        ...     result = session.search("how does X work")
        ...     session.share_feedback(feedback=[
        ...         FeedbackRating(idx=result.memories[0].idx,
        ...                        relevant=True, correct=True),
        ...     ])
    """

    def __init__(self, operations: MemoryOperations, session: Session) -> None:
        """Bind a session to a namespace.

        Args:
            operations: The namespace to forward every call to.
            session: The session that was opened.
        """
        self._operations = operations
        self._session = session

    @property
    def session_id(self) -> str:
        """The session every call through this scope names."""
        return self._session.session_id

    @property
    def instructions(self) -> Instructions:
        """What the service said when the session was opened."""
        return self._session.instructions

    def __enter__(self) -> SessionScope:
        """Enter a context manager.

        Returns:
            This scope.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Leave the scope, releasing nothing.

        There is no call that ends a session, so there is nothing to undo here.
        """

    def search(
        self,
        query: str,
        *,
        tags: Sequence[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search for memories answering a task-based query.

        Recorded under this scope's session, which is what relates the searches
        made for one task and what lets the results be rated afterwards with
        :meth:`share_feedback`.

        Args:
            query: A question, statement or task description, in plain language.
                Keyword and semantic search are both applied, so one
                concept per query works best. The service caps its length.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain; :meth:`MemoryOperations.describe_domains`
                describes them.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The memories selected, with guidance on adding to and rating them.

        Raises:
            MemcoInvalidRequestError: If the query is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = session.search(
            ...     "how should a client authenticate against the memory API",
            ...     tags=[Tag(type="language", value="python", version="3.12")],
            ... )
        """
        return self._operations.search(
            query, session_id=self.session_id, tags=tags, timeout=timeout
        )

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
            >>> memory = session.get_memory("memory-9fg6vc-1")
            >>> [insight.title for insight in memory.insights]
        """
        return self._operations.get_memory(idx, timeout=timeout)

    def create_memory(
        self,
        *,
        query: str,
        title: str,
        content: str,
        tags: Sequence[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge, attributed to this scope's session.

        The write is accepted asynchronously, so the result addresses the
        operation rather than the memory it will become. Use the returned
        operation id with :meth:`revert_memory` to undo it.

        Args:
            query: What someone would search to find this memory later.
            title: Short title.
            content: The knowledge itself. Be specific:
                exact names, values and procedures are what make an entry worth
                reading.
            tags: Tags describing the subject and context.
            source: Who produced the content. Defaults to
                :attr:`~memco.types.DataSource.AGENT`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The accepted write, whose ``operation_id`` is ``None`` if the write
            was accepted but cannot be undone.

        Raises:
            MemcoInvalidRequestError: If a field is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = session.create_memory(
            ...     query="how do I authenticate against the memory API",
            ...     title="Memory API takes a Bearer token",
            ...     content="The prefix is case-sensitive: lowercase 'bearer' is rejected.",
            ... )
        """
        return self._operations.create_memory(
            query=query,
            title=title,
            content=content,
            session_id=self.session_id,
            tags=tags,
            source=source,
            timeout=timeout,
        )

    def enrich_memory(
        self,
        *,
        memory_idx: str,
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
            >>> session.enrich_memory(
            ...     memory_idx="memory-9fg6vc-2",
            ...     title="A connection check does not prove the credential works",
            ...     content="That check carries no credential, so a bad token surfaces later.",
            ... )
        """
        return self._operations.enrich_memory(
            memory_idx=memory_idx,
            session_id=self.session_id,
            title=title,
            content=content,
            tags=tags,
            sources=sources,
            source=source,
            timeout=timeout,
        )

    def share_feedback(
        self,
        *,
        feedback: Sequence[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the results of a search made through this scope.

        Ratings are what move the reliability signal on an insight, and are the
        only way the service learns whether a result actually answered the
        query.

        Args:
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
            >>> session.share_feedback(feedback=[
            ...     FeedbackRating(idx="memory-9fg6vc-2-insight-1",
            ...                    relevant=True, correct=True),
            ... ])
        """
        return self._operations.share_feedback(
            session_id=self.session_id, feedback=feedback, timeout=timeout
        )

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
            >>> result = session.revert_memory("create-8fj2k1")
            >>> if result.outcome is RevertOutcome.EXPIRED:
            ...     print("outside the revert window")
        """
        return self._operations.revert_memory(operation_id, timeout=timeout)

    def import_memories(
        self, memories: Sequence[ImportedMemory], *, timeout: float | None = None
    ) -> ImportResult:
        """Contribute many memories in one call, attributed to this scope's session.

        Each becomes an ordinary memory, evaluated on the way in exactly as
        :meth:`create_memory` is, so this is a way to write a lot at once rather
        than a way to write differently. Every memory is judged on its own, so a
        refused entry does not stop the others.

        A batch of any length is accepted. The service caps how many memories
        one call may carry, so a longer batch is divided into groups of that
        size and sent as several calls; the outcomes come back numbered against
        the batch as submitted, not against the group each was sent in.

        A batch mints no operation id, so there is no handle that undoes an
        import. Resending one is safe: an import is written under an identity
        derived from its own content, so a memory that already landed comes back
        as :attr:`~memco.types.ImportStatus.DUPLICATE` rather than being written
        twice. That is also what to do if a call partway through a long batch
        fails — resend the whole thing, and what already landed costs nothing.

        Args:
            memories: The memories to contribute. Each needs at least one query
                and at least one insight.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            One outcome per memory submitted, in the order they were sent.

        Raises:
            MemcoInvalidRequestError: If the batch is empty, or if an entry is
                invalid or exceeds a reported per-entry cap.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = session.import_memories(
            ...     [
            ...         ImportedMemory(
            ...             queries=["how do I authenticate against the memory API"],
            ...             insights=[ImportedInsight(title="Bearer is case-sensitive",
            ...                                       content="Lowercase 'bearer' is rejected.")],
            ...         )
            ...     ]
            ... )
            >>> [(o.index, o.status.name) for o in result.results]
            [(0, 'QUEUED')]
        """
        return self._operations.import_memories(
            memories, session_id=self.session_id, timeout=timeout
        )

    def tools(self) -> Toolset:
        """This session's operations, described and rendered for an LLM.

        Each tool carries what the operation is for, a JSON Schema for its
        arguments, and a call that renders the result as text. Every one is
        bound to this session, so nothing a model sends can change which session
        a call is recorded under.

        The toolset hands itself to a framework — ``to_langchain()``,
        ``to_anthropic()``, ``to_openai()`` — or runs what a model named with
        ``call()``. See :mod:`memco.agent`.

        Returns:
            One tool per operation, as a :class:`~memco.agent.Toolset`.

        Raises:
            MemcoConfigError: If this package's docstrings are unavailable,
                which is what running Python with ``-OO`` does. Every
                description is derived from them.

        Example:
            >>> with client.memory.with_session("coding") as session:
            ...     create_agent(model, tools=session.tools().to_langchain())
        """
        # Deferred: memco.agent reads this module to derive the descriptions and
        # schemas, so importing it at module scope would be a cycle. The tools
        # belong to the session, which is why the accessor is here rather than
        # leaving every caller to find the builder.
        from .agent import _tools  # noqa: PLC0415

        return _tools(self)


class AsyncSessionScope:
    """The memory operations with one session already applied, on an asyncio client.

    Returned by :meth:`AsyncMemoryOperations.with_session`; not constructed
    directly. Mirrors :class:`SessionScope` method for method. Every call made
    through it is recorded under the session it holds, so the id cannot be
    dropped, mistyped, or invented further down a call stack. The session
    supplies the domain too, which is why no operation here takes one.

    Usable as an async context manager, which releases nothing: the contract has
    no call that ends a session, and a session id stays usable for as long as it
    is named. The block bounds the scope for the reader rather than managing a
    resource.

    Attributes:
        session_id: The session every call through this scope names.
        instructions: What the service said when the session was opened.

    Example:
        >>> async with client.memory.with_session("coding") as session:
        ...     result = await session.search("how does X work")
    """

    def __init__(self, operations: AsyncMemoryOperations, session: Session) -> None:
        """Bind a session to a namespace.

        Args:
            operations: The namespace to forward every call to.
            session: The session that was opened.
        """
        self._operations = operations
        self._session = session

    @property
    def session_id(self) -> str:
        """The session every call through this scope names."""
        return self._session.session_id

    @property
    def instructions(self) -> Instructions:
        """What the service said when the session was opened."""
        return self._session.instructions

    async def __aenter__(self) -> AsyncSessionScope:
        """Enter an async context manager.

        Returns:
            This scope.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Leave the scope, releasing nothing.

        There is no call that ends a session, so there is nothing to undo here.
        """

    async def search(
        self,
        query: str,
        *,
        tags: Sequence[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search for memories answering a task-based query.

        Recorded under this scope's session, which is what relates the searches
        made for one task and what lets the results be rated afterwards with
        :meth:`share_feedback`.

        Args:
            query: A question, statement or task description, in plain language.
                Keyword and semantic search are both applied, so one
                concept per query works best. The service caps its length.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain;
                :meth:`AsyncMemoryOperations.describe_domains` describes them.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The memories selected, with guidance on adding to and rating them.

        Raises:
            MemcoInvalidRequestError: If the query is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = await session.search(
            ...     "how should a client authenticate against the memory API",
            ...     tags=[Tag(type="language", value="python", version="3.12")],
            ... )
        """
        return await self._operations.search(
            query, session_id=self.session_id, tags=tags, timeout=timeout
        )

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
            >>> memory = await session.get_memory("memory-9fg6vc-1")
            >>> [insight.title for insight in memory.insights]
        """
        return await self._operations.get_memory(idx, timeout=timeout)

    async def create_memory(
        self,
        *,
        query: str,
        title: str,
        content: str,
        tags: Sequence[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge, attributed to this scope's session.

        The write is accepted asynchronously, so the result addresses the
        operation rather than the memory it will become. Use the returned
        operation id with :meth:`revert_memory` to undo it.

        Args:
            query: What someone would search to find this memory later.
            title: Short title.
            content: The knowledge itself. Be specific:
                exact names, values and procedures are what make an entry worth
                reading.
            tags: Tags describing the subject and context.
            source: Who produced the content. Defaults to
                :attr:`~memco.types.DataSource.AGENT`.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The accepted write, whose ``operation_id`` is ``None`` if the write
            was accepted but cannot be undone.

        Raises:
            MemcoInvalidRequestError: If a field is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = await session.create_memory(
            ...     query="how do I authenticate against the memory API",
            ...     title="Memory API takes a Bearer token",
            ...     content="The prefix is case-sensitive: lowercase 'bearer' is rejected.",
            ... )
        """
        return await self._operations.create_memory(
            query=query,
            title=title,
            content=content,
            session_id=self.session_id,
            tags=tags,
            source=source,
            timeout=timeout,
        )

    async def enrich_memory(
        self,
        *,
        memory_idx: str,
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
            >>> await session.enrich_memory(
            ...     memory_idx="memory-9fg6vc-2",
            ...     title="A connection check does not prove the credential works",
            ...     content="That check carries no credential, so a bad token surfaces later.",
            ... )
        """
        return await self._operations.enrich_memory(
            memory_idx=memory_idx,
            session_id=self.session_id,
            title=title,
            content=content,
            tags=tags,
            sources=sources,
            source=source,
            timeout=timeout,
        )

    async def share_feedback(
        self,
        *,
        feedback: Sequence[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the results of a search made through this scope.

        Ratings are what move the reliability signal on an insight, and are the
        only way the service learns whether a result actually answered the
        query.

        Args:
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
            >>> await session.share_feedback(feedback=[
            ...     FeedbackRating(idx="memory-9fg6vc-2-insight-1",
            ...                    relevant=True, correct=True),
            ... ])
        """
        return await self._operations.share_feedback(
            session_id=self.session_id, feedback=feedback, timeout=timeout
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
            >>> result = await session.revert_memory("create-8fj2k1")
            >>> if result.outcome is RevertOutcome.EXPIRED:
            ...     print("outside the revert window")
        """
        return await self._operations.revert_memory(operation_id, timeout=timeout)

    async def import_memories(
        self, memories: Sequence[ImportedMemory], *, timeout: float | None = None
    ) -> ImportResult:
        """Contribute many memories in one call, attributed to this scope's session.

        Each becomes an ordinary memory, evaluated on the way in exactly as
        :meth:`create_memory` is, so this is a way to write a lot at once rather
        than a way to write differently. Every memory is judged on its own, so a
        refused entry does not stop the others.

        A batch of any length is accepted. The service caps how many memories
        one call may carry, so a longer batch is divided into groups of that
        size and sent as several calls; the outcomes come back numbered against
        the batch as submitted, not against the group each was sent in.

        A batch mints no operation id, so there is no handle that undoes an
        import. Resending one is safe: an import is written under an identity
        derived from its own content, so a memory that already landed comes back
        as :attr:`~memco.types.ImportStatus.DUPLICATE` rather than being written
        twice. That is also what to do if a call partway through a long batch
        fails — resend the whole thing, and what already landed costs nothing.

        Args:
            memories: The memories to contribute. Each needs at least one query
                and at least one insight.
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            One outcome per memory submitted, in the order they were sent.

        Raises:
            MemcoInvalidRequestError: If the batch is empty, or if an entry is
                invalid or exceeds a reported per-entry cap.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> result = await session.import_memories(
            ...     [
            ...         ImportedMemory(
            ...             queries=["how do I authenticate against the memory API"],
            ...             insights=[ImportedInsight(title="Bearer is case-sensitive",
            ...                                       content="Lowercase 'bearer' is rejected.")],
            ...         )
            ...     ]
            ... )
            >>> [(o.index, o.status.name) for o in result.results]
            [(0, 'QUEUED')]
        """
        return await self._operations.import_memories(
            memories, session_id=self.session_id, timeout=timeout
        )

    def tools(self) -> AsyncToolset:
        """This session's operations, described and rendered for an LLM.

        Each tool carries what the operation is for, a JSON Schema for its
        arguments, and an awaitable call that renders the result as text. Every
        one is bound to this session, so nothing a model sends can change which
        session a call is recorded under.

        The toolset hands itself to a framework — ``to_langchain()``,
        ``to_anthropic()``, ``to_openai()`` — or runs what a model named with
        ``call()``. See :mod:`memco.agent`.

        Returns:
            One tool per operation, as a :class:`~memco.agent.AsyncToolset`.

        Raises:
            MemcoConfigError: If this package's docstrings are unavailable,
                which is what running Python with ``-OO`` does. Every
                description is derived from them.

        Example:
            >>> async with client.memory.with_session("coding") as session:
            ...     create_agent(model, tools=session.tools().to_langchain())
        """
        # Deferred for the same reason as the synchronous scope's.
        from .agent import _async_tools  # noqa: PLC0415

        return _async_tools(self)


class AsyncSessionOpener:
    """A session that has not been opened yet, awaitable or entered.

    ``with_session`` cannot both do I/O and be usable as ``async with`` without
    this: an ``async def`` would force ``async with await ...`` at every call
    site, which is the one shape the synchronous surface has no counterpart for.
    Opening is deferred to the ``await`` or the ``__aenter__``, so a scope that
    is built and dropped costs no session.
    """

    def __init__(
        self, operations: AsyncMemoryOperations, domain: str, timeout: float | None
    ) -> None:
        """Record what to open, without opening it.

        Args:
            operations: The namespace the scope will forward to.
            domain: Slug of the domain to open a session in.
            timeout: Per-call deadline for the open, or ``None``.
        """
        self._operations = operations
        self._domain = domain
        self._timeout = timeout
        self._scope: AsyncSessionScope | None = None

    def __await__(self) -> Generator[Any, None, AsyncSessionScope]:
        """Open the session.

        Returns:
            A generator yielding the opened scope, as ``await`` requires.
        """
        return self._open().__await__()

    async def __aenter__(self) -> AsyncSessionScope:
        """Open the session on entering an async context manager.

        Returns:
            The opened scope.
        """
        return await self._open()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Leave the scope, releasing nothing.

        There is no call that ends a session, so there is nothing to undo here.
        """

    async def _open(self) -> AsyncSessionScope:
        """Open the session and bind it, once.

        Awaiting or entering the same handle twice returns the session it
        already opened. Opening a second one would be a silent write — the same
        thing the retry policy refuses to do to ``StartSession`` — and the
        caller would have no way to reach the first.

        Returns:
            The scope, with the opened session applied to every call.
        """
        if self._scope is None:
            session = await self._operations.start_session(self._domain, timeout=self._timeout)
            self._scope = AsyncSessionScope(self._operations, session)
        return self._scope
