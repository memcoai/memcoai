"""Python SDK for Memco Shared Memory.

Memco Shared Memory is a persistent, searchable memory that your team and its AI
agents share. An agent searches it before starting work and writes back what it
learned when it finishes, so what one agent establishes, every teammate's agent
can find.

Getting started::

    from memcoai import Memco

    with Memco() as client:                       # reads MEMCO_API_TOKEN
        session = client.memory.start_session("coding")
        result = session.search("how should a client authenticate against the memory API")
        for memory in result.memories:
            for insight in memory.insights:
                print(insight.title, insight.updated)

Everything is available asynchronously too, with the same method names::

    from memcoai import AsyncMemco

    async with AsyncMemco() as client:
        session = await client.memory.start_session("coding")

The rest of the surface is grouped by concern:

* :mod:`memcoai.types` — the immutable result types every operation returns
* :mod:`memcoai.errors` — the exception hierarchy
* :mod:`memcoai.operations` — the operation namespaces reached as ``client.memory``
* :mod:`memcoai.agent` — the same operations as agent tools, with schemas and
  rendered results

Configuration comes from arguments first and the environment second:

* ``MEMCO_API_TOKEN`` — the credential: an API key or a session token.
  The older ``MEMCO_API_KEY`` still works but warns.
* ``MEMCO_API_HOST`` — the endpoint, defaulting to ``grpc.memco.ai:443``.
* ``MEMCO_LOG`` — the SDK's log level, defaulting to ``info``: ``debug``,
  ``info``, ``warning``, ``error``, ``critical``, or ``none`` to turn it off.
  The ``log_level`` argument on either client does the same and wins over it.

Every failure is a subclass of :class:`memcoai.errors.MemcoError`, so no raw
:class:`grpc.RpcError` ever reaches a caller.

Everything the SDK logs goes to a logger under ``memcoai`` — ``memcoai._sync``,
``memcoai._channel`` and so on — so configuring ``memcoai`` governs all of it::

    logging.getLogger("memcoai").setLevel(logging.DEBUG)

The SDK configures itself at ``info`` on import, reporting when a client
connects and closes; ``debug`` adds every RPC and where the endpoint and
credential came from. An application with its own logging should turn the SDK's
own handler off with ``log_level="none"`` and set the level on the ``memcoai``
logger instead. No credential is ever written to a record.
"""

from __future__ import annotations

from importlib.metadata import version as _metadata_version

from . import agent, errors, operations, types
from ._aio import AsyncMemco
from ._config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT
from ._deprecation import MemcoDeprecationWarning
from ._logging import configure as _configure_logging
from ._provenance import provenance
from ._sync import Memco
from ._validate import NEW_MEMORY

# Attach the null handler that keeps a library quiet, and apply MEMCO_LOG if
# the caller set it. Done at import so the first record the SDK writes — which
# may be during the very first client construction — already goes somewhere.
_configure_logging()

# Read from the installed metadata so there is one source of truth: a release
# that bumps pyproject.toml cannot leave this behind.
__version__ = _metadata_version("memcoai")

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
