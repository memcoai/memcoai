"""The memory operations, reached as ``client.memory``.

Held apart from the client, which owns the channel, the credential and the
health gate; this module owns only the calls.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
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

    def list_domains(self, *, timeout: float | None = None) -> DomainList:
        """List the memory domains available to you and describe each one: what it holds, when to
        search it, what belongs in it and what does not, and the tag vocabulary and format it uses.

        Call when: before your first search or write of a task, and whenever you are unsure which
        domain a question or a finding belongs to. The slug you choose is what
        :meth:`start_session`, :meth:`search` and :meth:`create_memory` take as their domain
        argument.

        Args:
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The available domains and accompanying guidance.

        Raises:
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> for domain in client.memory.list_domains().domains:
            ...     print(domain.slug, "-", domain.summary)
        """
        response = self._call(self._stub.ListDomains, _requests.list_domains_request(), timeout)
        described = _convert.to_domain_list(response)
        self._known.update(described.limits, described.domains)
        _deprecation.warn_once(described.deprecation_message, described.sunset_date)
        return described

    def start_session(self, domain: str, *, timeout: float | None = None) -> Session:
        """Start a session and get its id. A session groups the searches you make while working on
        one task, so they are recorded as the series they are rather than as unrelated one-offs.

        Call when: at the start of work that will involve multiple related searches, or when you
        want a stable session id to reuse across :meth:`search`, :meth:`share_feedback`, and
        :meth:`enrich_memory`. Pass the id as session_id to every search you make for it — and to
        :meth:`share_feedback` and :meth:`enrich_memory`. A session stays usable for as long as you
        keep naming it.

        Args:
            domain: (Required) The memory domain to operate in. Call :meth:`list_domains` for the
                domains available to you.
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
            domain: Slug of the domain, as returned by :meth:`list_domains`.
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
        tags: Iterable[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search Memco Shared Memory for existing knowledge before working a problem out from
        scratch. It holds what your teammates and their agents have already established and
        recorded.

        Call when: you start a task, plan a non-trivial piece of work, meet something unfamiliar,
        hit a question you cannot answer from what you already know, or are about to reason out
        something a teammate may already have settled. Search first, then work.

        Pass either a domain or a session_id — a search naming neither is refused. Naming a session
        runs the search in that session's domain and records it alongside the other searches made
        for the same task; naming a domain alone starts a session for this one search.

        The query uses both keyword and semantic search, and is intended for a single concept per
        query. If you need varied information, make multiple queries.

        Supply tags to narrow the results; :meth:`list_domains` lists the tag types the chosen
        domain uses and the format they take.

        Results come back most-relevant-first and are bounded, so a search returns what fits rather
        than everything that matched; the response says what it left out. Memories are written by
        your teammates and their agents. Within one session a result already returned is not
        repeated — it comes back as a reference to the idx that carried it, which :meth:`get_memory`
        turns back into content.

        Args:
            query: (Required) A task-based query from the user such as a question, statement, or
                task description. To ensure readability, use markdown formatting. At most 1000
                characters.
            domain: The memory domain to search in. Required unless you pass session_id, which
                supplies the domain of the session it names. Call :meth:`list_domains` for the
                domains available to you.
            session_id: The session to record this search under, as returned by
                :meth:`start_session` or a previous search. The search runs in that session's memory
                domain, so the domain argument is not needed and is ignored. Omit this to start a
                new session, in which case a domain is required.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain; :meth:`list_domains` describes
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
        """Fetch one memory a search returned, by its idx, and get it back in full.

        Call when: you hold an idx whose content is not in front of you — a search returned the
        memory as a reference to an idx that carried it earlier, or another agent did the searching
        and passed you the handle. An insight's idx returns the memory holding it.

        The idx is all it takes: copy it exactly as it appeared in a search response — it cannot be
        constructed by hand — and nothing else is needed to name the result.

        When a result shows a ref instead of content, ask by the value in its own idx, never the
        value in its ref. A result rendered as <memory idx="memory-THIS-1" ref="memory-EARLIER-1">
        is fetched with "memory-THIS-1": the ref says where the content was delivered, not what to
        ask for. Both return the same text, but only its own idx keeps a later rating with the
        search you are working in.

        Args:
            idx: (Required) The idx of the result to fetch, copied exactly as it appeared in a
                search response. An insight's idx returns the memory holding it.
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
        tags: Iterable[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge to Memco Shared Memory, where your teammates and their agents will
        find it.

        Call when: you have learned something non-obvious that would help your team — why something
        turned out the way it did, how something actually behaves, something that was hard to
        establish, or a decision and its rationale — or your user has corrected you. Search first:
        when a related memory already exists, :meth:`enrich_memory` extends it instead of leaving a
        near-duplicate beside it.

        Pass either a domain or a session_id — a call naming neither is refused. Naming the session
        you have been searching in saves the memory into that session's domain and records it as
        part of that work; naming a domain alone saves a standalone memory.

        Each memory needs a query (what someone would search to find this), a title, and content
        describing what you learned. :meth:`list_domains` says what belongs in the chosen domain and
        which tags to use.

        Args:
            query: (Required) A query describing what someone would search to find this memory, such
                as a question or problem statement. Use markdown formatting for readability. At most
                1000 characters.
            title: (Required) A short title describing what this memory is about. Title and content
                together must be at most 5000 characters.
            content: (Required) The knowledge to save. Should be a concise, non-trivial finding that
                others can learn from. Supports markdown formatting. Title and content together must
                be at most 5000 characters; split a longer finding across several memories.
            domain: The memory domain to save into. Required unless you pass session_id, which
                supplies the domain of the session it names. Call :meth:`list_domains` for the
                domains available to you.
            session_id: The session this memory was learned during, as returned by
                :meth:`start_session` or a previous search. It records the memory as part of that
                series of work, and supplies the memory domain, so the domain argument is not needed
                and is ignored. Omit it to save a standalone memory.
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
        tags: Iterable[Tag] | None = None,
        sources: Iterable[str] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Add information to an existing memory in Memco Shared Memory, so your finding lands
        beside the one it belongs to rather than in a memory that competes with it.

        Call when: a search returned a memory close to what you learned but incomplete, out of date,
        or missing the approach you took. Use :meth:`create_memory` instead when nothing returned
        covers the subject at all.

        Set memory_idx to the memory you want to extend (from search results), or 'new' to add a
        standalone addition. Keep an addition concise and say only what is not already there. The
        addition lands in the domain the search session ran in; you do not name one.

        Args:
            memory_idx: (Required) The memory_idx of the memory you are enriching. If you are adding
                to a new memory, set memory_idx to 'new'.
            session_id: (Required) The session id you are enriching a memory in. The ID was included
                in the response from :meth:`search`.
            title: (Required) A short title describing what you learned. Title and content together
                must be at most 5000 characters.
            content: (Required) The knowledge you want to add. Use markdown formatting for
                readability. Title and content together must be at most 5000 characters; split a
                longer finding across several enrichments.
            tags: Tags describing the addition.
            sources: A list of memories received from Memco Shared Memory that proved helpful in
                reaching this insight. Up to 20 sources can be included.
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
        feedback: Iterable[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the relevance and correctness of search results. Only you can tell whether a result
        answered the query, and these ratings shape which results are shown next.

        Call when: you have read the results of a search and can judge them — once per search, while
        its session id is still to hand.

        The feedback is recorded against the domain the search session ran in; you do not name one.

        Args:
            session_id: (Required) The session you are providing feedback for. The ID was included
                in the response from :meth:`search`.
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
        """Undo a memory you just wrote, using the operation id that :meth:`create_memory` or
        :meth:`enrich_memory` returned.

        Call when: you saved something by mistake — wrong content, the wrong domain, or something
        that should not have been shared.

        Your entry is always removed. The memory it belongs to is removed with it only when your
        entry was the last one in it — so reverting a :meth:`create_memory`, or an
        :meth:`enrich_memory` you sent with memory_idx 'new', removes that memory too, while
        reverting an addition to a memory that holds other entries leaves the memory in place. You
        can only revert your own writes, and only within 2 days.

        Args:
            operation_id: (Required) The operation id returned by the :meth:`create_memory` or
                :meth:`enrich_memory` call you want to undo, for example 'create-hpc08-1'.
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
        memories: Iterable[ImportedMemory],
        *,
        domain: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> ImportResult:
        """Fill a new or nearly empty workspace with the knowledge a team already holds, in one
        call, so memory starts out useful instead of empty.

        Call when: filling a workspace that has little or nothing in it, or onboarding someone into
        one — a teammate joining, or your own first connection to it. This is a setup step, done
        once: you are handing over what is already known, not recording something you just learned.
        Use :meth:`create_memory` for a single finding from this session.

        Pass either a domain or a session_id — a call naming neither is refused. Each memory needs
        at least one query describing what someone would search to find it, and at least one insight
        with a title and content. At most 25 memories per call, 20 queries and 10 insights each;
        send several calls for more.

        Every memory is checked on the way in and starts at your own standing, exactly as a single
        write does. The response answers per memory, by the position you sent it in: one that was
        refused says why, and one whose content is already held says so and is not written again.

        Args:
            memories: (Required) The memories to contribute. At least one, at most 25 per call; send
                several calls for more.
            domain: The memory domain to import into. Required unless you pass session_id, which
                supplies the domain of the session it names. Call :meth:`list_domains` for the
                domains available to you.
            session_id: The session these memories were contributed during, as returned by
                :meth:`start_session` or a previous search. It records them as part of that series
                of work, and supplies the memory domain, so the domain argument is not needed and is
                ignored.
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

    async def list_domains(self, *, timeout: float | None = None) -> DomainList:
        """List the memory domains available to you and describe each one: what it holds, when to
        search it, what belongs in it and what does not, and the tag vocabulary and format it uses.

        Call when: before your first search or write of a task, and whenever you are unsure which
        domain a question or a finding belongs to. The slug you choose is what
        :meth:`start_session`, :meth:`search` and :meth:`create_memory` take as their domain
        argument.

        Args:
            timeout: Per-call deadline in seconds. Defaults to the client's.

        Returns:
            The available domains and accompanying guidance.

        Raises:
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> for domain in (await client.memory.list_domains()).domains:
            ...     print(domain.slug, "-", domain.summary)
        """
        response = await self._call(
            self._stub.ListDomains, _requests.list_domains_request(), timeout
        )
        described = _convert.to_domain_list(response)
        self._known.update(described.limits, described.domains)
        _deprecation.warn_once(described.deprecation_message, described.sunset_date)
        return described

    async def start_session(self, domain: str, *, timeout: float | None = None) -> Session:
        """Start a session and get its id. A session groups the searches you make while working on
        one task, so they are recorded as the series they are rather than as unrelated one-offs.

        Call when: at the start of work that will involve multiple related searches, or when you
        want a stable session id to reuse across :meth:`search`, :meth:`share_feedback`, and
        :meth:`enrich_memory`. Pass the id as session_id to every search you make for it — and to
        :meth:`share_feedback` and :meth:`enrich_memory`. A session stays usable for as long as you
        keep naming it.

        Args:
            domain: (Required) The memory domain to operate in. Call :meth:`list_domains` for the
                domains available to you.
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
            domain: Slug of the domain, as returned by :meth:`list_domains`.
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
        tags: Iterable[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search Memco Shared Memory for existing knowledge before working a problem out from
        scratch. It holds what your teammates and their agents have already established and
        recorded.

        Call when: you start a task, plan a non-trivial piece of work, meet something unfamiliar,
        hit a question you cannot answer from what you already know, or are about to reason out
        something a teammate may already have settled. Search first, then work.

        Pass either a domain or a session_id — a search naming neither is refused. Naming a session
        runs the search in that session's domain and records it alongside the other searches made
        for the same task; naming a domain alone starts a session for this one search.

        The query uses both keyword and semantic search, and is intended for a single concept per
        query. If you need varied information, make multiple queries.

        Supply tags to narrow the results; :meth:`list_domains` lists the tag types the chosen
        domain uses and the format they take.

        Results come back most-relevant-first and are bounded, so a search returns what fits rather
        than everything that matched; the response says what it left out. Memories are written by
        your teammates and their agents. Within one session a result already returned is not
        repeated — it comes back as a reference to the idx that carried it, which :meth:`get_memory`
        turns back into content.

        Args:
            query: (Required) A task-based query from the user such as a question, statement, or
                task description. To ensure readability, use markdown formatting. At most 1000
                characters.
            domain: The memory domain to search in. Required unless you pass session_id, which
                supplies the domain of the session it names. Call :meth:`list_domains` for the
                domains available to you.
            session_id: The session to record this search under, as returned by
                :meth:`start_session` or a previous search. The search runs in that session's memory
                domain, so the domain argument is not needed and is ignored. Omit this to start a
                new session, in which case a domain is required.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain; :meth:`list_domains` describes
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
        """Fetch one memory a search returned, by its idx, and get it back in full.

        Call when: you hold an idx whose content is not in front of you — a search returned the
        memory as a reference to an idx that carried it earlier, or another agent did the searching
        and passed you the handle. An insight's idx returns the memory holding it.

        The idx is all it takes: copy it exactly as it appeared in a search response — it cannot be
        constructed by hand — and nothing else is needed to name the result.

        When a result shows a ref instead of content, ask by the value in its own idx, never the
        value in its ref. A result rendered as <memory idx="memory-THIS-1" ref="memory-EARLIER-1">
        is fetched with "memory-THIS-1": the ref says where the content was delivered, not what to
        ask for. Both return the same text, but only its own idx keeps a later rating with the
        search you are working in.

        Args:
            idx: (Required) The idx of the result to fetch, copied exactly as it appeared in a
                search response. An insight's idx returns the memory holding it.
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
        tags: Iterable[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge to Memco Shared Memory, where your teammates and their agents will
        find it.

        Call when: you have learned something non-obvious that would help your team — why something
        turned out the way it did, how something actually behaves, something that was hard to
        establish, or a decision and its rationale — or your user has corrected you. Search first:
        when a related memory already exists, :meth:`enrich_memory` extends it instead of leaving a
        near-duplicate beside it.

        Pass either a domain or a session_id — a call naming neither is refused. Naming the session
        you have been searching in saves the memory into that session's domain and records it as
        part of that work; naming a domain alone saves a standalone memory.

        Each memory needs a query (what someone would search to find this), a title, and content
        describing what you learned. :meth:`list_domains` says what belongs in the chosen domain and
        which tags to use.

        Args:
            query: (Required) A query describing what someone would search to find this memory, such
                as a question or problem statement. Use markdown formatting for readability. At most
                1000 characters.
            title: (Required) A short title describing what this memory is about. Title and content
                together must be at most 5000 characters.
            content: (Required) The knowledge to save. Should be a concise, non-trivial finding that
                others can learn from. Supports markdown formatting. Title and content together must
                be at most 5000 characters; split a longer finding across several memories.
            domain: The memory domain to save into. Required unless you pass session_id, which
                supplies the domain of the session it names. Call :meth:`list_domains` for the
                domains available to you.
            session_id: The session this memory was learned during, as returned by
                :meth:`start_session` or a previous search. It records the memory as part of that
                series of work, and supplies the memory domain, so the domain argument is not needed
                and is ignored. Omit it to save a standalone memory.
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
        tags: Iterable[Tag] | None = None,
        sources: Iterable[str] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Add information to an existing memory in Memco Shared Memory, so your finding lands
        beside the one it belongs to rather than in a memory that competes with it.

        Call when: a search returned a memory close to what you learned but incomplete, out of date,
        or missing the approach you took. Use :meth:`create_memory` instead when nothing returned
        covers the subject at all.

        Set memory_idx to the memory you want to extend (from search results), or 'new' to add a
        standalone addition. Keep an addition concise and say only what is not already there. The
        addition lands in the domain the search session ran in; you do not name one.

        Args:
            memory_idx: (Required) The memory_idx of the memory you are enriching. If you are adding
                to a new memory, set memory_idx to 'new'.
            session_id: (Required) The session id you are enriching a memory in. The ID was included
                in the response from :meth:`search`.
            title: (Required) A short title describing what you learned. Title and content together
                must be at most 5000 characters.
            content: (Required) The knowledge you want to add. Use markdown formatting for
                readability. Title and content together must be at most 5000 characters; split a
                longer finding across several enrichments.
            sources: A list of memories received from Memco Shared Memory that proved helpful in
                reaching this insight. Up to 20 sources can be included.
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
        feedback: Iterable[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the relevance and correctness of search results. Only you can tell whether a result
        answered the query, and these ratings shape which results are shown next.

        Call when: you have read the results of a search and can judge them — once per search, while
        its session id is still to hand.

        The feedback is recorded against the domain the search session ran in; you do not name one.

        Args:
            session_id: (Required) The session you are providing feedback for. The ID was included
                in the response from :meth:`search`.
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
        """Undo a memory you just wrote, using the operation id that :meth:`create_memory` or
        :meth:`enrich_memory` returned.

        Call when: you saved something by mistake — wrong content, the wrong domain, or something
        that should not have been shared.

        Your entry is always removed. The memory it belongs to is removed with it only when your
        entry was the last one in it — so reverting a :meth:`create_memory`, or an
        :meth:`enrich_memory` you sent with memory_idx 'new', removes that memory too, while
        reverting an addition to a memory that holds other entries leaves the memory in place. You
        can only revert your own writes, and only within 2 days.

        Args:
            operation_id: (Required) The operation id returned by the :meth:`create_memory` or
                :meth:`enrich_memory` call you want to undo, for example 'create-hpc08-1'.
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
        memories: Iterable[ImportedMemory],
        *,
        domain: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> ImportResult:
        """Fill a new or nearly empty workspace with the knowledge a team already holds, in one
        call, so memory starts out useful instead of empty.

        Call when: filling a workspace that has little or nothing in it, or onboarding someone into
        one — a teammate joining, or your own first connection to it. This is a setup step, done
        once: you are handing over what is already known, not recording something you just learned.
        Use :meth:`create_memory` for a single finding from this session.

        Pass either a domain or a session_id — a call naming neither is refused. Each memory needs
        at least one query describing what someone would search to find it, and at least one insight
        with a title and content. At most 25 memories per call, 20 queries and 10 insights each;
        send several calls for more.

        Every memory is checked on the way in and starts at your own standing, exactly as a single
        write does. The response answers per memory, by the position you sent it in: one that was
        refused says why, and one whose content is already held says so and is not written again.

        Args:
            memories: (Required) The memories to contribute. At least one, at most 25 per call; send
                several calls for more.
            domain: The memory domain to import into. Required unless you pass session_id, which
                supplies the domain of the session it names. Call :meth:`list_domains` for the
                domains available to you.
            session_id: The session these memories were contributed during, as returned by
                :meth:`start_session` or a previous search. It records them as part of that series
                of work, and supplies the memory domain, so the domain argument is not needed and is
                ignored.
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
        tags: Iterable[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search Memco Shared Memory for existing knowledge before working a problem out from
        scratch. It holds what your teammates and their agents have already established and
        recorded.

        Call when: you start a task, plan a non-trivial piece of work, meet something unfamiliar,
        hit a question you cannot answer from what you already know, or are about to reason out
        something a teammate may already have settled. Search first, then work.

        Pass either a domain or a session_id — a search naming neither is refused. Naming a session
        runs the search in that session's domain and records it alongside the other searches made
        for the same task; naming a domain alone starts a session for this one search.

        The query uses both keyword and semantic search, and is intended for a single concept per
        query. If you need varied information, make multiple queries.

        Supply tags to narrow the results; :meth:`~memco.operations.MemoryOperations.list_domains`
        lists the tag types the chosen domain uses and the format they take.

        Results come back most-relevant-first and are bounded, so a search returns what fits rather
        than everything that matched; the response says what it left out. Memories are written by
        your teammates and their agents. Within one session a result already returned is not
        repeated — it comes back as a reference to the idx that carried it, which :meth:`get_memory`
        turns back into content.

        Args:
            query: (Required) A task-based query from the user such as a question, statement, or
                task description. To ensure readability, use markdown formatting. At most 1000
                characters.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain; :meth:`MemoryOperations.list_domains`
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
        """Fetch one memory a search returned, by its idx, and get it back in full.

        Call when: you hold an idx whose content is not in front of you — a search returned the
        memory as a reference to an idx that carried it earlier, or another agent did the searching
        and passed you the handle. An insight's idx returns the memory holding it.

        The idx is all it takes: copy it exactly as it appeared in a search response — it cannot be
        constructed by hand — and nothing else is needed to name the result.

        When a result shows a ref instead of content, ask by the value in its own idx, never the
        value in its ref. A result rendered as <memory idx="memory-THIS-1" ref="memory-EARLIER-1">
        is fetched with "memory-THIS-1": the ref says where the content was delivered, not what to
        ask for. Both return the same text, but only its own idx keeps a later rating with the
        search you are working in.

        Args:
            idx: (Required) The idx of the result to fetch, copied exactly as it appeared in a
                search response. An insight's idx returns the memory holding it.
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
        tags: Iterable[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge to Memco Shared Memory, where your teammates and their agents will
        find it.

        Call when: you have learned something non-obvious that would help your team — why something
        turned out the way it did, how something actually behaves, something that was hard to
        establish, or a decision and its rationale — or your user has corrected you. Search first:
        when a related memory already exists, :meth:`enrich_memory` extends it instead of leaving a
        near-duplicate beside it.

        Pass either a domain or a session_id — a call naming neither is refused. Naming the session
        you have been searching in saves the memory into that session's domain and records it as
        part of that work; naming a domain alone saves a standalone memory.

        Each memory needs a query (what someone would search to find this), a title, and content
        describing what you learned. :meth:`~memco.operations.MemoryOperations.list_domains` says
        what belongs in the chosen domain and which tags to use.

        Args:
            query: (Required) A query describing what someone would search to find this memory, such
                as a question or problem statement. Use markdown formatting for readability. At most
                1000 characters.
            title: (Required) A short title describing what this memory is about. Title and content
                together must be at most 5000 characters.
            content: (Required) The knowledge to save. Should be a concise, non-trivial finding that
                others can learn from. Supports markdown formatting. Title and content together must
                be at most 5000 characters; split a longer finding across several memories.
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
        tags: Iterable[Tag] | None = None,
        sources: Iterable[str] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Add information to an existing memory in Memco Shared Memory, so your finding lands
        beside the one it belongs to rather than in a memory that competes with it.

        Call when: a search returned a memory close to what you learned but incomplete, out of date,
        or missing the approach you took. Use :meth:`create_memory` instead when nothing returned
        covers the subject at all.

        Set memory_idx to the memory you want to extend (from search results), or 'new' to add a
        standalone addition. Keep an addition concise and say only what is not already there. The
        addition lands in the domain the search session ran in; you do not name one.

        Args:
            memory_idx: (Required) The memory_idx of the memory you are enriching. If you are adding
                to a new memory, set memory_idx to 'new'.
            title: (Required) A short title describing what you learned. Title and content together
                must be at most 5000 characters.
            content: (Required) The knowledge you want to add. Use markdown formatting for
                readability. Title and content together must be at most 5000 characters; split a
                longer finding across several enrichments.
            tags: Tags describing the addition.
            sources: A list of memories received from Memco Shared Memory that proved helpful in
                reaching this insight. Up to 20 sources can be included.
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
        feedback: Iterable[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the relevance and correctness of search results. Only you can tell whether a result
        answered the query, and these ratings shape which results are shown next.

        Call when: you have read the results of a search and can judge them — once per search, while
        its session id is still to hand.

        The feedback is recorded against the domain the search session ran in; you do not name one.

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
        """Undo a memory you just wrote, using the operation id that :meth:`create_memory` or
        :meth:`enrich_memory` returned.

        Call when: you saved something by mistake — wrong content, the wrong domain, or something
        that should not have been shared.

        Your entry is always removed. The memory it belongs to is removed with it only when your
        entry was the last one in it — so reverting a :meth:`create_memory`, or an
        :meth:`enrich_memory` you sent with memory_idx 'new', removes that memory too, while
        reverting an addition to a memory that holds other entries leaves the memory in place. You
        can only revert your own writes, and only within 2 days.

        Args:
            operation_id: (Required) The operation id returned by the :meth:`create_memory` or
                :meth:`enrich_memory` call you want to undo, for example 'create-hpc08-1'.
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
        self, memories: Iterable[ImportedMemory], *, timeout: float | None = None
    ) -> ImportResult:
        """Fill a new or nearly empty workspace with the knowledge a team already holds, in one
        call, so memory starts out useful instead of empty.

        Call when: filling a workspace that has little or nothing in it, or onboarding someone into
        one — a teammate joining, or your own first connection to it. This is a setup step, done
        once: you are handing over what is already known, not recording something you just learned.
        Use :meth:`create_memory` for a single finding from this session.

        Pass either a domain or a session_id — a call naming neither is refused. Each memory needs
        at least one query describing what someone would search to find it, and at least one insight
        with a title and content. At most 25 memories per call, 20 queries and 10 insights each;
        send several calls for more.

        Every memory is checked on the way in and starts at your own standing, exactly as a single
        write does. The response answers per memory, by the position you sent it in: one that was
        refused says why, and one whose content is already held says so and is not written again.

        Args:
            memories: (Required) The memories to contribute. At least one, at most 25 per call; send
                several calls for more.
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
        tags: Iterable[Tag] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search Memco Shared Memory for existing knowledge before working a problem out from
        scratch. It holds what your teammates and their agents have already established and
        recorded.

        Call when: you start a task, plan a non-trivial piece of work, meet something unfamiliar,
        hit a question you cannot answer from what you already know, or are about to reason out
        something a teammate may already have settled. Search first, then work.

        Pass either a domain or a session_id — a search naming neither is refused. Naming a session
        runs the search in that session's domain and records it alongside the other searches made
        for the same task; naming a domain alone starts a session for this one search.

        The query uses both keyword and semantic search, and is intended for a single concept per
        query. If you need varied information, make multiple queries.

        Supply tags to narrow the results;
        :meth:`~memco.operations.AsyncMemoryOperations.list_domains` lists the tag types the chosen
        domain uses and the format they take.

        Results come back most-relevant-first and are bounded, so a search returns what fits rather
        than everything that matched; the response says what it left out. Memories are written by
        your teammates and their agents. Within one session a result already returned is not
        repeated — it comes back as a reference to the idx that carried it, which :meth:`get_memory`
        turns back into content.

        Args:
            query: (Required) A task-based query from the user such as a question, statement, or
                task description. To ensure readability, use markdown formatting. At most 1000
                characters.
            tags: Tags narrowing or boosting the results. Which types narrow
                rather than boost is per-domain;
                :meth:`AsyncMemoryOperations.list_domains` describes them.
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
        """Fetch one memory a search returned, by its idx, and get it back in full.

        Call when: you hold an idx whose content is not in front of you — a search returned the
        memory as a reference to an idx that carried it earlier, or another agent did the searching
        and passed you the handle. An insight's idx returns the memory holding it.

        The idx is all it takes: copy it exactly as it appeared in a search response — it cannot be
        constructed by hand — and nothing else is needed to name the result.

        When a result shows a ref instead of content, ask by the value in its own idx, never the
        value in its ref. A result rendered as <memory idx="memory-THIS-1" ref="memory-EARLIER-1">
        is fetched with "memory-THIS-1": the ref says where the content was delivered, not what to
        ask for. Both return the same text, but only its own idx keeps a later rating with the
        search you are working in.

        Args:
            idx: (Required) The idx of the result to fetch, copied exactly as it appeared in a
                search response. An insight's idx returns the memory holding it.
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
        tags: Iterable[Tag] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Save new knowledge to Memco Shared Memory, where your teammates and their agents will
        find it.

        Call when: you have learned something non-obvious that would help your team — why something
        turned out the way it did, how something actually behaves, something that was hard to
        establish, or a decision and its rationale — or your user has corrected you. Search first:
        when a related memory already exists, :meth:`enrich_memory` extends it instead of leaving a
        near-duplicate beside it.

        Pass either a domain or a session_id — a call naming neither is refused. Naming the session
        you have been searching in saves the memory into that session's domain and records it as
        part of that work; naming a domain alone saves a standalone memory.

        Each memory needs a query (what someone would search to find this), a title, and content
        describing what you learned. :meth:`~memco.operations.AsyncMemoryOperations.list_domains`
        says what belongs in the chosen domain and which tags to use.

        Args:
            query: (Required) A query describing what someone would search to find this memory, such
                as a question or problem statement. Use markdown formatting for readability. At most
                1000 characters.
            title: (Required) A short title describing what this memory is about. Title and content
                together must be at most 5000 characters.
            content: (Required) The knowledge to save. Should be a concise, non-trivial finding that
                others can learn from. Supports markdown formatting. Title and content together must
                be at most 5000 characters; split a longer finding across several memories.
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
        tags: Iterable[Tag] | None = None,
        sources: Iterable[str] | None = None,
        source: DataSource = DataSource.AGENT,
        timeout: float | None = None,
    ) -> WriteResult:
        """Add information to an existing memory in Memco Shared Memory, so your finding lands
        beside the one it belongs to rather than in a memory that competes with it.

        Call when: a search returned a memory close to what you learned but incomplete, out of date,
        or missing the approach you took. Use :meth:`create_memory` instead when nothing returned
        covers the subject at all.

        Set memory_idx to the memory you want to extend (from search results), or 'new' to add a
        standalone addition. Keep an addition concise and say only what is not already there. The
        addition lands in the domain the search session ran in; you do not name one.

        Args:
            memory_idx: (Required) The memory_idx of the memory you are enriching. If you are adding
                to a new memory, set memory_idx to 'new'.
            title: (Required) A short title describing what you learned. Title and content together
                must be at most 5000 characters.
            content: (Required) The knowledge you want to add. Use markdown formatting for
                readability. Title and content together must be at most 5000 characters; split a
                longer finding across several enrichments.
            tags: Tags describing the addition.
            sources: A list of memories received from Memco Shared Memory that proved helpful in
                reaching this insight. Up to 20 sources can be included.
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
        feedback: Iterable[FeedbackRating],
        timeout: float | None = None,
    ) -> FeedbackResult:
        """Rate the relevance and correctness of search results. Only you can tell whether a result
        answered the query, and these ratings shape which results are shown next.

        Call when: you have read the results of a search and can judge them — once per search, while
        its session id is still to hand.

        The feedback is recorded against the domain the search session ran in; you do not name one.

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
        """Undo a memory you just wrote, using the operation id that :meth:`create_memory` or
        :meth:`enrich_memory` returned.

        Call when: you saved something by mistake — wrong content, the wrong domain, or something
        that should not have been shared.

        Your entry is always removed. The memory it belongs to is removed with it only when your
        entry was the last one in it — so reverting a :meth:`create_memory`, or an
        :meth:`enrich_memory` you sent with memory_idx 'new', removes that memory too, while
        reverting an addition to a memory that holds other entries leaves the memory in place. You
        can only revert your own writes, and only within 2 days.

        Args:
            operation_id: (Required) The operation id returned by the :meth:`create_memory` or
                :meth:`enrich_memory` call you want to undo, for example 'create-hpc08-1'.
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
        self, memories: Iterable[ImportedMemory], *, timeout: float | None = None
    ) -> ImportResult:
        """Fill a new or nearly empty workspace with the knowledge a team already holds, in one
        call, so memory starts out useful instead of empty.

        Call when: filling a workspace that has little or nothing in it, or onboarding someone into
        one — a teammate joining, or your own first connection to it. This is a setup step, done
        once: you are handing over what is already known, not recording something you just learned.
        Use :meth:`create_memory` for a single finding from this session.

        Pass either a domain or a session_id — a call naming neither is refused. Each memory needs
        at least one query describing what someone would search to find it, and at least one insight
        with a title and content. At most 25 memories per call, 20 queries and 10 insights each;
        send several calls for more.

        Every memory is checked on the way in and starts at your own standing, exactly as a single
        write does. The response answers per memory, by the position you sent it in: one that was
        refused says why, and one whose content is already held says so and is not written again.

        Args:
            memories: (Required) The memories to contribute. At least one, at most 25 per call; send
                several calls for more.
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
