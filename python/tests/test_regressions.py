"""Regressions found in review. Each test failed before its fix."""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import date

import grpc
import pytest
from grpc_health.v1 import health_pb2

from memco.client import AsyncClient, Client, errors
from memco.client import _requests as requests
from memco.client._config import DEFAULT_HOST, DEFAULT_PORT, resolve
from memco.client._convert import _to_date
from memco.client._provenance import parse
from memco.client.types import DataSource, Tag

from .conftest import TOKEN
from .fake_server import Harness

# --- M4: scope values were only checked for presence, never validated -----


@pytest.mark.parametrize("domain", ["   ", "d" * 65])
def test_search_validates_the_domain_it_was_given(client: Client, harness: Harness, domain):
    with pytest.raises(errors.MemcoInvalidRequestError, match="domain"):
        client.memory.search("q", domain=domain)
    assert harness.memory.calls == []


@pytest.mark.parametrize("session_id", ["   ", "s" * 65])
def test_create_validates_the_session_it_was_given(client: Client, harness: Harness, session_id):
    with pytest.raises(errors.MemcoInvalidRequestError, match="session_id"):
        client.memory.create_memory(query="q", title="t", content="c", session_id=session_id)
    assert harness.memory.calls == []


def test_search_validates_the_session_it_was_given(client: Client, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError, match="session_id"):
        client.memory.search("q", session_id="s" * 65)
    assert harness.memory.calls == []


# --- M5: a per-call timeout of 0 was silently replaced by the default -----


def test_zero_timeout_is_rejected_not_silently_widened(client: Client, harness: Harness):
    # Asking for an immediate deadline and getting 30s is the opposite of the
    # request, so it must be an error rather than a substitution.
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        client.memory.list_domains(timeout=0)
    assert harness.memory.calls == []


def test_negative_timeout_is_rejected(client: Client, harness: Harness):
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        client.memory.list_domains(timeout=-5)
    assert harness.memory.calls == []


async def test_zero_timeout_is_rejected_on_the_async_client(async_client: AsyncClient):
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        await async_client.memory.list_domains(timeout=0)


# --- M6: a one-shot iterable was validated and then sent empty ------------


def test_generator_sources_actually_reach_the_wire():
    request = requests.enrich_memory_request(
        memory_idx="new",
        session_id="session-a",
        title="t",
        content="c",
        tags=None,
        sources=(f"src-{i}" for i in range(3)),
        source=DataSource.AGENT,
    )
    assert list(request.sources) == ["src-0", "src-1", "src-2"]


def test_generator_tags_actually_reach_the_wire():
    request = requests.search_request(
        "q",
        domain="coding",
        session_id=None,
        tags=(Tag(type="language", value=v) for v in ("python", "go")),
    )
    assert [t.value for t in request.tags] == ["python", "go"]


# --- L8: date grammar differed between Python 3.10 and 3.11+ --------------


@pytest.mark.parametrize("value", ["20260826", "2026-W35-3", "2026-08-26T00:00:00"])
def test_only_the_documented_date_grammar_is_accepted(value):
    # date.fromisoformat widened in 3.11, so it gave different results per
    # interpreter for identical server output.
    assert _to_date(value) is None


def test_the_documented_date_grammar_still_parses():
    assert _to_date("2026-08-26") == date(2026, 8, 26)


# --- H2: a call racing close() escaped as a raw non-Memco exception -------


def test_calling_a_closed_client_raises_a_memco_error(harness: Harness):
    connected = Client(token=TOKEN, host=harness.address, tls=False)
    connected._closed = True  # the pre-check still guards this path
    with pytest.raises(errors.MemcoError):
        connected.memory.list_domains()
    connected.close()


def test_a_call_that_races_close_still_raises_a_memco_error(harness: Harness):
    # close() flips the flag and then tears the channel down, so a caller can
    # pass the pre-check and still invoke on a dying channel. The client
    # documents itself as thread-safe, so this is supported usage.
    escaped: list[BaseException] = []

    def hammer(connected: Client) -> None:
        for _ in range(40):
            try:
                connected.memory.list_domains()
            except errors.MemcoError:
                pass
            except BaseException as exc:
                escaped.append(exc)
                return

    for _ in range(5):
        connected = Client(token=TOKEN, host=harness.address, tls=False)
        threads = [threading.Thread(target=hammer, args=(connected,)) for _ in range(8)]
        for thread in threads:
            thread.start()
        connected.close()
        for thread in threads:
            thread.join(timeout=10)

    assert not escaped, f"non-Memco exception escaped: {escaped[0]!r}"


async def test_an_async_call_that_races_close_still_raises_a_memco_error(harness: Harness):
    connected = AsyncClient(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    await connected._channel.close(grace=None)
    with pytest.raises(errors.MemcoError):
        await connected.memory.list_domains()
    await connected.close()


# --- H1: the async channel bound to whatever loop existed at construction --

# Run in a subprocess: this is about event-loop lifecycle, so it must not share
# a loop (or grpc.aio's global state) with the rest of the suite. It also
# reproduces the real shape of the bug — a client built at module scope in a
# user's script, then handed to asyncio.run().
LOOP_SCRIPT = """
import asyncio
import sys

from memco.client import AsyncClient

client = AsyncClient(token="t", host=sys.argv[1], tls=False)   # no loop exists yet


async def main() -> int:
    async with client:
        return len((await client.memory.list_domains()).domains)


print("domains:", asyncio.run(main()))
"""

LATER_SCRIPT = """
import asyncio
import sys

from memco.client import AsyncClient

asyncio.run(asyncio.sleep(0))          # leaves no current loop behind
client = AsyncClient(token="t", host=sys.argv[1], tls=False)


async def main() -> str:
    async with client:
        return (await client.memory.start_session("coding")).session_id


print("session:", asyncio.run(main()))
"""


@pytest.mark.parametrize(
    ("script", "expected"),
    [(LOOP_SCRIPT, "domains: 0"), (LATER_SCRIPT, "session: session-a")],
    ids=["constructed-before-any-loop", "constructed-after-a-loop-closed"],
)
def test_async_client_is_not_bound_to_the_wrong_event_loop(
    harness: Harness, tmp_path, script, expected
):
    source = tmp_path / "script.py"
    source.write_text(script)
    finished = subprocess.run(  # noqa: S603 - the interpreter and script are ours
        [sys.executable, str(source), harness.address],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert expected in finished.stdout
    # A channel bound to a dead loop leaves an un-awaited task behind.
    assert "was never awaited" not in finished.stderr
    assert "attached to a different loop" not in finished.stderr


# --- review: a blank host fell through to the production endpoint ---------


def test_blank_host_is_rejected_not_silently_defaulted():
    # `host=os.environ.get("MY_HOST", "")` and an unset CI variable both render
    # as "". Falling through to the production default would send a live token
    # somewhere the caller did not choose.
    with pytest.raises(errors.MemcoConfigError, match="host"):
        resolve(token="t", host="   ", env={})


def test_blank_host_env_var_is_treated_as_unset():
    cfg = resolve(token="t", env={"MEMCO_API_HOST": ""})
    assert cfg.host == DEFAULT_HOST


# --- review: IPv6 literals were rejected or silently mis-parsed -----------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("[2001:db8::1]", ("2001:db8::1", DEFAULT_PORT)),
        ("[2001:db8::1]:50051", ("2001:db8::1", 50051)),
        ("[::1]", ("::1", DEFAULT_PORT)),
        ("2001:db8::1", ("2001:db8::1", DEFAULT_PORT)),
        ("::1", ("::1", DEFAULT_PORT)),
    ],
)
def test_ipv6_hosts_parse(host, expected):
    cfg = resolve(token="t", host=host, env={})
    assert (cfg.host, cfg.port) == expected


def test_ipv4_and_names_still_parse():
    assert resolve(token="t", host="localhost:50051", env={}).port == 50051
    assert resolve(token="t", host="10.0.0.1:443", env={}).host == "10.0.0.1"
    assert resolve(token="t", host="example.test", env={}).port == DEFAULT_PORT


# --- review: an RpcError whose code() returns None escaped untyped --------


def test_an_rpc_error_with_no_code_still_becomes_a_memco_error():
    class Codeless(grpc.RpcError):  # type: ignore[misc]
        def code(self):
            return None

        def details(self):
            return "no code"

    translated = errors.from_rpc_error(Codeless())
    assert isinstance(translated, errors.MemcoAPIError)
    assert str(translated)


# --- review: connect() closed the client permanently on a failed probe ----


async def test_connect_can_be_retried_after_a_transient_failure(harness: Harness):
    connected = AsyncClient(token=TOKEN, host=harness.address, tls=False)
    harness.health.status = health_pb2.HealthCheckResponse.NOT_SERVING
    with pytest.raises(errors.MemcoUnhealthyError):
        await connected.connect()

    # The docstring promises a repeat simply repeats the checks.
    harness.health.status = health_pb2.HealthCheckResponse.SERVING
    await connected.connect()
    await connected.memory.list_domains()
    await connected.close()


# --- review: quota and rate-limit markers overlapped ---------------------


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("rate limit exceeded", errors.ResourceExhaustedKind.RATE_LIMIT),
        ("You've reached your per-minute request limit", errors.ResourceExhaustedKind.RATE_LIMIT),
        ("You've reached your daily search limit", errors.ResourceExhaustedKind.QUOTA),
        ("you have reached your daily rate limit", errors.ResourceExhaustedKind.QUOTA),
        ("monthly quota exhausted", errors.ResourceExhaustedKind.QUOTA),
        ("something else", errors.ResourceExhaustedKind.UNKNOWN),
    ],
)
def test_rate_limit_and_quota_are_told_apart(message, kind):
    class Exhausted(grpc.RpcError):  # type: ignore[misc]
        def code(self):
            return grpc.StatusCode.RESOURCE_EXHAUSTED

        def details(self):
            return message

    translated = errors.from_rpc_error(Exhausted())
    assert isinstance(translated, errors.MemcoResourceExhaustedError)
    assert translated.kind is kind


# --- review: a flush-left protos list read as empty -----------------------


def test_a_flush_left_protos_list_parses():
    # PyYAML's default dump style writes sequences at the parent's indent.
    parsed = parse("server_commit: c\nprotos:\n- path: a.proto\n  sha256: b\n")
    assert [(r.path, r.sha256) for r in parsed.protos] == [("a.proto", "b")]


def test_extra_spaces_after_the_dash_parse():
    parsed = parse("server_commit: c\nprotos:\n  -   path: a.proto\n      sha256: b\n")
    assert parsed.protos[0].path == "a.proto"
