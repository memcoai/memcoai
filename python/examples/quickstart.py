"""Connect, see what domains exist, and run one search.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/quickstart.py
"""

from memco import Memco


def main() -> None:
    """Run the example."""
    # Reads MEMCO_API_TOKEN and MEMCO_API_HOST. Constructing the client probes
    # the service's health endpoint and fetches its input limits, so a bad
    # endpoint or a bad token fails here rather than on the first real call.
    with Memco() as client:
        domains = client.memory.describe_domains().domains
        print(f"{len(domains)} domain(s) available:")
        for domain in domains:
            print(f"  {domain.slug:12s} {domain.summary.splitlines()[0]}")

        result = client.memory.search(
            "how should a client authenticate against the memory API",
            domain=domains[0].slug,
        )
        print(f"\n{len(result.memories)} memories for that query:")
        for memory in result.memories:
            print(f"  {memory.idx}  served {memory.times_served}x")
            for insight in memory.insights:
                print(f"    {insight.updated}  {insight.title}")


if __name__ == "__main__":
    main()
