"""The same as quickstart.py, on the asyncio client.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/quickstart_async.py
"""

import asyncio

from memco import AsyncMemco


async def main() -> None:
    """Run the example."""
    # AsyncMemco cannot reach the service in __init__, because that needs a
    # running event loop. `async with` calls connect() for you, which probes
    # health and fetches the input limits; construct it inside the loop that
    # will use it.
    async with AsyncMemco() as client:
        domains = (await client.memory.describe_domains()).domains
        print(f"{len(domains)} domain(s) available:")
        for domain in domains:
            print(f"  {domain.slug:12s} {domain.summary.splitlines()[0]}")

        result = await client.memory.search(
            "how should a client authenticate against the memory API",
            domain=domains[0].slug,
        )
        print(f"\n{len(result.memories)} memories for that query:")
        for memory in result.memories:
            for insight in memory.insights:
                print(f"  {insight.updated}  {insight.title}")


if __name__ == "__main__":
    asyncio.run(main())
