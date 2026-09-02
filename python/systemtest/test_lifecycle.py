"""Every command the service publishes, in one story, against the real server.

The order is not a stylistic choice. A write is accepted asynchronously, so
`create_memory` hands back an operation id rather than a memory, and the memory
it will become is not addressable until ingestion has run. Reverting before
then reports NOT_FOUND — the contract says so outright: "either it never
existed or its ingestion has not completed yet". So the memory has to be found
by searching for it before it can be rated, fetched, enriched or removed.

The content the test writes is real prose about this SDK rather than filler.
A write is evaluated on the way in and can be rejected downstream by the
service's quality gate, and a rejected memory never becomes searchable — which
would surface here as "search never found it", indistinguishable from a broken
search path.
"""

from __future__ import annotations

import time

import pytest

from memco import Memco
from memco.errors import MemcoNotFoundError
from memco.types import (
    DataSource,
    FeedbackRating,
    ImportedInsight,
    ImportedMemory,
    ImportStatus,
    Insight,
    Memory,
    RevertOutcome,
)

# A write is accepted asynchronously and only becomes searchable once ingestion
# has run, so every assertion about a memory existing — or having stopped
# existing — is a poll rather than a single call.
INGEST_TIMEOUT = 180.0
REMOVAL_TIMEOUT = 60.0
POLL_INTERVAL = 5.0


def probe(nonce: str) -> dict[str, str]:
    """The memory this run writes: true, substantive, and free of identifiers.

    The run marker lives in the query and nowhere else. The query becomes the
    memory's intent, which comes back on every search result, so the test can
    still recognise its own memory — while the insight the service evaluates is
    pure prose. An identifier in the title or body is rejected outright: the
    quality gate refuses content dominated by IDs, and a rejected memory never
    becomes searchable, which surfaces here as a search that never finds it.

    The subject is deliberately specific to the Python SDK, so it can never be
    read as the same knowledge as what the Node.js suite writes.
    """
    return {
        "query": (
            "Which gRPC calls does the Memco Python SDK retry, "
            f"and which does it deliberately not retry? (system test {nonce})"
        ),
        "title": "Python SDK retry policy",
        "content": (
            "The Memco Python SDK enables gRPC retries for a deliberately narrow set of "
            "methods. `_channel.RETRYABLE_METHODS` names `ListDomains` and `GetMemory`, "
            "together with the health `Check`, and the service config allows at most three "
            "attempts, only on `UNAVAILABLE`.\n\n"
            "Everything else is excluded on purpose. A retried write could be applied twice, "
            "and a retried `Search` would be recorded against the session twice, so both are "
            "left for the caller to decide about.\n\n"
            "This note is written and then removed again by the Memco Python SDK system "
            "test. If you are reading it, that run did not finish cleaning up."
        ),
    }


def addition() -> dict[str, str]:
    """The insight the enrich step adds to the memory above.

    A different fact about the same subject, on purpose. An enrichment close
    enough to the insight it joins can be endorsed as a duplicate rather than
    added as new, and it would then never appear as a second insight to find.

    Its title is what tells it apart from the insight it joins, so neither needs
    a marker of its own.
    """
    return {
        "title": "Python SDK credential withholding",
        "content": (
            "The Memco Python SDK attaches its credential with a channel interceptor rather "
            "than with call credentials. gRPC refuses call credentials on an insecure "
            "channel, so the interceptor is what lets `tls=False` work against a local "
            "plaintext endpoint — which is how the whole offline test suite runs.\n\n"
            "The interceptor withholds the credential from any method under "
            "`/grpc.health.v1.`, so the health probe the constructor makes is unauthenticated "
            "and a bad token cannot be mistaken for an unhealthy service.\n\n"
            "Added and then removed again by the Memco Python SDK system test."
        ),
    }


def _insight_titled(memory: Memory, title: str) -> Insight | None:
    """The insight of this memory with exactly this title, if any."""
    for insight in memory.insights:
        if insight.title == title:
            return insight
    return None


def _is_ours(memory: Memory, nonce: str) -> bool:
    """Whether this memory was written by this run.

    The marker is in the intent because it cannot be in the insight: the query
    passed to create_memory becomes the memory's intent, and intents come back
    on every search result.
    """
    return any(nonce in intent for intent in memory.intents)


def _resolved(client: Memco, memory: Memory) -> Memory:
    """A memory with its insights, fetching them if the search withheld them.

    Within one session a memory a previous search already delivered comes back
    as a bare reference carrying no insights. The polling below re-searches in
    one session, so without this a memory delivered once — before its insight
    was attached, say — would never match again and the poll would time out
    blaming the quality gate.
    """
    if memory.insights or not memory.reference:
        return memory
    return client.memory.get_memory(memory.idx)


def _search_until_found(
    client: Memco, session_id: str, query: str, nonce: str, title: str
) -> tuple[Memory, Insight]:
    """Poll the search until this run's own memory comes back, or give up loudly.

    Every returned memory is examined rather than just the first: the probe is
    brand new and competing with whatever else the domain holds, so its rank is
    not something the test may assume.
    """
    deadline = time.monotonic() + INGEST_TIMEOUT
    seen = 0
    while time.monotonic() < deadline:
        result = client.memory.search(query, session_id=session_id)
        seen = len(result.memories)
        for candidate in result.memories:
            memory = _resolved(client, candidate)
            if not _is_ours(memory, nonce):
                continue
            insight = _insight_titled(memory, title)
            if insight is not None:
                return memory, insight
        print(f"  waiting for ingestion; search returned {seen} memories, none ours")
        time.sleep(POLL_INTERVAL)
    pytest.fail(
        f"the memory never became searchable within {INGEST_TIMEOUT:.0f}s "
        f"(last search returned {seen} memories, none whose intent names {nonce!r}). "
        "Three things can cause this. The write may have been rejected downstream "
        "by the quality gate, in which case it never becomes searchable at all. "
        "Ingestion may simply be slower than the budget. Or the service may not "
        "carry a newly created memory's own query in its intents, which is the "
        "assumption this suite rests on to recognise its own memory without "
        "putting an identifier in the insight."
    )


def _get_until_titled(client: Memco, idx: str, title: str) -> Insight:
    """Poll GetMemory until the memory carries an insight with this title.

    GetMemory rather than a second search: within one session a memory already
    returned comes back as a bare reference with no insights, so a search cannot
    show us the insight the enrich step just added.
    """
    deadline = time.monotonic() + INGEST_TIMEOUT
    while time.monotonic() < deadline:
        insight = _insight_titled(client.memory.get_memory(idx), title)
        if insight is not None:
            return insight
        print("  waiting for the enrichment to be ingested")
        time.sleep(POLL_INTERVAL)
    pytest.fail(
        f"the enrichment never appeared on {idx} within {INGEST_TIMEOUT:.0f}s. "
        "Either ingestion is slower than the budget, or the service endorsed the "
        "addition as a duplicate of an insight the memory already held instead of "
        "adding it as a new one."
    )


def _get_until_gone(client: Memco, idx: str) -> None:
    """Poll GetMemory until it reports the memory is gone."""
    deadline = time.monotonic() + REMOVAL_TIMEOUT
    while time.monotonic() < deadline:
        try:
            client.memory.get_memory(idx)
        except MemcoNotFoundError:
            return
        print("  waiting for the removal to take effect")
        time.sleep(POLL_INTERVAL)
    pytest.fail(
        f"{idx} was still retrievable {REMOVAL_TIMEOUT:.0f}s after a revert reported MEMORY_REMOVED"
    )


def test_the_whole_lifecycle_runs_against_the_live_service(
    client: Memco, domain: str, nonce: str, written: list[str]
) -> None:
    """Create, find, rate, fetch, enrich and remove a memory in one domain.

    ListDomains is exercised by the parametrisation that produced `domain` and
    again by the client constructor, so it is not called a third time here.
    """
    fields = probe(nonce)
    extra = addition()
    print(f"\n[{domain}] probe {nonce}")

    session = client.memory.start_session(domain)
    assert session.session_id

    write = client.memory.create_memory(
        query=fields["query"],
        title=fields["title"],
        content=fields["content"],
        session_id=session.session_id,
        source=DataSource.AGENT,
    )
    assert write.operation_id, (
        "the write was accepted without an operation id, so it cannot be reverted; "
        "refusing to leave a memory behind in a live domain"
    )
    written.append(write.operation_id)
    print(f"  created, operation {write.operation_id}")

    memory, insight = _search_until_found(
        client, session.session_id, fields["query"], nonce, fields["title"]
    )
    print(f"  found as {memory.idx}, insight {insight.idx}")

    feedback = client.memory.share_feedback(
        session_id=session.session_id,
        feedback=[FeedbackRating(idx=insight.idx, relevant=True, correct=True)],
    )
    assert insight.idx in [entry.idx for entry in feedback.entries]

    fetched = client.memory.get_memory(memory.idx)
    assert fetched.idx == memory.idx
    assert _insight_titled(fetched, fields["title"]) is not None

    # An enrichment is a second write against the same memory, with an
    # operation id of its own.
    enrichment = client.memory.enrich_memory(
        memory_idx=memory.idx,
        session_id=session.session_id,
        title=extra["title"],
        content=extra["content"],
    )
    assert enrichment.operation_id
    written.append(enrichment.operation_id)
    added = _get_until_titled(client, memory.idx, extra["title"])
    print(f"  enriched, insight {added.idx}")

    undo_addition = client.memory.revert_memory(enrichment.operation_id)
    assert undo_addition.outcome is RevertOutcome.ADDITION_REMOVED, (
        f"reverting the enrichment reported {undo_addition.outcome.name}, not ADDITION_REMOVED"
    )
    # The outcome is what the service reported; this is what it did. The
    # addition is gone and the memory it joined is intact.
    after = client.memory.get_memory(memory.idx)
    assert _insight_titled(after, extra["title"]) is None, "the reverted addition is still there"
    assert _insight_titled(after, fields["title"]) is not None, (
        "reverting the addition took the original insight with it"
    )

    # Removing the original insight takes its memory with it: it is the last
    # one the memory holds.
    undo_memory = client.memory.revert_memory(write.operation_id)
    assert undo_memory.outcome is RevertOutcome.MEMORY_REMOVED, (
        f"reverting the write reported {undo_memory.outcome.name}, not MEMORY_REMOVED. "
        "MERGED or ADDITION_REMOVED means the probe was folded into an existing memory. "
        "The insight carries no run marker, so two runs of this SDK overlapping would "
        "write identical content and collide; the CI job holds a per-language concurrency "
        "group to keep one of them in flight at a time."
    )
    print("  reverted")

    _get_until_gone(client, memory.idx)
    print("  gone")


def import_fixture() -> ImportedMemory:
    """The batch the import step contributes.

    It carries no run marker, deliberately. An import mints no operation id and
    cannot be reverted — the contract is explicit that there is no handle that
    undoes one — so a per-run payload would leave a memory behind in a live
    domain on every pull request. An import is written under an identity derived
    from its own content, so this fixed batch lands once, ever, and every run
    after that is reported DUPLICATE and charged nothing.

    It is therefore real knowledge worth keeping rather than a test artefact:
    whatever this writes stays, and will need correcting by hand if it goes out
    of date.
    """
    return ImportedMemory(
        queries=[
            (
                "What endpoint, credential and timeout does the Memco Python SDK "
                "use when nothing is configured?"
            )
        ],
        insights=[
            ImportedInsight(
                title="Memco Python SDK connection defaults",
                content=(
                    "The Memco Python SDK resolves its endpoint and credential with "
                    "argument > environment > default precedence. Left unset it dials "
                    "`grpc.spark.memco.ai` on port 443 over TLS with a 30 second deadline, "
                    "and reads the credential from `MEMCO_API_TOKEN`. `MEMCO_API_KEY` is "
                    "still accepted as a fallback and warns once when it is used.\n\n"
                    "A blank token or host passed as an argument is refused outright rather "
                    "than falling back to the environment, so a caller that computed its "
                    "configuration wrongly fails at construction instead of quietly reaching "
                    "a different service than it meant to."
                ),
            )
        ],
    )


def test_a_batch_import_is_accepted_or_already_present(client: Memco, primary_domain: str) -> None:
    """Contribute a batch — the one write the service gives no way to undo.

    One domain, not every domain: the write is permanent, so the blast radius is
    kept to a single memory rather than one per domain the credential reaches.
    """
    result = client.memory.import_memories([import_fixture()], domain=primary_domain)
    assert len(result.results) == 1
    outcome = result.results[0]
    assert outcome.index == 0
    assert outcome.status in {ImportStatus.QUEUED, ImportStatus.DUPLICATE}, (
        f"the import reported {outcome.status.name}"
        + (f": {'; '.join(outcome.errors)}" if outcome.errors else "")
    )
    print(f"\n[{primary_domain}] import {outcome.status.name}")
