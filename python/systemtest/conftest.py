"""Fixtures for the system test: a live client, and the domains to run against.

Unlike `tests/`, nothing here is faked. The suite needs `MEMCO_API_TOKEN` and a
reachable service; without a credential it reports itself skipped rather than
failing, so `make system-test` is safe to run offline and on a fork's CI.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from functools import cache

import pytest

from memco import Memco

TOKEN_ENV = "MEMCO_API_TOKEN"

NO_CREDENTIAL = f"{TOKEN_ENV} is not set: the system test needs a credential"


@cache
def _available_domains() -> tuple[str, ...]:
    """The domains this credential can reach, or nothing without one.

    Empty means "no credential", and nothing else. A credential that reaches no
    domain at all raises instead, so a service that has stopped listing them
    cannot be mistaken here for a contributor who has no token.
    """
    if not os.environ.get(TOKEN_ENV):
        return ()
    with Memco() as client:
        slugs = tuple(entry.slug for entry in client.memory.list_domains().domains)
    if not slugs:
        raise RuntimeError("the service returned no domains")
    return slugs


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Run every test asking for a `domain` once per domain the service lists.

    The lifecycle is exercised in each domain separately, so a domain that is
    configured differently from the others fails on its own row rather than
    hiding behind the first one that happened to work.
    """
    if "domain" not in metafunc.fixturenames:
        return
    domains = _available_domains()
    if not domains:
        skip = pytest.mark.skip(reason=NO_CREDENTIAL)
        metafunc.parametrize("domain", [pytest.param("", marks=skip)])
        return
    metafunc.parametrize("domain", domains)


@pytest.fixture(scope="session")
def client() -> Iterator[Memco]:
    """One live client for the whole session.

    Construction is itself the first assertion: it resolves the credential,
    negotiates TLS, probes health and calls ListDomains, so a broken endpoint or
    a rejected token fails here rather than inside a test.

    The credential is checked before constructing rather than left to the
    parametrisation: a test that takes this fixture but is not parametrised over
    domains would otherwise reach the constructor and fail where it should skip.
    """
    if not os.environ.get(TOKEN_ENV):
        pytest.skip(NO_CREDENTIAL)
    with Memco() as memco:
        yield memco


@pytest.fixture
def nonce(domain: str) -> str:
    """A token unique to this run, this domain and this SDK.

    It goes into the query, the title and the body of everything the test
    writes, which is what lets the test find its own memory again and what keeps
    it from colliding with the Node.js suite: the two run concurrently against
    the same organisation, and near-identical content would be folded together
    by the service's deduplication.
    """
    run = os.environ.get("GITHUB_RUN_ID")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    tail = f"{run}-{attempt}" if run else secrets.token_hex(4)
    return f"pysys-{domain}-{tail}"


@pytest.fixture
def written(client: Memco) -> Iterator[list[str]]:
    """Operation ids the test minted, reverted on the way out whatever happens.

    The test reverts them itself and asserts on the outcome; this is the safety
    net for a run that fails somewhere in between. Revert is idempotent — a
    second one reports NOT_FOUND, which is a successful call — so reverting
    everything unconditionally costs an RPC and never an error.
    """
    ids: list[str] = []
    yield ids
    for operation_id in reversed(ids):
        try:
            client.memory.revert_memory(operation_id)
        # Broad on purpose: cleanup runs after a failure and must not
        # replace the real one with an error of its own.
        except Exception as exc:
            print(f"cleanup: reverting {operation_id} failed: {exc}")
