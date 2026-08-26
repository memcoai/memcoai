"""Regressions found in review. Each test failed before its fix."""

from __future__ import annotations

import asyncio
import copy
import pathlib
import pickle
import subprocess
import sys
import threading
import warnings
from concurrent import futures
from datetime import date

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

import memco
from memco import AsyncMemco, Memco, errors
from memco import _requests as requests
from memco._config import DEFAULT_HOST, DEFAULT_PORT, resolve
from memco._convert import _to_date
from memco._provenance import parse
from memco.memory.v1 import memory_pb2 as pb
from memco.types import DataSource, Tag

from .conftest import TOKEN
from .fake_server import FakeHealthService, Harness

# --- M4: scope values were only checked for presence, never validated -----


@pytest.mark.parametrize("domain", ["   "])
def test_search_validates_the_domain_it_was_given(client: Memco, harness: Harness, domain):
    with pytest.raises(errors.MemcoInvalidRequestError, match="domain"):
        client.memory.search("q", domain=domain)
    assert harness.memory.calls == []


@pytest.mark.parametrize("session_id", ["   "])
def test_create_validates_the_session_it_was_given(client: Memco, harness: Harness, session_id):
    with pytest.raises(errors.MemcoInvalidRequestError, match="session_id"):
        client.memory.create_memory(query="q", title="t", content="c", session_id=session_id)
    assert harness.memory.calls == []


def test_search_validates_the_session_it_was_given(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoInvalidRequestError, match="session_id"):
        client.memory.search("q", session_id="   ")
    assert harness.memory.calls == []


# --- M5: a per-call timeout of 0 was silently replaced by the default -----


def test_zero_timeout_is_rejected_not_silently_widened(client: Memco, harness: Harness):
    # Asking for an immediate deadline and getting 30s is the opposite of the
    # request, so it must be an error rather than a substitution.
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        client.memory.describe_domains(timeout=0)
    assert harness.memory.calls == []


def test_negative_timeout_is_rejected(client: Memco, harness: Harness):
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        client.memory.describe_domains(timeout=-5)
    assert harness.memory.calls == []


async def test_zero_timeout_is_rejected_on_the_async_client(async_client: AsyncMemco):
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        await async_client.memory.describe_domains(timeout=0)


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
    connected = Memco(token=TOKEN, host=harness.address, tls=False)
    connected._closed = True  # the pre-check still guards this path
    with pytest.raises(errors.MemcoError):
        connected.memory.describe_domains()
    connected.close()


RACE_SCRIPT = """
import threading

from memco import Memco, errors
from tests.fake_server import Harness

harness = Harness()
escaped = []
for _ in range(5):
    client = Memco(token="t", host=harness.address, tls=False)

    def hammer():
        for _ in range(30):
            # Four different methods: grpc registers a call handle per method,
            # and invoking an unwarmed one on a destroyed channel used to take
            # the whole process down rather than raise.
            for call in (
                lambda: client.memory.describe_domains(),
                lambda: client.memory.search("q", domain="d"),
                lambda: client.memory.get_memory("m"),
                lambda: client.memory.start_session("d"),
            ):
                try:
                    call()
                except errors.MemcoError:
                    pass
                except BaseException as exc:
                    escaped.append(repr(exc))
                    return

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    client.close()
    for thread in threads:
        thread.join(timeout=15)

harness.stop()
print("escaped:", escaped[:1] or "none")
"""


def test_closing_while_calls_are_in_flight_does_not_crash(tmp_path):
    # Run out of process: the failure mode this pins is a SIGSEGV, which would
    # take the whole suite down rather than fail one test.
    source = tmp_path / "race.py"
    source.write_text(RACE_SCRIPT)
    finished = subprocess.run(  # noqa: S603 - the interpreter and script are ours
        [sys.executable, str(source)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=pathlib.Path(__file__).parent.parent,
        check=False,
    )
    assert finished.returncode == 0, f"crashed ({finished.returncode}): {finished.stderr[-400:]}"
    assert "escaped: none" in finished.stdout, finished.stdout


def test_a_call_that_races_close_still_raises_a_memco_error(harness: Harness):
    # close() flips the flag and then tears the channel down, so a caller can
    # pass the pre-check and still invoke on a dying channel. The client
    # documents itself as thread-safe, so this is supported usage.
    escaped: list[BaseException] = []

    def hammer(connected: Memco) -> None:
        for _ in range(40):
            try:
                connected.memory.describe_domains()
            except errors.MemcoError:
                pass
            except BaseException as exc:
                escaped.append(exc)
                return

    for _ in range(5):
        connected = Memco(token=TOKEN, host=harness.address, tls=False)
        threads = [threading.Thread(target=hammer, args=(connected,)) for _ in range(8)]
        for thread in threads:
            thread.start()
        connected.close()
        for thread in threads:
            thread.join(timeout=10)

    assert not escaped, f"non-Memco exception escaped: {escaped[0]!r}"


async def test_an_async_call_that_races_close_still_raises_a_memco_error(harness: Harness):
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    await connected._channel.close(grace=None)
    with pytest.raises(errors.MemcoError):
        await connected.memory.describe_domains()
    await connected.close()


# --- H1: the async channel bound to whatever loop existed at construction --

# Run in a subprocess: this is about event-loop lifecycle, so it must not share
# a loop (or grpc.aio's global state) with the rest of the suite. It also
# reproduces the real shape of the bug — a client built at module scope in a
# user's script, then handed to asyncio.run().
LOOP_SCRIPT = """
import asyncio
import sys

from memco import AsyncMemco
client = AsyncMemco(token="t", host=sys.argv[1], tls=False)   # no loop exists yet


async def main() -> int:
    async with client:
        return len((await client.memory.describe_domains()).domains)


print("domains:", asyncio.run(main()))
"""

LATER_SCRIPT = """
import asyncio
import sys

from memco import AsyncMemco
asyncio.run(asyncio.sleep(0))          # leaves no current loop behind
client = AsyncMemco(token="t", host=sys.argv[1], tls=False)


async def main() -> str:
    async with client:
        return (await client.memory.start_session("coding")).session_id


print("session:", asyncio.run(main()))
"""


REUSE_SCRIPT = """
import asyncio
import sys

from memco import AsyncMemco

client = AsyncMemco(token="t", host=sys.argv[1], tls=False)


async def main() -> int:
    await client.connect()          # deliberately not closed between runs
    return len((await client.memory.describe_domains()).domains)


for _ in range(3):
    print("domains:", asyncio.run(main()))
"""


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (LOOP_SCRIPT, "domains: 0"),
        (LATER_SCRIPT, "session: session-a"),
        # A channel is bound to the loop that built it, so a client reused
        # across asyncio.run() calls has to rebuild rather than fail with a
        # bare RuntimeError("Event loop is closed").
        (REUSE_SCRIPT, "domains: 0"),
    ],
    ids=["constructed-before-any-loop", "constructed-after-a-loop-closed", "reused-across-loops"],
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
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    harness.health.status = health_pb2.HealthCheckResponse.NOT_SERVING
    with pytest.raises(errors.MemcoUnhealthyError):
        await connected.connect()

    # The docstring promises a repeat simply repeats the checks.
    harness.health.status = health_pb2.HealthCheckResponse.SERVING
    await connected.connect()
    await connected.memory.describe_domains()
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


# --- review: IPv6 host lost its brackets when rejoined with the port ------


@pytest.mark.parametrize(
    ("host", "target"),
    [
        ("[::1]:34059", "[::1]:34059"),
        ("[2001:db8::1]:50051", "[2001:db8::1]:50051"),
        ("[2001:db8::1]", "[2001:db8::1]:443"),
        ("2001:db8::1", "[2001:db8::1]:443"),
        ("localhost:50051", "localhost:50051"),
        ("example.test", "example.test:443"),
        ("10.0.0.1:443", "10.0.0.1:443"),
    ],
)
def test_the_dialled_target_is_well_formed(host, target):
    # _split_host_port strips the brackets; target has to put them back or the
    # address is unparseable and the failure is reported as a DNS error.
    assert resolve(token="t", host=host, env={}).target == target


async def test_an_ipv6_endpoint_can_actually_be_dialled():

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    health_pb2_grpc.add_HealthServicer_to_server(FakeHealthService(), server)
    port = server.add_insecure_port("[::1]:0")
    server.start()
    try:
        client = Memco(token="t", host=f"[::1]:{port}", tls=False, timeout=5.0)
        client.close()
    finally:
        server.stop(grace=None).wait(timeout=5)


# --- review: a blank host part resolved to an empty host ------------------


@pytest.mark.parametrize("host", [":50051", " :443", "[]"])
def test_a_host_with_no_name_is_rejected(host):
    with pytest.raises(errors.MemcoConfigError, match="host"):
        resolve(token="t", host=host, env={})


# --- review: operation_id is legitimately None and fed straight back ------


def test_a_missing_operation_id_is_a_typed_error_not_an_attribute_error():
    # WriteResult.operation_id is None for an un-revertible write, and the
    # documented flow feeds it straight into revert_memory.
    with pytest.raises(errors.MemcoInvalidRequestError, match="operation_id"):
        requests.revert_memory_request(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_handles_are_typed_errors(value):
    with pytest.raises(errors.MemcoInvalidRequestError):
        requests.get_memory_request(value)


# --- review: a bare string of sources became one source per character -----


def test_a_single_source_handle_is_not_exploded_into_characters():
    with pytest.raises(errors.MemcoInvalidRequestError, match="sources"):
        requests.enrich_memory_request(
            memory_idx="new",
            session_id="s",
            title="t",
            content="c",
            tags=None,
            sources="memory-9fg6vc-2",
            source=DataSource.AGENT,
        )


def test_a_list_of_sources_still_works():
    request = requests.enrich_memory_request(
        memory_idx="new",
        session_id="s",
        title="t",
        content="c",
        tags=None,
        sources=["memory-9fg6vc-2"],
        source=DataSource.AGENT,
    )
    assert list(request.sources) == ["memory-9fg6vc-2"]


# --- review: an unset memory field became a fabricated empty Memory -------


def test_get_memory_reports_an_absent_memory_rather_than_fabricating_one(
    client: Memco, harness: Harness
):
    harness.memory.responses["GetMemory"] = pb.GetMemoryResponse()  # no memory set
    with pytest.raises(errors.MemcoNotFoundError):
        client.memory.get_memory("memory-abc-1")


# --- review: exceptions could not cross a process or be copied ------------


@pytest.mark.parametrize(
    "exc",
    [
        errors.MemcoNotFoundError(grpc.StatusCode.NOT_FOUND, "gone", "dbg"),
        errors.MemcoResourceExhaustedError(grpc.StatusCode.RESOURCE_EXHAUSTED, "daily limit"),
        errors.MemcoInternalError(grpc.StatusCode.INTERNAL, "boom"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_errors_survive_pickling_and_copying(exc):
    # A worker that lets one of these out of a child process would otherwise
    # raise an opaque TypeError in place of the real error.
    for restored in (pickle.loads(pickle.dumps(exc)), copy.deepcopy(exc)):
        assert type(restored) is type(exc)
        assert restored.code is exc.code
        assert restored.message == exc.message


def test_resource_exhausted_keeps_its_kind_through_a_pickle():
    exc = errors.MemcoResourceExhaustedError(grpc.StatusCode.RESOURCE_EXHAUSTED, "daily limit")
    assert pickle.loads(pickle.dumps(exc)).kind is exc.kind


# --- review: unencodable text escaped as a raw UnicodeEncodeError ---------


@pytest.mark.parametrize("field", ["query", "title", "content"])
def test_unencodable_text_is_a_typed_error(field):
    # A lone surrogate arrives routinely from os.fsdecode of a mis-encoded
    # filename or from scraped JSON.
    kwargs = {"query": "q", "title": "t", "content": "c", "domain": "d"}
    kwargs[field] = "bad \ud800 here"
    with pytest.raises(errors.MemcoInvalidRequestError):
        requests.create_memory_request(
            query=kwargs["query"],
            title=kwargs["title"],
            content=kwargs["content"],
            domain=kwargs["domain"],
            session_id=None,
            tags=None,
            source=DataSource.AGENT,
        )


# --- review: the deprecation warning was attributed inside the package ----


def test_the_legacy_env_var_warning_reaches_the_caller():
    # DeprecationWarning is shown by default only when attributed to the
    # caller's own module, so a warning blamed on memco/_sync.py warns nobody.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = Memco(
            token=None,
            host="localhost:1",
            tls=False,
            check_health=False,
            env={"MEMCO_API_KEY": "legacy"},
        )
        client.close()
    assert caught, "no warning was raised"
    package = str(pathlib.Path(memco.__file__).parent)
    assert not caught[0].filename.startswith(package), (
        f"warning blamed on the SDK itself: {caught[0].filename}"
    )


# --- review: closing cancelled in-flight calls with a BaseException -------


async def test_closing_waits_for_an_in_flight_call(harness: Harness):
    # A CancelledError is caught by neither `except MemcoError` nor
    # `except Exception`, and inside a gather it is indistinguishable from the
    # caller cancelling the task.
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    task = asyncio.create_task(connected.memory.search("q", domain="coding"))
    await asyncio.sleep(0)
    await connected.close()
    assert (await task).session_id == "session-a"


async def test_concurrent_calls_all_survive_a_close(harness: Harness):
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    await connected.connect()
    tasks = [
        asyncio.create_task(connected.memory.search(f"q{n}", domain="coding")) for n in range(8)
    ]
    await asyncio.sleep(0)
    await connected.close()
    assert len(await asyncio.gather(*tasks)) == 8
