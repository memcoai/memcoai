"""Deprecation notices and input limits, both delivered on DescribeDomains."""

from __future__ import annotations

import warnings
from datetime import date

import pytest

from memco import Memco, MemcoDeprecationWarning, errors, types
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
        )
    )
    limits = client.memory.describe_domains().limits
    assert limits is not None
    assert limits.max_query_characters == 100
    assert limits.max_text_characters == 200
    assert limits.max_sources == 3


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
        built = Memco(token=TOKEN, host=harness.address, tls=False, verify_credentials=True)
    built.close()


def test_the_warning_category_is_visible_by_default():
    # DeprecationWarning is hidden outside __main__, so a notice raised from
    # inside a framework or a service would reach nobody.
    assert issubclass(MemcoDeprecationWarning, FutureWarning)


# --- limits: four refuse, two trim ---------------------------------------


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
