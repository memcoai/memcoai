"""The full read loop: open a session, search under it, rate what came back.

Rating is the part people skip, and it is the only signal the service gets
about whether a result actually answered the question. Searches made under one
session are recorded as a series, which is what makes them rateable afterwards.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/search_and_rate.py
"""

from memco.client import Client, FeedbackRating, Tag

DOMAIN = "coding"


def main() -> None:
    """Run the example."""
    with Client() as client:
        # A session ties related searches together. Reuse its id for every
        # search made for the same task, and for the ratings afterwards.
        session = client.memory.start_session(DOMAIN)
        print(f"session {session.session_id}")

        result = client.memory.search(
            "how does gRPC health checking interact with an auth interceptor",
            session_id=session.session_id,
            # Which tag types narrow results and which merely boost them is
            # per-domain; list_domains() describes them. A wrong filtering tag
            # returns nothing at all, so start without tags if unsure.
            tags=[Tag(type="language", value="python", version="3.12")],
        )

        if result.notice:
            print(f"notice: {result.notice}")
        if not result.memories:
            print("nothing matched; try a broader query")
            return

        ratings = []
        for memory in result.memories:
            for insight in memory.insights:
                print(f"\n{insight.title}  (updated {insight.updated})")
                print(f"  endorsed {insight.endorsed} / disputed {insight.disputed}")
                print(f"  {insight.content[:200]}...")

                # Handles are opaque and must be copied exactly from a result;
                # they cannot be constructed by hand.
                ratings.append(
                    FeedbackRating(
                        idx=insight.idx,
                        relevant=True,
                        correct=True,
                        comment="answered the question directly",
                    )
                )

        # At most 10 ratings per call, so send them in batches.
        recorded = client.memory.share_feedback(
            session_id=session.session_id, feedback=ratings[:10]
        )
        print(f"\nrecorded {len(recorded.entries)} rating(s)")
        for entry in recorded.entries:
            if entry.advice:
                print(f"  {entry.idx}: {entry.advice}")


if __name__ == "__main__":
    main()
