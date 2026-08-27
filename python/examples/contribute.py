"""Write knowledge back, add to an existing memory, and undo a write.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/contribute.py
"""

from memco import Memco
from memco.types import RevertOutcome, Tag

DOMAIN = "coding"


def main() -> None:
    """Run the example."""
    with Memco() as client:
        session = client.memory.start_session(DOMAIN)

        # Writes are accepted asynchronously, so the result addresses the
        # operation rather than the memory it will become.
        created = client.memory.create_memory(
            query="how do I authenticate against the Memco memory API",
            title="Retries must not be applied to memory writes",
            content=(
                "A write is accepted asynchronously and its operation id identifies "
                "that one acceptance, so retrying a write that appears to fail can "
                "record it twice. Retry the read operations instead, and use the "
                "returned operation id to undo a write you did not mean to make."
            ),
            session_id=session.session_id,
            tags=[Tag(type="language", value="python"), Tag(type="task", value="implementation")],
        )
        print(f"created: {created.operation_id}")

        # An empty operation id means the write was accepted but cannot be
        # undone: the content is worth more than the ability to revert it.
        if created.operation_id is None:
            print("  (accepted, but not revertible)")

        # Enrichment adds to a memory a search returned, so the addition lands
        # alongside the existing insights instead of becoming a rival memory.
        # Pass the literal "new" to open one instead — it is case-sensitive.
        enriched = client.memory.enrich_memory(
            memory_idx="new",
            session_id=session.session_id,
            title="Building a client is what teaches it the service's limits",
            content=(
                "Constructing a client probes health and then calls describe_domains. "
                "The second call carries the credential, so a bad token fails there, "
                "and it reports the caps the service enforces — which is why an "
                "oversized field is refused locally rather than after a round trip."
            ),
        )
        print(f"enriched: {enriched.operation_id}")

        # Reverting reports what it actually removed. Not-found, expired and
        # refused are outcomes, not errors: they describe caller-visible state.
        if created.operation_id:
            reverted = client.memory.revert_memory(created.operation_id)
            print(f"revert: {reverted.outcome.name}")
            if reverted.outcome is RevertOutcome.EXPIRED:
                print("  outside the revert window")
            elif reverted.outcome is RevertOutcome.NOT_FOUND:
                print("  ingestion may still be running; try again shortly")


if __name__ == "__main__":
    main()
