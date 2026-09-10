from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class DataSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DATA_SOURCE_UNSPECIFIED: _ClassVar[DataSource]
    DATA_SOURCE_USER: _ClassVar[DataSource]
    DATA_SOURCE_AGENT: _ClassVar[DataSource]

class RevertOutcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REVERT_OUTCOME_UNSPECIFIED: _ClassVar[RevertOutcome]
    REVERT_OUTCOME_MEMORY_REMOVED: _ClassVar[RevertOutcome]
    REVERT_OUTCOME_ADDITION_REMOVED: _ClassVar[RevertOutcome]
    REVERT_OUTCOME_ENTRY_REMOVED: _ClassVar[RevertOutcome]
    REVERT_OUTCOME_MERGED: _ClassVar[RevertOutcome]
    REVERT_OUTCOME_NOT_FOUND: _ClassVar[RevertOutcome]
    REVERT_OUTCOME_EXPIRED: _ClassVar[RevertOutcome]
    REVERT_OUTCOME_REFUSED: _ClassVar[RevertOutcome]

class ImportStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    IMPORT_STATUS_UNSPECIFIED: _ClassVar[ImportStatus]
    IMPORT_STATUS_QUEUED: _ClassVar[ImportStatus]
    IMPORT_STATUS_REJECTED: _ClassVar[ImportStatus]
    IMPORT_STATUS_ERROR: _ClassVar[ImportStatus]
    IMPORT_STATUS_DUPLICATE: _ClassVar[ImportStatus]
DATA_SOURCE_UNSPECIFIED: DataSource
DATA_SOURCE_USER: DataSource
DATA_SOURCE_AGENT: DataSource
REVERT_OUTCOME_UNSPECIFIED: RevertOutcome
REVERT_OUTCOME_MEMORY_REMOVED: RevertOutcome
REVERT_OUTCOME_ADDITION_REMOVED: RevertOutcome
REVERT_OUTCOME_ENTRY_REMOVED: RevertOutcome
REVERT_OUTCOME_MERGED: RevertOutcome
REVERT_OUTCOME_NOT_FOUND: RevertOutcome
REVERT_OUTCOME_EXPIRED: RevertOutcome
REVERT_OUTCOME_REFUSED: RevertOutcome
IMPORT_STATUS_UNSPECIFIED: ImportStatus
IMPORT_STATUS_QUEUED: ImportStatus
IMPORT_STATUS_REJECTED: ImportStatus
IMPORT_STATUS_ERROR: ImportStatus
IMPORT_STATUS_DUPLICATE: ImportStatus

class Instructions(_message.Message):
    __slots__ = ("content", "policy", "adding", "rating", "next")
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    ADDING_FIELD_NUMBER: _ClassVar[int]
    RATING_FIELD_NUMBER: _ClassVar[int]
    NEXT_FIELD_NUMBER: _ClassVar[int]
    content: str
    policy: str
    adding: str
    rating: str
    next: str
    def __init__(self, content: _Optional[str] = ..., policy: _Optional[str] = ..., adding: _Optional[str] = ..., rating: _Optional[str] = ..., next: _Optional[str] = ...) -> None: ...

class Tag(_message.Message):
    __slots__ = ("type", "value", "version")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    type: str
    value: str
    version: str
    def __init__(self, type: _Optional[str] = ..., value: _Optional[str] = ..., version: _Optional[str] = ...) -> None: ...

class DomainEntry(_message.Message):
    __slots__ = ("slug", "title", "summary", "when_to_search", "when_to_save", "what_not_to_save", "tags_description", "filter_tag_types", "version_tag_types", "max_tags_per_query")
    SLUG_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    WHEN_TO_SEARCH_FIELD_NUMBER: _ClassVar[int]
    WHEN_TO_SAVE_FIELD_NUMBER: _ClassVar[int]
    WHAT_NOT_TO_SAVE_FIELD_NUMBER: _ClassVar[int]
    TAGS_DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    FILTER_TAG_TYPES_FIELD_NUMBER: _ClassVar[int]
    VERSION_TAG_TYPES_FIELD_NUMBER: _ClassVar[int]
    MAX_TAGS_PER_QUERY_FIELD_NUMBER: _ClassVar[int]
    slug: str
    title: str
    summary: str
    when_to_search: str
    when_to_save: str
    what_not_to_save: str
    tags_description: str
    filter_tag_types: _containers.RepeatedScalarFieldContainer[str]
    version_tag_types: _containers.RepeatedScalarFieldContainer[str]
    max_tags_per_query: int
    def __init__(self, slug: _Optional[str] = ..., title: _Optional[str] = ..., summary: _Optional[str] = ..., when_to_search: _Optional[str] = ..., when_to_save: _Optional[str] = ..., what_not_to_save: _Optional[str] = ..., tags_description: _Optional[str] = ..., filter_tag_types: _Optional[_Iterable[str]] = ..., version_tag_types: _Optional[_Iterable[str]] = ..., max_tags_per_query: _Optional[int] = ...) -> None: ...

class ListDomainsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListDomainsResponse(_message.Message):
    __slots__ = ("domains", "instructions", "limits", "deprecated", "deprecation_message", "sunset_date", "server_commit")
    DOMAINS_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    DEPRECATED_FIELD_NUMBER: _ClassVar[int]
    DEPRECATION_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    SUNSET_DATE_FIELD_NUMBER: _ClassVar[int]
    SERVER_COMMIT_FIELD_NUMBER: _ClassVar[int]
    domains: _containers.RepeatedCompositeFieldContainer[DomainEntry]
    instructions: Instructions
    limits: Limits
    deprecated: bool
    deprecation_message: str
    sunset_date: str
    server_commit: str
    def __init__(self, domains: _Optional[_Iterable[_Union[DomainEntry, _Mapping]]] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ..., limits: _Optional[_Union[Limits, _Mapping]] = ..., deprecated: bool = ..., deprecation_message: _Optional[str] = ..., sunset_date: _Optional[str] = ..., server_commit: _Optional[str] = ...) -> None: ...

class Limits(_message.Message):
    __slots__ = ("max_query_characters", "max_text_characters", "max_idx_characters", "max_sources", "max_feedback_entries", "max_import_memories", "max_import_queries_per_memory", "max_import_insights_per_memory", "max_import_tags_per_memory")
    MAX_QUERY_CHARACTERS_FIELD_NUMBER: _ClassVar[int]
    MAX_TEXT_CHARACTERS_FIELD_NUMBER: _ClassVar[int]
    MAX_IDX_CHARACTERS_FIELD_NUMBER: _ClassVar[int]
    MAX_SOURCES_FIELD_NUMBER: _ClassVar[int]
    MAX_FEEDBACK_ENTRIES_FIELD_NUMBER: _ClassVar[int]
    MAX_IMPORT_MEMORIES_FIELD_NUMBER: _ClassVar[int]
    MAX_IMPORT_QUERIES_PER_MEMORY_FIELD_NUMBER: _ClassVar[int]
    MAX_IMPORT_INSIGHTS_PER_MEMORY_FIELD_NUMBER: _ClassVar[int]
    MAX_IMPORT_TAGS_PER_MEMORY_FIELD_NUMBER: _ClassVar[int]
    max_query_characters: int
    max_text_characters: int
    max_idx_characters: int
    max_sources: int
    max_feedback_entries: int
    max_import_memories: int
    max_import_queries_per_memory: int
    max_import_insights_per_memory: int
    max_import_tags_per_memory: int
    def __init__(self, max_query_characters: _Optional[int] = ..., max_text_characters: _Optional[int] = ..., max_idx_characters: _Optional[int] = ..., max_sources: _Optional[int] = ..., max_feedback_entries: _Optional[int] = ..., max_import_memories: _Optional[int] = ..., max_import_queries_per_memory: _Optional[int] = ..., max_import_insights_per_memory: _Optional[int] = ..., max_import_tags_per_memory: _Optional[int] = ...) -> None: ...

class StartSessionRequest(_message.Message):
    __slots__ = ("domain",)
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    domain: str
    def __init__(self, domain: _Optional[str] = ...) -> None: ...

class StartSessionResponse(_message.Message):
    __slots__ = ("session_id", "instructions")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    instructions: Instructions
    def __init__(self, session_id: _Optional[str] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ...) -> None: ...

class SearchRequest(_message.Message):
    __slots__ = ("tags", "domain", "query", "session_id")
    TAGS_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    domain: str
    query: str
    session_id: str
    def __init__(self, tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ..., domain: _Optional[str] = ..., query: _Optional[str] = ..., session_id: _Optional[str] = ...) -> None: ...

class SearchResponse(_message.Message):
    __slots__ = ("session_id", "memories", "notice", "instructions")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    MEMORIES_FIELD_NUMBER: _ClassVar[int]
    NOTICE_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    memories: _containers.RepeatedCompositeFieldContainer[MemoryResult]
    notice: str
    instructions: Instructions
    def __init__(self, session_id: _Optional[str] = ..., memories: _Optional[_Iterable[_Union[MemoryResult, _Mapping]]] = ..., notice: _Optional[str] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ...) -> None: ...

class MemoryResult(_message.Message):
    __slots__ = ("idx", "kind", "times_served", "intents", "insights", "reference")
    IDX_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TIMES_SERVED_FIELD_NUMBER: _ClassVar[int]
    INTENTS_FIELD_NUMBER: _ClassVar[int]
    INSIGHTS_FIELD_NUMBER: _ClassVar[int]
    REFERENCE_FIELD_NUMBER: _ClassVar[int]
    idx: str
    kind: str
    times_served: int
    intents: _containers.RepeatedScalarFieldContainer[str]
    insights: _containers.RepeatedCompositeFieldContainer[InsightResult]
    reference: str
    def __init__(self, idx: _Optional[str] = ..., kind: _Optional[str] = ..., times_served: _Optional[int] = ..., intents: _Optional[_Iterable[str]] = ..., insights: _Optional[_Iterable[_Union[InsightResult, _Mapping]]] = ..., reference: _Optional[str] = ...) -> None: ...

class InsightResult(_message.Message):
    __slots__ = ("idx", "title", "content", "updated", "times_served", "endorsed", "disputed")
    IDX_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_FIELD_NUMBER: _ClassVar[int]
    TIMES_SERVED_FIELD_NUMBER: _ClassVar[int]
    ENDORSED_FIELD_NUMBER: _ClassVar[int]
    DISPUTED_FIELD_NUMBER: _ClassVar[int]
    idx: str
    title: str
    content: str
    updated: str
    times_served: int
    endorsed: int
    disputed: int
    def __init__(self, idx: _Optional[str] = ..., title: _Optional[str] = ..., content: _Optional[str] = ..., updated: _Optional[str] = ..., times_served: _Optional[int] = ..., endorsed: _Optional[int] = ..., disputed: _Optional[int] = ...) -> None: ...

class GetMemoryRequest(_message.Message):
    __slots__ = ("idx",)
    IDX_FIELD_NUMBER: _ClassVar[int]
    idx: str
    def __init__(self, idx: _Optional[str] = ...) -> None: ...

class GetMemoryResponse(_message.Message):
    __slots__ = ("memory",)
    MEMORY_FIELD_NUMBER: _ClassVar[int]
    memory: MemoryResult
    def __init__(self, memory: _Optional[_Union[MemoryResult, _Mapping]] = ...) -> None: ...

class CreateMemoryRequest(_message.Message):
    __slots__ = ("tags", "source", "domain", "session_id", "query", "title", "content")
    TAGS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    source: DataSource
    domain: str
    session_id: str
    query: str
    title: str
    content: str
    def __init__(self, tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ..., source: _Optional[_Union[DataSource, str]] = ..., domain: _Optional[str] = ..., session_id: _Optional[str] = ..., query: _Optional[str] = ..., title: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class CreateMemoryResponse(_message.Message):
    __slots__ = ("operation_id", "instructions")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    instructions: Instructions
    def __init__(self, operation_id: _Optional[str] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ...) -> None: ...

class EnrichMemoryRequest(_message.Message):
    __slots__ = ("tags", "source", "title", "content", "sources", "session_id", "memory_idx")
    TAGS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    MEMORY_IDX_FIELD_NUMBER: _ClassVar[int]
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    source: DataSource
    title: str
    content: str
    sources: _containers.RepeatedScalarFieldContainer[str]
    session_id: str
    memory_idx: str
    def __init__(self, tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ..., source: _Optional[_Union[DataSource, str]] = ..., title: _Optional[str] = ..., content: _Optional[str] = ..., sources: _Optional[_Iterable[str]] = ..., session_id: _Optional[str] = ..., memory_idx: _Optional[str] = ...) -> None: ...

class EnrichMemoryResponse(_message.Message):
    __slots__ = ("operation_id", "instructions")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    instructions: Instructions
    def __init__(self, operation_id: _Optional[str] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ...) -> None: ...

class ShareFeedbackRequest(_message.Message):
    __slots__ = ("feedback", "session_id")
    FEEDBACK_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    feedback: _containers.RepeatedCompositeFieldContainer[FeedbackRating]
    session_id: str
    def __init__(self, feedback: _Optional[_Iterable[_Union[FeedbackRating, _Mapping]]] = ..., session_id: _Optional[str] = ...) -> None: ...

class FeedbackRating(_message.Message):
    __slots__ = ("idx", "relevant", "correct", "comment")
    IDX_FIELD_NUMBER: _ClassVar[int]
    RELEVANT_FIELD_NUMBER: _ClassVar[int]
    CORRECT_FIELD_NUMBER: _ClassVar[int]
    COMMENT_FIELD_NUMBER: _ClassVar[int]
    idx: str
    relevant: bool
    correct: bool
    comment: str
    def __init__(self, idx: _Optional[str] = ..., relevant: bool = ..., correct: bool = ..., comment: _Optional[str] = ...) -> None: ...

class ShareFeedbackResponse(_message.Message):
    __slots__ = ("session_id", "entries", "instructions")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    entries: _containers.RepeatedCompositeFieldContainer[FeedbackEntry]
    instructions: Instructions
    def __init__(self, session_id: _Optional[str] = ..., entries: _Optional[_Iterable[_Union[FeedbackEntry, _Mapping]]] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ...) -> None: ...

class FeedbackEntry(_message.Message):
    __slots__ = ("idx", "relevant", "correct", "advice")
    IDX_FIELD_NUMBER: _ClassVar[int]
    RELEVANT_FIELD_NUMBER: _ClassVar[int]
    CORRECT_FIELD_NUMBER: _ClassVar[int]
    ADVICE_FIELD_NUMBER: _ClassVar[int]
    idx: str
    relevant: bool
    correct: bool
    advice: str
    def __init__(self, idx: _Optional[str] = ..., relevant: bool = ..., correct: bool = ..., advice: _Optional[str] = ...) -> None: ...

class RevertMemoryRequest(_message.Message):
    __slots__ = ("op_id",)
    OP_ID_FIELD_NUMBER: _ClassVar[int]
    op_id: str
    def __init__(self, op_id: _Optional[str] = ...) -> None: ...

class RevertMemoryResponse(_message.Message):
    __slots__ = ("operation_id", "outcome", "instructions")
    OPERATION_ID_FIELD_NUMBER: _ClassVar[int]
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    operation_id: str
    outcome: RevertOutcome
    instructions: Instructions
    def __init__(self, operation_id: _Optional[str] = ..., outcome: _Optional[_Union[RevertOutcome, str]] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ...) -> None: ...

class ImportMemoriesRequest(_message.Message):
    __slots__ = ("domain", "memories", "session_id")
    DOMAIN_FIELD_NUMBER: _ClassVar[int]
    MEMORIES_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    domain: str
    memories: _containers.RepeatedCompositeFieldContainer[ImportedMemory]
    session_id: str
    def __init__(self, domain: _Optional[str] = ..., memories: _Optional[_Iterable[_Union[ImportedMemory, _Mapping]]] = ..., session_id: _Optional[str] = ...) -> None: ...

class ImportedMemory(_message.Message):
    __slots__ = ("queries", "insights", "tags")
    QUERIES_FIELD_NUMBER: _ClassVar[int]
    INSIGHTS_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    queries: _containers.RepeatedScalarFieldContainer[str]
    insights: _containers.RepeatedCompositeFieldContainer[ImportedInsight]
    tags: _containers.RepeatedCompositeFieldContainer[Tag]
    def __init__(self, queries: _Optional[_Iterable[str]] = ..., insights: _Optional[_Iterable[_Union[ImportedInsight, _Mapping]]] = ..., tags: _Optional[_Iterable[_Union[Tag, _Mapping]]] = ...) -> None: ...

class ImportedInsight(_message.Message):
    __slots__ = ("title", "content")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    title: str
    content: str
    def __init__(self, title: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class ImportMemoriesResponse(_message.Message):
    __slots__ = ("results", "instructions")
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTIONS_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[ImportOutcome]
    instructions: Instructions
    def __init__(self, results: _Optional[_Iterable[_Union[ImportOutcome, _Mapping]]] = ..., instructions: _Optional[_Union[Instructions, _Mapping]] = ...) -> None: ...

class ImportOutcome(_message.Message):
    __slots__ = ("index", "status", "errors")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ERRORS_FIELD_NUMBER: _ClassVar[int]
    index: int
    status: ImportStatus
    errors: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, index: _Optional[int] = ..., status: _Optional[_Union[ImportStatus, str]] = ..., errors: _Optional[_Iterable[str]] = ...) -> None: ...
