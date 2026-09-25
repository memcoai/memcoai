"""Fixtures for the system test: live clients, and the domains to run against.

Unlike `tests/`, nothing here is faked. The memory suite needs `MEMCO_API_TOKEN`
and a reachable service; the administration and impersonation suites need an API
client's `MEMCO_CLIENT_ID` and `MEMCO_CLIENT_SECRET` instead. Without its
credential each reports itself skipped rather than failing, so `make system-test`
is safe to run offline and on a fork's CI.

`MEMCO_API_TLS=false` dials without TLS, for a local development server such
as `localhost:50052`: every client here takes it from the environment.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Iterable, Iterator
from functools import cache

import pytest

from memcoai import Memco
from memcoai.errors import MemcoNotFoundError
from memcoai.types import ExternalUser, Network

TOKEN_ENV = "MEMCO_API_TOKEN"
CLIENT_ID_ENV = "MEMCO_CLIENT_ID"
CLIENT_SECRET_ENV = "MEMCO_CLIENT_SECRET"

NO_CREDENTIAL = f"{TOKEN_ENV} is not set: the system test needs a credential"
NO_CLIENT = (
    f"{CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} are not both set: "
    "administration and impersonation need an API client"
)


def _run() -> str:
    """A tag naming this run: the CI run and attempt, or random bytes locally."""
    run = os.environ.get("GITHUB_RUN_ID")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return f"{run}-{attempt}" if run else secrets.token_hex(4)


def _unique() -> str:
    """A name no other run, and no other object of this run, carries.

    Names the networks and users the suite creates. In CI it names the run
    too, so one a failed cleanup left behind can be traced to the run that
    made it.
    """
    return f"pysys-{_run()}-{secrets.token_hex(3)}"


@cache
def _available_domains() -> tuple[str, ...]:
    """The domains this credential can reach, or nothing without one.

    Empty means "no credential", and nothing else. A credential that reaches no
    domain at all raises instead, so a service that has stopped listing them
    cannot be mistaken here for a contributor who has no token.
    """
    if not os.environ.get(TOKEN_ENV):
        return ()
    # Passed explicitly, here and in `client`: CI sets the API client's
    # variables beside the token, and those win over MEMCO_API_TOKEN for a
    # client given no credential argument.
    with Memco(token=os.environ[TOKEN_ENV]) as client:
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
    with Memco(token=os.environ[TOKEN_ENV]) as memco:
        yield memco


@pytest.fixture(scope="session")
def admin() -> Iterator[Memco]:
    """One live client authenticated as an API client, for the whole session.

    Construction exchanges the credentials for a token, so a rejected pair
    fails here rather than inside a test. They are passed explicitly, as the
    token is to the memory client, so which credential each client holds is
    written here rather than decided by the environment's precedence.
    """
    client_id = os.environ.get(CLIENT_ID_ENV)
    client_secret = os.environ.get(CLIENT_SECRET_ENV)
    if not (client_id and client_secret):
        pytest.skip(NO_CLIENT)
    with Memco(client_id=client_id, client_secret=client_secret) as memco:
        yield memco


@pytest.fixture(scope="session")
def root_network(admin: Memco) -> Network:
    """The root network the suite creates its customer networks under.

    There is one root per memory domain, and the first the service lists is
    taken: its domain is the one the administration and impersonation tests run
    in. Not the `domain` parametrisation the memory tests use, because that
    lists domains with `MEMCO_API_TOKEN`, which these tests do not otherwise
    need, and because what they need of a domain is a root to build under,
    which only the network listing can promise.
    """
    roots = admin.networks.list(parent_id="root").networks
    if not roots:
        raise RuntimeError("the organisation has no root network to create customer networks under")
    return roots[0]


@pytest.fixture
def customer_networks(admin: Memco, root_network: Network) -> Iterator[Callable[[], Network]]:
    """Create customer networks under the root, deleting each on the way out.

    A factory, because a test acting as two users gives each a network of its
    own, so neither can find what the other wrote by any route but a crossed
    key. A customer network is the only kind an external user can be placed in.

    Deleting a network takes everything placed in it with it, the memories its
    members wrote included, so this is also the safety net for a write an
    impersonated session could not revert. A test that deleted the network
    itself leaves nothing to delete, which is not a failure.
    """
    names: list[str] = []

    def create() -> Network:
        name = _unique()
        # Recorded before the create is sent, as `external_users` records its
        # ids: a create the service commits but the client sees fail, such as
        # one whose answer arrives after the deadline, still made a network.
        names.append(name)
        return admin.networks.create(
            name=name,
            parent_id=root_network.id,
            scope="customer",
            description="A network the Python SDK system test creates and deletes again.",
        )

    yield create
    for name in reversed(names):
        try:
            # Found by name, since a create seen to fail returned no id.
            found = admin.networks.list(name=name, parent_id=root_network.id).networks
            for network in found:
                if network.name == name:
                    admin.networks.delete(network.id)
        except MemcoNotFoundError:
            pass
        # Broad on purpose, as in `written`: cleanup runs after a failure and
        # must not replace the real one with an error of its own.
        except Exception as exc:
            print(f"cleanup: deleting network {name} failed: {exc}")


@pytest.fixture
def external_users(admin: Memco) -> Iterator[Callable[..., ExternalUser]]:
    """Create external users, deleting each on the way out.

    A factory for the same reason as `customer_networks`. Each user is a
    creator unless the test asks for other roles, and each is recorded before
    the create is sent, so a user the service ought to have refused but created
    anyway is still removed. Deleting a user deletes every key it holds.
    """
    created: list[str] = []

    def create(roles: Iterable[str] = ("creator",)) -> ExternalUser:
        external_id = _unique()
        created.append(external_id)
        return admin.users.create(external_id, roles=roles)

    yield create
    for external_id in reversed(created):
        try:
            admin.users.delete(external_id)
        except MemcoNotFoundError:
            pass
        except Exception as exc:
            print(f"cleanup: deleting external user {external_id} failed: {exc}")


@pytest.fixture
def nonce(domain: str) -> str:
    """A token unique to this run, this domain and this SDK.

    It goes into the query, the title and the body of everything the test
    writes, which is what lets the test find its own memory again and what keeps
    it from colliding with the Node.js suite: the two run concurrently against
    the same organisation, and near-identical content would be folded together
    by the service's deduplication.
    """
    return f"pysys-{domain}-{_run()}"


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
