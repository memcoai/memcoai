"""Shared fixtures: a real server, and clients pointed at it."""

from __future__ import annotations

import os

# Several tests close a channel while calls are in flight, which is exactly the
# race they exist to cover. gRPC's C core logs that to stderr at INFO, burying
# real output. Set before the core initialises on first channel creation.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

# A developer with MEMCO_LOG exported would otherwise have the SDK set
# propagate = False on import, and every caplog assertion in the suite would
# stop seeing records.
os.environ.pop("MEMCO_LOG", None)

import logging
from collections.abc import AsyncIterator, Iterator

import pytest

from memco import AsyncMemco, Memco

from .fake_server import Harness

TOKEN = "test-token"


@pytest.fixture(autouse=True)
def neutral_memco_logger() -> Iterator[None]:
    """Give each test a propagating ``memco`` logger, and put it back after.

    The SDK configures itself at INFO on import, which sets ``propagate =
    False``. caplog captures through the root logger, so leaving that in place
    would make every assertion about a record see nothing. Undoing it here is
    exactly what an application with its own logging is told to do, and
    restoring afterwards keeps one test's level from leaking into the next.
    """
    logger = logging.getLogger("memco")
    handlers = logger.handlers[:]
    level, propagate, disabled = logger.level, logger.propagate, logger.disabled
    logger.handlers[:] = [h for h in handlers if isinstance(h, logging.NullHandler)]
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    logger.disabled = False
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate
    logger.disabled = disabled


@pytest.fixture
def harness() -> Iterator[Harness]:
    """Start a real gRPC server for one test and stop it afterwards."""
    running = Harness()
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture
def client(harness: Harness) -> Iterator[Memco]:
    """A synchronous client connected to the fixture server over plaintext."""
    with Memco(token=TOKEN, host=harness.address, tls=False) as connected:
        _forget_the_construction_calls(harness)
        yield connected


@pytest.fixture
async def async_client(harness: Harness) -> AsyncIterator[AsyncMemco]:
    """An asynchronous client connected to the fixture server over plaintext."""
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    _forget_the_construction_calls(harness)
    try:
        yield connected
    finally:
        await connected.close()


def _forget_the_construction_calls(harness: Harness) -> None:
    """Clear what connecting recorded, so a test starts from a clean server.

    Connecting fetches the service's limits, so without this every test would
    open with a ``ListDomains`` already on the record — and the suite proves
    "this was rejected before any request was sent" by asserting the server saw
    no calls at all.
    """
    harness.memory.calls.clear()
    harness.memory.metadata.clear()
    harness.memory.requests.clear()
