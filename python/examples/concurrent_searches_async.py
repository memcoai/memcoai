"""Run many searches concurrently on one connection.

One client holds one channel; gRPC multiplexes concurrent calls over it, so
there is no need for a client per task. Failures are handled per task, so one
bad query does not sink the batch.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/concurrent_searches_async.py
"""

import asyncio

from memco import AsyncMemco
from memco.errors import MemcoAPIError
from memco.types import SearchResult

QUERIES = [
    "how does gRPC health checking work",
    "what does a case-sensitive Bearer prefix imply for clients",
    "how should a client tell a transient failure from a permanent one",
    "when should a write be retried",
]


async def search(client: AsyncMemco, query: str, session_id: str) -> tuple[str, str]:
    """Run one search, turning a failure into a reportable result.

    Args:
        client: A connected client.
        query: The query to run.
        session_id: The session to record the search under.

    Returns:
        The query and a one-line summary of how it went.
    """
    try:
        result: SearchResult = await client.memory.search(query, session_id=session_id)
    except MemcoAPIError as exc:
        return query, f"failed: {exc.code.name}"
    insights = sum(len(memory.insights) for memory in result.memories)
    return query, f"{len(result.memories)} memories, {insights} insights"


async def main() -> None:
    """Run the example."""
    async with AsyncMemco() as client:
        session = await client.memory.start_session("coding")

        # All four run concurrently over the single channel.
        results = await asyncio.gather(*(search(client, query, session.id) for query in QUERIES))

        for query, summary in results:
            print(f"{summary:32s} {query}")


if __name__ == "__main__":
    asyncio.run(main())
