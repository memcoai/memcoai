"""Contribute a batch of memories in one call, and read what became of each.

Use this to move knowledge you already hold — a wiki export, a runbook, notes
from another system — into a domain in bulk. For knowledge learned during a
task, ``create_memory`` is the call: it mints an operation id you can undo with,
and an import does not.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/import_memories.py
"""

from memco import Memco
from memco.types import ImportedInsight, ImportedMemory, ImportStatus, Tag

DOMAIN = "coding"

BATCH = [
    ImportedMemory(
        # Queries are what someone would search to find this memory. More than
        # one is worth giving: they are the ways in, not a description of it.
        queries=[
            "how do I authenticate against the Memco memory API",
            "which header does the memory API take a token in",
        ],
        insights=[
            ImportedInsight(
                title="The Bearer prefix is case-sensitive",
                content=(
                    "Both an API key and a session token go in the same Authorization "
                    "header. A lowercase 'bearer' is rejected with UNAUTHENTICATED, "
                    "which reads as a bad credential rather than a malformed header."
                ),
            )
        ],
        tags=[Tag(type="language", value="python")],
    ),
    ImportedMemory(
        queries=["why is a memory write not retried after a connection blip"],
        insights=[
            ImportedInsight(
                title="gRPC retries are at-least-once, so writes stay out of the policy",
                content=(
                    "A retry sent after the server committed produces a duplicate, not "
                    "a second chance. Only ListDomains and GetMemory are replayed."
                ),
            )
        ],
    ),
]


def main() -> None:
    """Run the example."""
    with Memco() as client:
        # No session: a standalone upload belongs to no series of work. Pass
        # session_id instead when the batch was gathered during one task.
        #
        # BATCH can be any length. The service caps how many memories one call
        # carries, and the SDK divides a longer batch into that many per call —
        # so a wiki export goes in as one statement, not a chunking loop.
        result = client.memory.import_memories(BATCH, domain=DOMAIN)

        for outcome in result.results:
            queries = BATCH[outcome.index].queries
            print(f"[{outcome.index}] {outcome.status.name}: {queries[0]}")
            if outcome.status is ImportStatus.REJECTED:
                # The entry itself was not usable; fix it and send it again.
                for problem in outcome.errors:
                    print(f"      {problem}")
            elif outcome.status is ImportStatus.ERROR:
                # Usable, but not queued. Resubmitting is the whole remedy.
                print("      not queued; send this one again")

        # Sending the same batch twice is safe and free: an import is written
        # under an identity derived from its own content, so a second run
        # reports DUPLICATE and writes nothing. Nothing undoes an import, and
        # this is what stands in for that.
        again = client.memory.import_memories(BATCH, domain=DOMAIN)
        print(f"resent: {[o.status.name for o in again.results]}")


if __name__ == "__main__":
    main()
