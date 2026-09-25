"""Sessions acting as an external user, against the real server.

An API client places its own users in customer networks, then opens memory
sessions as them. The fake server in `tests/` already proves which key each
call carries; what only the real service can prove is what those keys do. A
write made through a session is the user's: the user finds it, and the user
alone can revert it, since a revert answers only for its caller's own writes.
Two sessions running at once each act as their own user, each in a network of
its own, so a crossed key would show as one finding what the other wrote.
And closing a session really revokes its key.

The memories written follow the lifecycle suite's rules, for the same reasons:
the run marker lives only in the query, which becomes the memory's intent, and
the insight is plain prose about this SDK, which the quality gate accepts. Each
subject is distinct, so no write reads as the same knowledge as another. A
write a failing test leaves behind goes with its customer network, whose
deletion takes the memories placed in it.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable

import pytest

from memcoai import AsyncMemco, Memco
from memcoai._auth import Minted
from memcoai.admin.v1 import admin_pb2 as admin_pb
from memcoai.errors import MemcoAuthenticationError, MemcoNotFoundError
from memcoai.operations import AsyncSession, Session
from memcoai.types import AsyncMemory, DataSource, ExternalUser, Memory, Network, RevertOutcome

from .conftest import CLIENT_ID_ENV, CLIENT_SECRET_ENV
from .test_lifecycle import INGEST_TIMEOUT, poll_waits

OPENING = {
    "query": "How does the Memco Python SDK open a session as one of an organisation's own users?",
    "title": "Python SDK impersonated sessions",
    "content": (
        "The Memco Python SDK opens a memory session as one of an organisation's own users "
        "when `start_session` or `with_session` is given an `external_id`. It mints an "
        "impersonation key under the API client's token, lists the domains under that key "
        "so the session learns its limits as that user, and only then starts the session.\n\n"
        "Every call made through the session carries that key rather than the client's "
        "token. Closing the session, or leaving its `with` block, ends the key on the "
        "service instead of leaving it live until it expires."
    ),
}
RENEWAL = {
    "query": "When does the Memco Python SDK renew an impersonation key?",
    "title": "Python SDK impersonation key renewal",
    "content": (
        "The Memco Python SDK renews an impersonation key once four fifths of its lifetime "
        "have passed. A burst of calls arriving at that point mints a single new key, and "
        "the key it replaces is ended only after the last call still carrying it has "
        "finished, so a renewal never revokes a key under a call in flight."
    ),
}
SWEEP = {
    "query": "What happens to open impersonation keys when a Memco Python SDK client closes?",
    "title": "Python SDK impersonation key cleanup",
    "content": (
        "Closing a Memco Python SDK client ends every impersonation key a session left open. "
        "It waits for calls in flight to finish, then ends each key under the client's own "
        "token, counting a key the service reports as already gone as ended. A key the "
        "service cannot end is logged by its id, never its value, and expires on its own."
    ),
}


def _asked(fields: dict[str, str], marker: str) -> dict[str, str]:
    """The fields to write, with the run marker added to the query alone."""
    return {**fields, "query": f"{fields['query']} (system test {marker})"}


def _carries(memory: Memory | AsyncMemory, marker: str) -> bool:
    """Whether a memory was written with this marker in its query.

    The lifecycle suite's test, for either client's memories: the query passed
    to create_memory becomes one of the memory's intents.
    """
    return any(marker in intent for intent in memory.intents)


def _placed(
    admin: Memco,
    customer_networks: Callable[[], Network],
    external_users: Callable[..., ExternalUser],
) -> tuple[str, str]:
    """A new external user, placed in a customer network of its own.

    Returns:
        The network's domain, and the user's external id, which doubles as the
        marker of what the user writes: no other run or user carries it.
    """
    network, user = customer_networks(), external_users()
    admin.networks.add_member(network.id, user.id)
    return network.domain, user.external_id


def _record_keys(monkeypatch: pytest.MonkeyPatch, client: Memco) -> list[Minted]:
    """Collect every impersonation key the client mints from here on.

    Private access, deliberately: the SDK exposes a key's value nowhere, which
    is the point of it, and only the value lets the test ask the service itself
    whether the key still works once its session is closed. Each is kept as the
    SDK holds it, whose repr leaves the value out, so an assertion pytest
    explains on failure cannot print a live key into the CI log.
    """
    keys: list[Minted] = []
    mint = client._mint  # noqa: SLF001 - see above

    def recorded(external_id: str) -> Minted:
        key = mint(external_id)
        keys.append(key)
        return key

    monkeypatch.setattr(client, "_mint", recorded)
    return keys


def _record_async_keys(monkeypatch: pytest.MonkeyPatch, client: AsyncMemco) -> list[Minted]:
    """The asyncio twin of `_record_keys`."""
    keys: list[Minted] = []
    mint = client._mint  # noqa: SLF001 - see _record_keys

    async def recorded(external_id: str) -> Minted:
        key = await mint(external_id)
        keys.append(key)
        return key

    monkeypatch.setattr(client, "_mint", recorded)
    return keys


def _found(session: Session, query: str, marker: str) -> None:
    """Poll the session's search until the memory carrying the marker comes back."""
    deadline = time.monotonic() + INGEST_TIMEOUT
    waits = poll_waits()
    while time.monotonic() < deadline:
        if any(_carries(memory, marker) for memory in session.search(query).memories):
            return
        print("  waiting for ingestion")
        time.sleep(next(waits))
    pytest.fail(f"{marker}'s memory never became searchable within {INGEST_TIMEOUT:.0f}s")


async def _found_alone(session: AsyncSession, query: str, marker: str, other: str) -> None:
    """Poll as `_found` does, failing the moment a search returns the other user's memory."""
    deadline = time.monotonic() + INGEST_TIMEOUT
    waits = poll_waits()
    while time.monotonic() < deadline:
        memories = (await session.search(query)).memories
        assert not any(_carries(memory, other) for memory in memories), (
            f"{marker}'s session found what {other} wrote, in a network {marker} is not in"
        )
        if any(_carries(memory, marker) for memory in memories):
            return
        print(f"  waiting for ingestion of {marker}'s memory")
        await asyncio.sleep(next(waits))
    pytest.fail(f"{marker}'s memory never became searchable within {INGEST_TIMEOUT:.0f}s")


async def _act(
    client: AsyncMemco, domain: str, user: str, fields: dict[str, str], other: str
) -> None:
    """Write, find and revert a memory as one user, never seeing the other's."""
    async with client.memory.with_session(domain, external_id=user) as session:
        before = (await session.search(fields["query"])).memories
        assert not any(_carries(memory, user) for memory in before)
        write = await session.create_memory(
            query=fields["query"],
            title=fields["title"],
            content=fields["content"],
            source=DataSource.AGENT,
        )
        assert write.operation_id, "the write was accepted without an operation id to revert"
        await _found_alone(session, fields["query"], user, other)
        undo = await session.revert_memory(write.operation_id)
        assert undo.outcome is RevertOutcome.MEMORY_REMOVED, (
            f"reverting as {user}, who wrote it, reported {undo.outcome.name}"
        )


def test_a_session_acts_as_an_external_user_and_its_key_ends_with_it(
    admin: Memco,
    customer_networks: Callable[[], Network],
    external_users: Callable[..., ExternalUser],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write, find and revert a memory as an external user, then find the key refused."""
    domain, user = _placed(admin, customer_networks, external_users)
    keys = _record_keys(monkeypatch, admin)
    fields = _asked(OPENING, user)
    print(f"\n[{domain}] acting as {user}")

    with admin.memory.with_session(domain, external_id=user) as session:
        before = session.search(fields["query"]).memories
        assert not any(_carries(memory, user) for memory in before)
        write = session.create_memory(
            query=fields["query"],
            title=fields["title"],
            content=fields["content"],
            source=DataSource.AGENT,
        )
        assert write.operation_id, "the write was accepted without an operation id to revert"
        _found(session, fields["query"], user)
        undo = session.revert_memory(write.operation_id)
        assert undo.outcome is RevertOutcome.MEMORY_REMOVED, (
            f"reverting as the user who wrote it reported {undo.outcome.name}"
        )
        # The key works while its session is open, so the refusal below is
        # the close's doing rather than a key that never worked from outside.
        with Memco(token=keys[0].value):
            pass

    assert len(keys) == 1, f"one session minted {len(keys)} keys"
    with pytest.raises(MemcoAuthenticationError):
        Memco(token=keys[0].value)
    # Ending an ended key is answered NOT_FOUND, which the SDK counts as
    # ended already. Any other answer would have every session closed after
    # its key ran out log a warning, and stop the client's sweep at that key.
    with pytest.raises(MemcoNotFoundError):
        admin._call(  # noqa: SLF001 - the SDK ends only the keys it still holds
            admin._admin.EndImpersonation,  # noqa: SLF001
            admin_pb.EndImpersonationRequest(external_id=user, key_id=keys[0].key_id),
            None,
        )


async def test_two_users_act_at_once_each_under_a_key_of_its_own(
    admin: Memco,
    customer_networks: Callable[[], Network],
    external_users: Callable[..., ExternalUser],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent sessions each find only their own user's write, and each key ends."""
    domain, first = _placed(admin, customer_networks, external_users)
    _, second = _placed(admin, customer_networks, external_users)
    print(f"\n[{domain}] acting as {first} and {second} at once")

    async with AsyncMemco(
        client_id=os.environ[CLIENT_ID_ENV], client_secret=os.environ[CLIENT_SECRET_ENV]
    ) as client:
        keys = _record_async_keys(monkeypatch, client)
        await asyncio.gather(
            _act(client, domain, first, _asked(RENEWAL, first), second),
            _act(client, domain, second, _asked(SWEEP, second), first),
        )
        # One key per session, each checked before the client closes, so what
        # ended it is the close of its own session rather than the client's sweep.
        assert len(keys) == len((first, second)), f"two sessions minted {len(keys)} keys"
        for key in keys:
            with pytest.raises(MemcoAuthenticationError):
                await AsyncMemco(token=key.value).connect()
