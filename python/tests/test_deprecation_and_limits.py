"""Deprecation notices and input limits, both delivered on DescribeDomains."""

from __future__ import annotations

import warnings
from datetime import date

import grpc
import pytest
from google.protobuf import any_pb2
from google.rpc import code_pb2, error_details_pb2, status_pb2
from grpc_status import rpc_status

from memco import AsyncMemco, Memco, MemcoDeprecationWarning, errors, types
from memco._deprecation import reset_warnings
from memco.memory.v1 import memory_pb2 as pb

from .conftest import TOKEN
from .fake_server import Harness


@pytest.fixture(autouse=True)
def _forget_previous_warnings():
    # The notice is emitted once per process, so tests must not inherit one
    # another's state.
    reset_warnings()
    yield
    reset_warnings()


# What the service sends a version it has stopped serving. Named once, for the
# same reason deprecated_response() is: it is fixture data, not a value under
# test. The two reasons are the two things a sunset can block.
CLIENT_SUNSET = "CLIENT_VERSION_SUNSET"
API_SUNSET = "API_VERSION_SUNSET"
UPGRADE_REMEDY = "memco-python/0.1.0 is no longer served; upgrade to >=0.4.0"
MIGRATE_REMEDY = "Memory API v1 is no longer served; migrate to v2"


def sunset_status(reason: str, message: str, domain: str = "memco.ai") -> grpc.Status:
    """Build the FAILED_PRECONDITION a blocked version is refused with."""
    detail = any_pb2.Any()
    detail.Pack(error_details_pb2.ErrorInfo(reason=reason, domain=domain))
    return rpc_status.to_status(
        status_pb2.Status(code=code_pb2.FAILED_PRECONDITION, message=message, details=[detail])
    )


def deprecated_response(**kwargs) -> pb.DescribeDomainsResponse:
    return pb.DescribeDomainsResponse(
        domains=[pb.DomainEntry(slug="coding")],
        deprecated=True,
        deprecation_message="Memory API v1 is superseded; migrate to v2 by 2027-01-01.",
        **kwargs,
    )


# --- the fields are read at all ------------------------------------------


def test_deprecation_fields_reach_the_caller(client: Memco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = deprecated_response(sunset_date="2027-01-01")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = client.memory.describe_domains()
    assert result.deprecated is True
    assert result.deprecation_message.startswith("Memory API v1 is superseded")
    assert result.sunset_date == date(2027, 1, 1)


def test_limits_reach_the_caller(client: Memco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
        limits=pb.Limits(
            max_query_characters=100,
            max_text_characters=200,
            max_idx_characters=32,
            max_sources=3,
            max_feedback_entries=5,
            max_import_memories=50,
            max_import_queries_per_memory=6,
            max_import_insights_per_memory=7,
            max_import_tags_per_memory=8,
        )
    )
    limits = client.memory.describe_domains().limits
    assert limits is not None
    assert limits.max_query_characters == 100
    assert limits.max_text_characters == 200
    assert limits.max_sources == 3
    assert limits.max_import_memories == 50
    assert limits.max_import_queries_per_memory == 6
    assert limits.max_import_insights_per_memory == 7
    assert limits.max_import_tags_per_memory == 8


def test_per_domain_tag_cap_reaches_the_caller(client: Memco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
        domains=[pb.DomainEntry(slug="coding", max_tags_per_query=4)]
    )
    assert client.memory.describe_domains().domains[0].max_tags_per_query == 4


# --- an older server says nothing, and that must not mean zero -----------


def test_an_absent_limits_message_means_validate_nothing(client: Memco, harness: Harness):
    # Treating a missing message as zeros would reject every call locally.
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
        domains=[pb.DomainEntry(slug="coding")]
    )
    result = client.memory.describe_domains()
    assert result.limits is None
    client.memory.search("a query of some length", domain="coding")
    client.memory.create_memory(query="q", title="t" * 500, content="c" * 500, domain="coding")


def test_an_older_server_reports_no_deprecation(client: Memco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = client.memory.describe_domains()
    assert result.deprecated is False
    assert result.sunset_date is None
    assert not caught


def test_an_empty_sunset_date_is_none(client: Memco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = deprecated_response()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert client.memory.describe_domains().sunset_date is None


# --- the notice is surfaced once, unchanged, and never fatally ------------


def test_the_notice_is_emitted_once_across_repeated_calls(client: Memco, harness: Harness):
    # Per-call warnings make a busy client unusable and get filtered wholesale,
    # which defeats the point.
    harness.memory.responses["DescribeDomains"] = deprecated_response(sunset_date="2027-01-01")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for _ in range(5):
            client.memory.describe_domains()
    assert len(caught) == 1


def test_the_notice_relays_the_server_text_unchanged(client: Memco, harness: Harness):
    # Only the service knows the remedy, so nothing may be inferred or added
    # beyond the sunset date.
    harness.memory.responses["DescribeDomains"] = deprecated_response()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.memory.describe_domains()
    assert str(caught[0].message) == "Memory API v1 is superseded; migrate to v2 by 2027-01-01."


def test_a_sunset_date_is_appended_not_substituted(client: Memco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = deprecated_response(sunset_date="2027-01-01")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.memory.describe_domains()
    text = str(caught[0].message)
    assert text.startswith("Memory API v1 is superseded")
    assert "2027-01-01" in text


def test_a_changed_message_is_surfaced_again(client: Memco, harness: Harness):
    harness.memory.responses["DescribeDomains"] = deprecated_response()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client.memory.describe_domains()
        harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
            deprecated=True, deprecation_message="A different remedy entirely."
        )
        client.memory.describe_domains()
    assert len(caught) == 2


def test_a_deprecation_never_fails_the_call(client: Memco, harness: Harness):
    # Deprecation is advice: it must not raise, even escalated to an error.
    harness.memory.responses["DescribeDomains"] = deprecated_response(sunset_date="2027-01-01")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = client.memory.describe_domains()
    assert result.deprecated is True


def test_a_deprecated_service_still_lets_a_client_be_built(harness: Harness):
    harness.memory.responses["DescribeDomains"] = deprecated_response()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        built = Memco(token=TOKEN, host=harness.address, tls=False)
    built.close()


def test_construction_surfaces_the_notice(harness: Harness):
    # Construction fetches the limits, so the notice arrives there — which is
    # where "once per process, not per call" wants it.
    harness.memory.responses["DescribeDomains"] = deprecated_response()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Memco(token=TOKEN, host=harness.address, tls=False).close()
    assert [str(warning.message) for warning in caught] == [
        "Memory API v1 is superseded; migrate to v2 by 2027-01-01."
    ]


def test_the_warning_category_is_visible_by_default():
    # DeprecationWarning is hidden outside __main__, so a notice raised from
    # inside a framework or a service would reach nobody.
    assert issubclass(MemcoDeprecationWarning, FutureWarning)


# --- past the sunset, the service blocks the version outright -------------


def test_a_blocked_client_version_says_upgrade_the_package(client: Memco, harness: Harness):
    # The other end of the lifecycle from the warning above: that is advance
    # notice on a version that still works, this is the block on one that does
    # not. The service decides when it happens.
    harness.memory.rich_error = sunset_status(CLIENT_SUNSET, UPGRADE_REMEDY)
    with pytest.raises(errors.MemcoSunsetError) as caught:
        client.memory.search("anything at all", domain="coding")
    assert caught.value.kind is errors.SunsetKind.CLIENT_VERSION
    assert caught.value.message == UPGRADE_REMEDY


def test_a_blocked_api_version_says_migrate(client: Memco, harness: Harness):
    # The case the flag deliberately does not distinguish. Blocking API v1 is
    # not a reason to tell anyone to reinstall the package, so the kind is what
    # a caller branches on and the message is what it shows.
    harness.memory.rich_error = sunset_status(API_SUNSET, MIGRATE_REMEDY)
    with pytest.raises(errors.MemcoSunsetError) as caught:
        client.memory.search("anything at all", domain="coding")
    assert caught.value.kind is errors.SunsetKind.API_VERSION
    assert caught.value.message == MIGRATE_REMEDY


def test_a_precondition_failure_carrying_nothing_is_not_a_sunset(client: Memco, harness: Harness):
    # FAILED_PRECONDITION is a general-purpose status: a gateway, a billing
    # check or an account in the wrong state all use it. Without the detail
    # that names a sunset, telling the caller to upgrade would be a guess.
    harness.memory.error = (grpc.StatusCode.FAILED_PRECONDITION, "billing account disabled")
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        client.memory.search("anything at all", domain="coding")
    assert not isinstance(caught.value, errors.MemcoSunsetError)
    assert caught.value.message == "billing account disabled"


def test_another_service_cannot_claim_a_memco_sunset(client: Memco, harness: Harness):
    # The domain scopes the reason. Without checking it, any service on the
    # path could tell a caller their Memco client is out of date.
    harness.memory.rich_error = sunset_status(CLIENT_SUNSET, "not ours", domain="example.com")
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        client.memory.search("anything at all", domain="coding")
    assert not isinstance(caught.value, errors.MemcoSunsetError)


def test_an_unrecognised_reason_is_not_guessed_at(client: Memco, harness: Harness):
    # A reason this build has never heard of may or may not be a sunset. The
    # message still reaches the caller either way.
    harness.memory.rich_error = sunset_status("SOMETHING_NEW", "a remedy we cannot classify")
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        client.memory.search("anything at all", domain="coding")
    assert not isinstance(caught.value, errors.MemcoSunsetError)
    assert caught.value.message == "a remedy we cannot classify"


def test_a_detail_that_is_not_an_error_info_is_stepped_over(client: Memco, harness: Harness):
    # A status may carry several details. Finding a RetryInfo first must not
    # stop the ErrorInfo behind it from being read.
    retry = any_pb2.Any()
    retry.Pack(error_details_pb2.RetryInfo())
    sunset = any_pb2.Any()
    sunset.Pack(error_details_pb2.ErrorInfo(reason=CLIENT_SUNSET, domain="memco.ai"))
    harness.memory.rich_error = rpc_status.to_status(
        status_pb2.Status(
            code=code_pb2.FAILED_PRECONDITION,
            message=UPGRADE_REMEDY,
            details=[retry, sunset],
        )
    )
    with pytest.raises(errors.MemcoSunsetError) as caught:
        client.memory.search("anything at all", domain="coding")
    assert caught.value.kind is errors.SunsetKind.CLIENT_VERSION


def test_a_malformed_detail_never_escapes_as_a_raw_protobuf_error(client: Memco, harness: Harness):
    # Losing the discriminator is acceptable; losing the error is not. A detail
    # whose bytes do not parse must still leave the caller with the failure the
    # service actually reported.
    corrupt = any_pb2.Any(
        type_url="type.googleapis.com/google.rpc.ErrorInfo", value=b"\xff\xff\xff\xff"
    )
    harness.memory.rich_error = rpc_status.to_status(
        status_pb2.Status(code=code_pb2.FAILED_PRECONDITION, message="billing", details=[corrupt])
    )
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        client.memory.search("anything at all", domain="coding")
    assert caught.value.message == "billing"


def test_a_blocked_version_fails_when_the_client_is_built(harness: Harness):
    # Construction calls DescribeDomains, so a blocked version is refused there
    # rather than on the caller's first real call. DescribeDomains is in
    # RETRYABLE_METHODS, but only UNAVAILABLE is retryable, so the single
    # recorded call also shows the refusal was not replayed.
    harness.memory.rich_error = sunset_status(CLIENT_SUNSET, UPGRADE_REMEDY)
    with pytest.raises(errors.MemcoSunsetError):
        Memco(token=TOKEN, host=harness.address, tls=False)
    assert harness.memory.calls == ["DescribeDomains"]


async def test_a_blocked_version_fails_when_the_async_client_connects(harness: Harness):
    # AsyncMemco cannot probe in __init__, so connect() is where it lands.
    harness.memory.rich_error = sunset_status(CLIENT_SUNSET, UPGRADE_REMEDY)
    connected = AsyncMemco(token=TOKEN, host=harness.address, tls=False)
    with pytest.raises(errors.MemcoSunsetError) as caught:
        await connected.connect()
    assert caught.value.kind is errors.SunsetKind.CLIENT_VERSION
    await connected.close()


def test_the_health_probe_does_not_produce_a_sunset(harness: Harness):
    # Construction probes grpc's health service before it calls Memco at all.
    # That service scopes its own errors under its own domain, so a precondition
    # failure from it reaches the caller as the general error rather than as
    # "your Memco client is out of date". The domain is what distinguishes
    # them: this does not police who is on the connection.
    harness.health.rich_error = sunset_status(CLIENT_SUNSET, UPGRADE_REMEDY, domain="grpc.io")
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        Memco(token=TOKEN, host=harness.address, tls=False)
    assert not isinstance(caught.value, errors.MemcoSunsetError)
    assert harness.memory.calls == []


# --- limits: eight refuse, two trim --------------------------------------


def limited(client: Memco, harness: Harness, **limits) -> None:
    """Teach the client the service's limits by making the call that carries them."""
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
        domains=[pb.DomainEntry(slug="coding", max_tags_per_query=limits.pop("max_tags", 0))],
        limits=pb.Limits(**limits),
    )
    client.memory.describe_domains()


def test_an_over_long_query_is_refused(client: Memco, harness: Harness):
    limited(client, harness, max_query_characters=10)
    harness.memory.calls.clear()
    with pytest.raises(errors.MemcoInvalidRequestError, match="query"):
        client.memory.search("x" * 11, domain="coding")
    assert harness.memory.calls == []


def test_title_and_content_are_bounded_together_not_each(client: Memco, harness: Harness):
    limited(client, harness, max_text_characters=100)
    harness.memory.calls.clear()
    # 60 + 60 exceeds 100 even though neither alone does.
    with pytest.raises(errors.MemcoInvalidRequestError):
        client.memory.create_memory(query="q", title="t" * 60, content="c" * 60, domain="coding")
    assert harness.memory.calls == []
    client.memory.create_memory(query="q", title="t" * 40, content="c" * 40, domain="coding")


def test_an_over_long_handle_is_refused(client: Memco, harness: Harness):
    limited(client, harness, max_idx_characters=8)
    harness.memory.calls.clear()
    with pytest.raises(errors.MemcoInvalidRequestError, match="idx"):
        client.memory.get_memory("m" * 9)
    assert harness.memory.calls == []


def test_too_many_feedback_entries_are_refused(client: Memco, harness: Harness):
    limited(client, harness, max_feedback_entries=2)
    harness.memory.calls.clear()
    ratings = [types.FeedbackRating(idx=f"i{n}", relevant=True, correct=True) for n in range(3)]
    with pytest.raises(errors.MemcoInvalidRequestError, match="feedback"):
        client.memory.share_feedback(session_id="s", feedback=ratings)
    assert harness.memory.calls == []


def test_excess_sources_are_trimmed_not_refused(client: Memco, harness: Harness):
    # Raising would reject a call the service would have accepted.
    limited(client, harness, max_sources=2)
    client.memory.enrich_memory(
        memory_idx="new",
        session_id="s",
        title="t",
        content="c",
        sources=["a", "b", "c", "d"],
    )
    assert len(harness.memory.requests["EnrichMemory"].sources) == 2


def test_excess_tags_are_trimmed_per_domain(client: Memco, harness: Harness):
    limited(client, harness, max_tags=2)
    tags = [types.Tag(type="language", value=v) for v in ("python", "go", "rust")]
    client.memory.search("q", domain="coding", tags=tags)
    assert len(harness.memory.requests["Search"].tags) == 2


def test_a_zero_tag_cap_means_no_cap(client: Memco, harness: Harness):
    limited(client, harness, max_tags=0)
    tags = [types.Tag(type="language", value=v) for v in ("python", "go", "rust")]
    client.memory.search("q", domain="coding", tags=tags)
    assert len(harness.memory.requests["Search"].tags) == 3


def test_limits_are_not_applied_before_they_are_known(client: Memco, harness: Harness):
    # Nothing has taught the client any limits, so the service rules.
    client.memory.search("x" * 5000, domain="coding")
    assert harness.memory.calls == ["Search"]


# --- limits: the import caps all refuse, and name the entry at fault ------


def imported(**overrides) -> types.ImportedMemory:
    """One valid imported memory, with fields swapped out per test."""
    fields = {
        "queries": ["how does X work"],
        "insights": [types.ImportedInsight(title="t", content="c")],
    }
    return types.ImportedMemory(**{**fields, **overrides})


def test_a_batch_over_the_reported_cap_is_split_rather_than_refused(
    client: Memco, harness: Harness
):
    # The cap bounds one call, not one batch. A caller with more memories than
    # the service takes at once should not have to discover the number and chunk
    # against it, so the SDK splits and calls again.
    limited(client, harness, max_import_memories=2)
    harness.memory.calls.clear()
    result = client.memory.import_memories([imported()] * 5, domain="coding")
    assert harness.memory.calls == ["ImportMemories"] * 3
    # Each group numbers its own results from zero, so without a remap the
    # caller would get 0,1,0,1,0 and be unable to tell the entries apart.
    assert [outcome.index for outcome in result.results] == [0, 1, 2, 3, 4]
    # The last group carries the remainder; none exceeds the cap.
    assert len(harness.memory.requests["ImportMemories"].memories) == 1


async def test_the_async_client_splits_a_long_batch_too(async_client: AsyncMemco, harness: Harness):
    # The async side awaits inside the comprehension that makes the calls, which
    # is unusual enough to pin: the two surfaces have to split identically.
    harness.memory.responses["DescribeDomains"] = pb.DescribeDomainsResponse(
        limits=pb.Limits(max_import_memories=2)
    )
    await async_client.memory.describe_domains()
    harness.memory.calls.clear()
    result = await async_client.memory.import_memories([imported()] * 5, domain="coding")
    assert harness.memory.calls == ["ImportMemories"] * 3
    assert [outcome.index for outcome in result.results] == [0, 1, 2, 3, 4]


def test_an_unreported_import_cap_sends_the_whole_batch_in_one_call(
    client: Memco, harness: Harness
):
    # No cap reported means the service rules, here as everywhere else: the SDK
    # does not invent a group size of its own to split against.
    harness.memory.calls.clear()
    result = client.memory.import_memories([imported()] * 5, domain="coding")
    assert harness.memory.calls == ["ImportMemories"]
    assert len(harness.memory.requests["ImportMemories"].memories) == 5
    assert [outcome.index for outcome in result.results] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    ("cap", "entry", "expected"),
    [
        ("max_import_queries_per_memory", {"queries": ["a", "b", "c"]}, r"memories\[1\] queries"),
        (
            "max_import_insights_per_memory",
            {"insights": [types.ImportedInsight(title="t", content="c")] * 3},
            r"memories\[1\] insights",
        ),
        (
            "max_import_tags_per_memory",
            {"tags": [types.Tag(type="language", value=f"v{n}") for n in range(3)]},
            r"memories\[1\] tags",
        ),
    ],
)
def test_a_per_entry_import_cap_refuses_and_names_the_entry(
    client: Memco, harness: Harness, cap: str, entry: dict[str, object], expected: str
):
    # A batch gives the caller no handle to address one memory by, so the index
    # is the only thing that says which of them has to be cut down.
    limited(client, harness, **{cap: 2})
    harness.memory.calls.clear()
    with pytest.raises(errors.MemcoInvalidRequestError, match=expected):
        client.memory.import_memories([imported(), imported(**entry)], domain="coding")
    assert harness.memory.calls == []


def test_an_imported_insight_is_bounded_by_the_text_cap(client: Memco, harness: Harness):
    limited(client, harness, max_text_characters=100)
    harness.memory.calls.clear()
    # 60 + 60 exceeds 100 even though neither alone does, as everywhere else.
    with pytest.raises(errors.MemcoInvalidRequestError, match=r"memories\[0\] insights\[0\]"):
        client.memory.import_memories(
            [imported(insights=[types.ImportedInsight(title="t" * 60, content="c" * 60)])],
            domain="coding",
        )
    assert harness.memory.calls == []
    client.memory.import_memories(
        [imported(insights=[types.ImportedInsight(title="t" * 40, content="c" * 40)])],
        domain="coding",
    )


def test_the_import_tag_cap_refuses_where_the_domain_cap_trims(client: Memco, harness: Harness):
    # Two caps meet on the same field and disagree about what to do. The import
    # cap refuses, so it is checked against what the caller supplied — checking
    # it after the domain trim would make it unreachable.
    limited(client, harness, max_tags=1, max_import_tags_per_memory=2)
    harness.memory.calls.clear()
    tags = [types.Tag(type="language", value=f"v{n}") for n in range(3)]
    with pytest.raises(errors.MemcoInvalidRequestError, match=r"memories\[0\] tags"):
        client.memory.import_memories([imported(tags=tags)], domain="coding")
    assert harness.memory.calls == []
    # Within the refusing cap, the trimming one still trims rather than raising.
    client.memory.import_memories([imported(tags=tags[:2])], domain="coding")
    assert len(harness.memory.requests["ImportMemories"].memories[0].tags) == 1
