"""Write knowledge back, add to an existing memory, and undo a write.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/contribute.py
"""

from memco.client import Client, RevertOutcome, Tag

DOMAIN = "coding"


def main() -> None:
    """Run the example."""
    with Client() as client:
        session = client.memory.start_session(DOMAIN)

        # Writes are accepted asynchronously, so the result addresses the
        # operation rather than the memory it will become.
        created = client.memory.create_memory(
            query="how do I authenticate against the Memco memory API",
            title="The memory API takes a Bearer token, case-sensitively",
            content=(
                "The credential travels as `authorization: Bearer <token>`. The server "
                "matches the scheme with a case-sensitive prefix check, so a lowercase "
                "`bearer` is rejected as an invalid credential. The same header carries "
                "either a static API key or a WorkOS JWT."
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
            title="Health checks bypass the auth chain",
            content=(
                "grpc.health.v1.Health/Check is served outside the authentication "
                "interceptor, so it verifies connectivity but cannot validate a token."
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
