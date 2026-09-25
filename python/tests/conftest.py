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
import time
from collections.abc import AsyncIterator, Iterator

import pytest

from memcoai import AsyncMemco, Memco, _auth

from .fake_server import Harness

TOKEN = "test-token"

CLIENT_ID = "client-test"
CLIENT_SECRET = "client-secret-test"
"""The API-client credentials the ``credentialed`` clients are built with.

The secret is distinctive enough that finding it in a log or an error can only
mean it leaked there.
"""


@pytest.fixture(autouse=True)
def neutral_memco_logger() -> Iterator[None]:
    """Give each test a propagating ``memcoai`` logger, and put it back after.

    The SDK configures itself at INFO on import, which sets ``propagate =
    False``. caplog captures through the root logger, so leaving that in place
    would make every assertion about a record see nothing. Undoing it here is
    exactly what an application with its own logging is told to do, and
    restoring afterwards keeps one test's level from leaking into the next.
    """
    logger = logging.getLogger("memcoai")
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


@pytest.fixture
def credentialed(harness: Harness) -> Iterator[Memco]:
    """A synchronous client holding API-client credentials rather than a token."""
    with Memco(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, host=harness.address, tls=False
    ) as connected:
        _forget_the_construction_calls(harness)
        yield connected


@pytest.fixture
async def async_credentialed(harness: Harness) -> AsyncIterator[AsyncMemco]:
    """An asynchronous client holding API-client credentials rather than a token."""
    connected = AsyncMemco(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, host=harness.address, tls=False
    )
    await connected.connect()
    _forget_the_construction_calls(harness)
    try:
        yield connected
    finally:
        await connected.close()


def _forget_the_construction_calls(harness: Harness) -> None:
    """Clear what connecting recorded, so a test starts from a clean server.

    Connecting fetches the service's limits, or issues a token when the client
    holds API-client credentials, so without this every test would open with
    that call already on the record — and the suite proves "this was rejected
    before any request was sent" by asserting the server saw no calls at all.
    """
    for recorder in (harness.memory, harness.tokens, harness.admin):
        recorder.clear()


class FakeClock:
    """A clock that moves only when a test moves it.

    Stands in for both clocks the SDK schedules renewal by: the monotonic one a
    renewal point is measured on, and the wall clock an impersonation key's
    absolute expiry is read against. They move together.

    Both start at whole seconds of the real time. Real, so a renewal point
    computed before the clock was patched still lines up with it; whole, so a
    test stepping to either side of a renewal point lands exactly where it
    means to.
    """

    def __init__(self) -> None:
        self.now = float(int(time.monotonic()))
        self.wall = float(int(time.time()))

    def monotonic(self) -> float:
        """Stand in for :func:`time.monotonic`."""
        return self.now

    def time(self) -> float:
        """Stand in for :func:`time.time`."""
        return self.wall

    def advance(self, seconds: float) -> None:
        """Move both clocks forward.

        Args:
            seconds: How far.
        """
        self.now += seconds
        self.wall += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Put the SDK's renewal clocks in the test's hands.

    The SDK reads both through ``memcoai._auth``, so patching them there moves
    every renewal decision and nothing else: grpc's deadlines keep real time.
    """
    fake = FakeClock()
    monkeypatch.setattr(_auth, "monotonic", fake.monotonic)
    monkeypatch.setattr(_auth, "time", fake.time)
    return fake
