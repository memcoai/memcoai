"""Python SDK for Memco Shared Memory.

Memco Shared Memory is a persistent store your team and its agents share. This
package wraps the generated gRPC client with connection handling, credential
management, typed results and typed errors.

Getting started::

    from memco.client import Client

    with Client() as client:                       # reads MEMCO_API_TOKEN
        for domain in client.memory.list_domains().domains:
            print(domain.slug, "-", domain.summary)

        session = client.memory.start_session("coding")
        result = client.memory.search(
            "how should a client authenticate against the memory API",
            session_id=session.session_id,
        )
        for memory in result.memories:
            for insight in memory.insights:
                print(insight.title, insight.updated)

The memory operations live on ``client.memory``.

Everything is available asynchronously too, with the same method names::

    from memco.client import AsyncClient

    async with AsyncClient() as client:
        session = await client.memory.start_session("coding")

Configuration comes from arguments first and the environment second:

* ``MEMCO_API_TOKEN`` — the credential, either a static API key or a WorkOS JWT.
  The older ``MEMCO_API_KEY`` still works but warns.
* ``MEMCO_API_HOST`` — the endpoint, defaulting to ``grpc.spark.memco.ai:443``.

Every failure is a subclass of :class:`~memco.client.errors.MemcoError`, so no
raw :class:`grpc.RpcError` ever reaches a caller.
"""

from __future__ import annotations

from importlib.metadata import version as _metadata_version

from . import errors, types
from ._aio import AsyncClient
from ._config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT
from ._provenance import provenance
from ._sync import Client
from ._validate import (
    MAX_FEEDBACK,
    MAX_IDX,
    MAX_QUERY,
    MAX_SOURCES,
    MAX_TEXT,
    NEW_MEMORY,
)
from .errors import (
    MemcoAPIError,
    MemcoAuthenticationError,
    MemcoConfigError,
    MemcoError,
    MemcoInternalError,
    MemcoInvalidRequestError,
    MemcoNotFoundError,
    MemcoPermissionError,
    MemcoResourceExhaustedError,
    MemcoTimeoutError,
    MemcoUnavailableError,
    MemcoUnhealthyError,
    ResourceExhaustedKind,
)
from .types import (
    DataSource,
    DomainEntry,
    DomainList,
    FeedbackEntry,
    FeedbackRating,
    FeedbackResult,
    Insight,
    Instructions,
    Memory,
    ProtoRecord,
    Provenance,
    RevertOutcome,
    RevertResult,
    SearchResult,
    Session,
    Tag,
    WriteResult,
)

# Read from the installed metadata so there is one source of truth: a release
# that bumps pyproject.toml cannot leave this behind.
__version__ = _metadata_version("memco")

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "MAX_FEEDBACK",
    "MAX_IDX",
    "MAX_QUERY",
    "MAX_SOURCES",
    "MAX_TEXT",
    "NEW_MEMORY",
    "AsyncClient",
    "Client",
    "DataSource",
    "DomainEntry",
    "DomainList",
    "FeedbackEntry",
    "FeedbackRating",
    "FeedbackResult",
    "Insight",
    "Instructions",
    "MemcoAPIError",
    "MemcoAuthenticationError",
    "MemcoConfigError",
    "MemcoError",
    "MemcoInternalError",
    "MemcoInvalidRequestError",
    "MemcoNotFoundError",
    "MemcoPermissionError",
    "MemcoResourceExhaustedError",
    "MemcoTimeoutError",
    "MemcoUnavailableError",
    "MemcoUnhealthyError",
    "Memory",
    "ProtoRecord",
    "Provenance",
    "ResourceExhaustedKind",
    "RevertOutcome",
    "RevertResult",
    "SearchResult",
    "Session",
    "Tag",
    "WriteResult",
    "__version__",
    "errors",
    "provenance",
    "types",
]
