"""Immutable result types returned by the Memco SDK.

Every method returns one of these frozen dataclasses rather than a protobuf
message. That keeps the generated code an implementation detail, and lets the
SDK present values in their natural Python form: dates as :class:`datetime.date`,
enumerations as :class:`enum.Enum`, repeated fields as tuples, and genuinely
absent strings as ``None``.

Values are immutable, so a result can be cached or shared between threads
without defensive copying.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from memco.memory.v1 import memory_pb2 as _pb

__all__ = [
    "DataSource",
    "DomainEntry",
    "DomainList",
    "FeedbackEntry",
    "FeedbackRating",
    "FeedbackResult",
    "ImportOutcome",
    "ImportResult",
    "ImportStatus",
    "ImportedInsight",
    "ImportedMemory",
    "Insight",
    "Instructions",
    "Limits",
    "Memory",
    "ProtoRecord",
    "Provenance",
    "RevertOutcome",
    "RevertResult",
    "SearchResult",
    "Session",
    "Tag",
    "WriteResult",
]


class DataSource(enum.Enum):
    """Who produced the content of a write.

    Attributes:
        UNSPECIFIED: Not stated. The service reads this as :attr:`AGENT`.
        USER: The content came from a human.
        AGENT: The content came from an agent. This is the usual value and the
            SDK's default.
    """

    UNSPECIFIED = 0
    USER = 1
    AGENT = 2

    @classmethod
    def from_wire(cls, value: int) -> DataSource:
        """Convert a wire value, tolerating one this SDK does not know.

        Args:
            value: The enum value as it arrived on the wire.

        Returns:
            The matching member, or :attr:`UNSPECIFIED` if the service sent a
            value added after this SDK was released.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.UNSPECIFIED


class RevertOutcome(enum.Enum):
    """What a revert actually removed.

    A revert reports the effect it had rather than the operation that was asked
    for, so several of these describe a successful call that removed nothing.

    Attributes:
        UNSPECIFIED: No outcome was reported.
        MEMORY_REMOVED: The reverted insight was its memory's last, so the memory
            and its orphaned intents went with it.
        ADDITION_REMOVED: Only the caller's insight was removed; its memory holds
            others and stays.
        ENTRY_REMOVED: The caller's insight was removed and no memory was
            involved, which is what happens to a write the validator rejected.
        MERGED: The write had been folded into an existing insight as an
            endorsement. The duplicate was removed and the endorsed insight is
            untouched.
        NOT_FOUND: No write by this caller carries that operation id, either
            because it never existed or because its ingestion is still running.
        EXPIRED: The operation is older than the revert window.
        REFUSED: The content is under moderation and only a moderator may remove
            it.
    """

    UNSPECIFIED = 0
    MEMORY_REMOVED = 1
    ADDITION_REMOVED = 2
    ENTRY_REMOVED = 3
    MERGED = 4
    NOT_FOUND = 5
    EXPIRED = 6
    REFUSED = 7

    @classmethod
    def from_wire(cls, value: int) -> RevertOutcome:
        """Convert a wire value, tolerating one this SDK does not know.

        Args:
            value: The enum value as it arrived on the wire.

        Returns:
            The matching member, or :attr:`UNSPECIFIED` if the service sent a
            value added after this SDK was released.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.UNSPECIFIED


class ImportStatus(enum.Enum):
    """What became of one memory of an imported batch.

    Every memory is judged on its own, so a refused entry does not stop the
    others. None of these is an error: each reports what happened to one entry
    of a call that succeeded.

    Attributes:
        UNSPECIFIED: No status was reported.
        QUEUED: Accepted and queued for writing.
        REJECTED: The entry itself was not usable; its ``errors`` say what about
            it.
        ERROR: The entry was usable but could not be queued. Resubmitting it is
            the remedy: an import is written under an identity derived from its
            own content, so a memory that did land is not duplicated by sending
            it again.
        DUPLICATE: The content is already in memory, so nothing was written and
            nothing was charged. Sending the same batch again is safe and free.
    """

    UNSPECIFIED = 0
    QUEUED = 1
    REJECTED = 2
    ERROR = 3
    DUPLICATE = 4

    @classmethod
    def from_wire(cls, value: int) -> ImportStatus:
        """Convert a wire value, tolerating one this SDK does not know.

        Args:
            value: The enum value as it arrived on the wire.

        Returns:
            The matching member, or :attr:`UNSPECIFIED` if the service sent a
            value added after this SDK was released.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.UNSPECIFIED


@dataclass(frozen=True, slots=True)
class Tag:
    """One selector on a search or a write.

    The service lowercases each field and folds hyphens to underscores, so
    ``"Go"`` and ``"go"`` name the same tag. Which types exist, which of them
    narrow results rather than boost them, and which carry a version are all
    per-domain; :meth:`~memco.operations.MemoryOperations.list_domains` describes them.

    Attributes:
        type: The tag's category, such as ``"language"`` or ``"framework"``.
        value: The value within that category, such as ``"python"``.
        version: Version of the thing named, where its type carries one. A
            version on a type that does not carry one is dropped by the service.
            Most tag types carry no version, so this is usually ``None``.

    Example:
        >>> from memco.types import Tag
        >>> Tag(type="language", value="python", version="3.12")
        Tag(type='language', value='python', version='3.12')
    """

    type: str
    value: str
    version: str | None = None

    def to_proto(self) -> _pb.Tag:
        """Convert to the wire message.

        Returns:
            The protobuf ``Tag``, with ``version`` left unset when it is ``None``.
        """
        message = _pb.Tag(type=self.type, value=self.value)
        if self.version is not None:
            message.version = self.version
        return message


@dataclass(frozen=True, slots=True)
class Instructions:
    """Model-facing guidance accompanying a result.

    Held apart from the data so a caller can render its own document or use this
    one. Each part arrives already rendered for its domain, and a part with
    nothing to say is an empty string rather than ``None``.

    Attributes:
        content: What the result is, or what happened.
        policy: How a policy result outranks the rest. Non-empty only when one
            was returned.
        adding: How to contribute back to what came back.
        rating: How to rate what came back.
        next: The follow-up call this result enables, such as the handle that
            undoes a write.
    """

    content: str
    policy: str
    adding: str
    rating: str
    next: str


@dataclass(frozen=True, slots=True)
class DomainEntry:
    """One memory domain: what it holds and the tag vocabulary it uses.

    Attributes:
        slug: The value to pass as the ``domain`` argument elsewhere.
        title: Human-readable name.
        summary: What the domain holds.
        when_to_search: When to draw on it.
        when_to_save: What is worth writing to it.
        what_not_to_save: What must never be written to it.
        tags_description: Rendered description of the tag vocabulary.
        filter_tag_types: Tag types that narrow results rather than boost them.
            This is the distinction that decides whether a wrong tag returns
            nothing at all.
        version_tag_types: Tag types that carry a version. A version on any other
            type is dropped.
        max_tags_per_query: Bounds the tags on a call naming this domain.
            **Trimmed** rather than refused, and per-domain rather than global.
            Zero means this domain sets no cap.
    """

    slug: str
    title: str
    summary: str
    when_to_search: str
    when_to_save: str
    what_not_to_save: str
    tags_description: str
    filter_tag_types: tuple[str, ...]
    version_tag_types: tuple[str, ...]
    max_tags_per_query: int


@dataclass(frozen=True, slots=True)
class Limits:
    """The caps the service enforces on request fields.

    Delivered by :meth:`~memco.operations.MemoryOperations.list_domains` so
    the service owns them: an SDK carrying its own numbers would keep rejecting
    requests the service had started accepting. A client that has not asked for
    them validates nothing and lets the service rule.

    Attributes:
        max_query_characters: Bounds ``query`` on search and on a create.
            Exceeding it is refused.
        max_text_characters: Bounds ``title`` and ``content`` **together**, not
            each. Exceeding it is refused.
        max_idx_characters: Bounds every handle-shaped value — ``idx``,
            ``memory_idx``, and each entry of ``sources``. Exceeding it is
            refused.
        max_sources: Bounds ``sources`` on an enrichment. **Trimmed** rather
            than refused: the service keeps the first this many, so a client
            trims to match instead of rejecting a call the service would accept.
        max_feedback_entries: Bounds the ratings in one feedback call. Exceeding
            it is refused.
        max_import_memories: Bounds the memories in one import call — one
            *call*, not one batch. Exceeding it is refused, so the SDK divides a
            longer batch into groups of this size and makes several calls; a
            caller does not have to chunk against it.
        max_import_queries_per_memory: Bounds the queries on one imported
            memory. Exceeding it is refused.
        max_import_insights_per_memory: Bounds the insights on one imported
            memory. Exceeding it is refused.
        max_import_tags_per_memory: Bounds the tags on one imported memory.
            Exceeding it is refused — unlike the per-domain cap reported by
            :attr:`DomainEntry.max_tags_per_query`, which trims.
    """

    max_query_characters: int
    max_text_characters: int
    max_idx_characters: int
    max_sources: int
    max_feedback_entries: int
    max_import_memories: int
    max_import_queries_per_memory: int
    max_import_insights_per_memory: int
    max_import_tags_per_memory: int


@dataclass(frozen=True, slots=True)
class DomainList:
    """The domains the caller may name.

    Attributes:
        domains: The available domains.
        instructions: Guidance accompanying the result.
        limits: The caps the service enforces on request fields, or ``None``
            against a service that does not report them. ``None`` means *do not
            validate*, never zero.
        deprecated: Whether what this caller is using has been superseded —
            either the API version or this SDK build. Which of the two is
            deliberately not distinguished here; ``deprecation_message`` says.
        deprecation_message: The service-authored remedy, empty when nothing is
            deprecated. It is the whole of what a caller should be shown.
        sunset_date: When what the caller uses stops working, or ``None`` when
            no date is set — which is not a promise that none will be.
        server_commit: The build that answered this call, for quoting in a bug
            report. Empty against a service that does not report one. This is
            not :func:`~memco.provenance`'s ``server_commit``, which is the
            commit the installed package was generated from.
    """

    domains: tuple[DomainEntry, ...]
    instructions: Instructions
    limits: Limits | None
    deprecated: bool
    deprecation_message: str
    sunset_date: date | None
    server_commit: str


@dataclass(frozen=True, slots=True)
class Session:
    """An open session.

    Attributes:
        session_id: Handle identifying the session. Pass it to subsequent
            searches and writes so they are recorded as one series.
        instructions: Guidance accompanying the result.
    """

    session_id: str
    instructions: Instructions


@dataclass(frozen=True, slots=True)
class Insight:
    """One insight held under a memory.

    Attributes:
        idx: Handle addressing this insight. Copy it exactly; it cannot be
            constructed by hand.
        title: Short title.
        content: The insight itself.
        updated: The day the insight was last written. Enrichment edits an
            insight in place, so this is the age of the knowledge rather than of
            the record. ``None`` if the service sent no parseable date.
        times_served: How often this insight has been delivered to anyone. It
            records deliveries and nothing else: a frequently served insight is
            not thereby a correct one, which is what :attr:`endorsed` and
            :attr:`disputed` answer.
        endorsed: How many callers rated this insight correct.
        disputed: How many callers rated it incorrect.
    """

    idx: str
    title: str
    content: str
    updated: date | None
    times_served: int
    endorsed: int
    disputed: int


@dataclass(frozen=True, slots=True)
class Memory:
    """One memory, with the insights sampled from it.

    Attributes:
        idx: Handle addressing this memory.
        kind: The memory's kind, as reported by the service.
        times_served: How often this memory has been delivered, this delivery
            included.
        intents: The questions this memory has been retrieved by, oldest first.
            They describe what it answers; none of them is "the one that
            matched", since a memory is retrieved whole.
        insights: The insights sampled from this memory. Empty when
            :attr:`reference` is set.
        reference: The handle an earlier search in the same session returned this
            memory under. When set, the memory is already in the caller's context
            under that handle and no insights are repeated here; pass this
            result's own :attr:`idx` to
            :meth:`~memco.operations.MemoryOperations.get_memory` to read it again.
    """

    idx: str
    kind: str
    times_served: int
    intents: tuple[str, ...]
    insights: tuple[Insight, ...]
    reference: str | None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """What a search selected.

    Attributes:
        session_id: The session this search was recorded under. Reuse it for
            follow-up searches and for rating these results.
        memories: The memories selected, in rendered order. Empty when the search
            selected nothing.
        notice: A remark about the query itself rather than about its results,
            or ``None`` when there is nothing to say.
        instructions: Guidance accompanying the result.
    """

    session_id: str
    memories: tuple[Memory, ...]
    notice: str | None
    instructions: Instructions


@dataclass(frozen=True, slots=True)
class WriteResult:
    """The outcome of a write, which the service accepts asynchronously.

    Because the write is still being ingested, the result addresses the
    operation rather than the memory it will become.

    Attributes:
        operation_id: Handle addressing the write, for
            :meth:`~memco.operations.MemoryOperations.revert_memory`. ``None`` when no handle
            could be minted: the content is worth more than the ability to undo
            it, so a numbering failure degrades to an un-revertible write rather
            than a rejected one.
        instructions: Guidance accompanying the result.
    """

    operation_id: str | None
    instructions: Instructions


@dataclass(frozen=True, slots=True)
class FeedbackRating:
    """One rating to give a search result.

    Attributes:
        idx: Handle copied exactly from a search result. An insight's handle
            rates that insight; a memory's own handle rates every insight under
            it. It cannot be constructed by hand.
        relevant: Whether the result was a good match for the query.
        correct: Whether its content was accurate.
        comment: An optional note about this result.

    Example:
        >>> from memco.types import FeedbackRating
        >>> FeedbackRating(idx="memory-a-1-insight-1", relevant=True, correct=True)
        FeedbackRating(idx='memory-a-1-insight-1', relevant=True, correct=True, comment=None)
    """

    idx: str
    relevant: bool
    correct: bool
    comment: str | None = None

    def to_proto(self) -> _pb.FeedbackRating:
        """Convert to the wire message.

        Returns:
            The protobuf ``FeedbackRating``, with ``comment`` left unset when it
            is ``None``.
        """
        message = _pb.FeedbackRating(idx=self.idx, relevant=self.relevant, correct=self.correct)
        if self.comment is not None:
            message.comment = self.comment
        return message


@dataclass(frozen=True, slots=True)
class FeedbackEntry:
    """One rating that was recorded.

    Attributes:
        idx: The handle that was rated.
        relevant: The relevance verdict that was recorded.
        correct: The correctness verdict that was recorded.
        advice: The suggestion this particular verdict earned, or ``None`` when
            the verdict suggests nothing. It addresses one result, so it belongs
            to the entry rather than to the batch.
    """

    idx: str
    relevant: bool
    correct: bool
    advice: str | None


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    """The ratings that were recorded for one search.

    Attributes:
        session_id: The session whose search was rated.
        entries: One entry per rating recorded.
        instructions: Guidance accompanying the result.
    """

    session_id: str
    entries: tuple[FeedbackEntry, ...]
    instructions: Instructions


@dataclass(frozen=True, slots=True)
class RevertResult:
    """What a revert removed.

    Every outcome arrives as a successful call. ``NOT_FOUND``, ``EXPIRED`` and
    ``REFUSED`` report caller-visible state rather than a service failure, so
    they are values here rather than exceptions.

    Attributes:
        operation_id: The operation that was addressed, or ``None`` if the
            service reported none.
        outcome: What the revert actually removed.
        instructions: Guidance accompanying the result.

    Example:
        >>> result = client.memory.revert_memory("create-abc")
        >>> if result.outcome is RevertOutcome.EXPIRED:
        ...     print("too late to undo that one")
    """

    operation_id: str | None
    outcome: RevertOutcome
    instructions: Instructions


@dataclass(frozen=True, slots=True)
class ImportedInsight:
    """One insight to contribute as part of an imported memory.

    Attributes:
        title: (Required) A short title describing what this insight is about. Title and content
            together must be at most 5000 characters.
        content: (Required) The knowledge to save. Should be a concise, non-trivial finding that
            others can learn from. Supports markdown formatting.

    Example:
        >>> from memco.types import ImportedInsight
        >>> ImportedInsight(title="Bearer is case-sensitive",
        ...                 content="Lowercase 'bearer' is rejected.")
        ImportedInsight(title='Bearer is case-sensitive', content="Lowercase 'bearer' is rejected.")
    """

    title: str
    content: str

    def to_proto(self) -> _pb.ImportedInsight:
        """Convert to the wire message.

        Returns:
            The protobuf ``ImportedInsight``.
        """
        return _pb.ImportedInsight(title=self.title, content=self.content)


@dataclass(frozen=True, slots=True)
class ImportedMemory:
    """One memory to contribute: what it should be found by, and what it holds.

    Attributes:
        queries: (Required) The queries someone would search to find this memory, such as questions
            or problem statements. At least one, at most 20.
        insights: (Required) The findings this memory holds. At least one, at most 10.
        tags: Tags describing the subject and context.

    Example:
        >>> from memco.types import ImportedInsight, ImportedMemory, Tag
        >>> memory = ImportedMemory(
        ...     queries=["how do I authenticate against the memory API"],
        ...     insights=[ImportedInsight(title="Bearer is case-sensitive",
        ...                               content="Lowercase 'bearer' is rejected.")],
        ...     tags=[Tag(type="language", value="python")],
        ... )
    """

    queries: Iterable[str]
    insights: Iterable[ImportedInsight]
    tags: Iterable[Tag] | None = None


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """What happened to one memory of an imported batch.

    An import mints no handle a caller could name a memory by, so an outcome is
    addressed by the position its memory held in the request.

    Attributes:
        index: The position of this memory in the submitted batch.
        status: What became of it.
        errors: What was wrong with an entry that was not queued. Empty for one
            that was.
    """

    index: int
    status: ImportStatus
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """What an import accepted.

    Each memory is written asynchronously, so this reports what was accepted
    rather than what now exists. There is no operation id: a batch mints none,
    and nothing undoes an import.

    Attributes:
        results: One outcome per memory submitted, in the order they were sent.
        instructions: Guidance accompanying the result.

    Example:
        >>> result = client.memory.import_memories(batch, domain="coding")
        >>> [(o.index, o.status.name) for o in result.results]
        [(0, 'QUEUED'), (1, 'DUPLICATE')]
    """

    results: tuple[ImportOutcome, ...]
    instructions: Instructions


@dataclass(frozen=True, slots=True)
class ProtoRecord:
    """One contract file the generated client was built from.

    Attributes:
        path: Path of the ``.proto`` within the contract tree.
        sha256: Hex-encoded SHA-256 of that file's contents.
    """

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Provenance:
    """Which version of the contract this SDK's generated client came from.

    Read from the descriptor the export ships inside the package, so it
    describes the installed artifact rather than the repository it was built in.

    Attributes:
        server_commit: Commit of the server repository that produced the
            generated client.
        protos: The contract files it was generated from, with their checksums.

    Example:
        >>> provenance().server_commit  # the commit this wheel was built from
        '762721a87ab0...'
    """

    server_commit: str
    protos: tuple[ProtoRecord, ...]
