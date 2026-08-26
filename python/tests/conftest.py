"""Shared fixtures: a real server, and clients pointed at it."""

from __future__ import annotations

import os

# Several tests close a channel while calls are in flight, which is exactly the
# race they exist to cover. gRPC's C core logs that to stderr at INFO, burying
# real output. Set before the core initialises on first channel creation.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

from collections.abc import AsyncIterator, Iterator

import pytest

from memco import AsyncMemco, Memco

from .fake_server import Harness

TOKEN = "test-token"


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
        yield connected


@pytest.fixture
async def async_client(harness: Harness) -> AsyncIterator[AsyncMemco]:
    """An asynchronous client connected to the fixture server over plaintext."""
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    try:
        yield connected
    finally:
        await connected.close()
