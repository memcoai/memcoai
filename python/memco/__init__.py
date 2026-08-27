"""Python SDK for Memco Shared Memory.

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share. An agent searches it before starting work and writes back what it
learned when it finishes, so what one agent establishes, every teammate's agent
can find.

Getting started::

    from memco import Memco

    with Memco() as client:                       # reads MEMCO_API_TOKEN
        session = client.memory.start_session("coding")
        result = client.memory.search(
            "how should a client authenticate against the memory API",
            session_id=session.session_id,
        )
        for memory in result.memories:
            for insight in memory.insights:
                print(insight.title, insight.updated)

Everything is available asynchronously too, with the same method names::

    from memco import AsyncMemco

    async with AsyncMemco() as client:
        session = await client.memory.start_session("coding")

The rest of the surface is grouped by concern:

* :mod:`memco.types` — the immutable result types every operation returns
* :mod:`memco.errors` — the exception hierarchy
* :mod:`memco.operations` — the operation namespaces reached as ``client.memory``
* :mod:`memco.agent` — the same operations as agent tools, with schemas and
  rendered results

Configuration comes from arguments first and the environment second:

* ``MEMCO_API_TOKEN`` — the credential: an API key or a session token.
  The older ``MEMCO_API_KEY`` still works but warns.
* ``MEMCO_API_HOST`` — the endpoint, defaulting to ``grpc.spark.memco.ai:443``.

Every failure is a subclass of :class:`memco.errors.MemcoError`, so no raw
:class:`grpc.RpcError` ever reaches a caller.
"""

from __future__ import annotations

from importlib.metadata import version as _metadata_version

from . import agent, errors, operations, types
from ._aio import AsyncMemco
from ._config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT
from ._deprecation import MemcoDeprecationWarning
from ._provenance import provenance
from ._sync import Memco
from ._validate import NEW_MEMORY

# Read from the installed metadata so there is one source of truth: a release
# that bumps pyproject.toml cannot leave this behind.
__version__ = _metadata_version("memco")

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "NEW_MEMORY",
    "AsyncMemco",
    "Memco",
    "MemcoDeprecationWarning",
    "__version__",
    "agent",
    "errors",
    "operations",
    "provenance",
    "types",
]
