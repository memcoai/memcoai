"""Wire a LangChain agent to Memco shared memory and a web-search tool.

The agent runs the same task twice. The first run has nothing in memory to go
on, so it searches the web; if it saves what it finds, the second run can
just search memory instead. Both runs report the tokens and time spent, so
you can see the difference memory makes.

Run it with::

    export MEMCO_API_TOKEN=...
    export GOOGLE_API_KEY=...

    pip install memcoai langchain langchain-google-genai ddgs
    python examples/langchain_agent.py

Any provider LangChain speaks works — set ``MEMCO_EXAMPLE_MODEL`` to
``<provider>:<model>`` for the one you run, such as
``anthropic:claude-opus-5``, and install that provider's package instead.

Running it for real writes a new memory to whatever domain and credential you
point it at.
"""

import os
from time import monotonic, sleep

from ddgs import DDGS
from langchain.agents import create_agent
from langchain_core.tools import tool

from memcoai import Memco, agent
from memcoai.types import DomainEntry

DOMAIN = "coding"

MODEL = os.environ.get("MEMCO_EXAMPLE_MODEL", "google_genai:gemini-3.1-pro-preview")

IDENTITY = "You are an engineering assistant for the team that builds the Memco SDKs."

TASK = (
    "One of our services runs as a Cloud Run Function and calls several Google "
    "Cloud APIs — Secrets Manager, Pub/Sub, BigQuery, and Workflows — over gRPC. "
    "Since upgrading grpcio to 1.78.1, those calls started failing. Find out "
    "what's going on and report it."
)


def run_once(client: Memco, entry: DomainEntry) -> tuple[int, float]:
    """Run one full agent turn on ``TASK``, printing its trace and answer.

    Args:
        client: A connected client.
        entry: The domain to run in, from ``list_domains``.

    Returns:
        Tokens spent researching, and how long the run took in seconds.
    """
    started = monotonic()
    with client.memory.with_session(DOMAIN) as session:
        print(f"session {session.id} in {entry.slug}\n")

        @tool
        def web_search(query: str) -> str:
            """Search the web with DuckDuckGo."""
            try:
                results = DDGS().text(query, max_results=5)
                return "\n".join(f"{r['title']}: {r['body']}" for r in results) or "no results"
            except Exception as error:
                return f"web search failed: {error}"

        tools = [*session.tools().to_langchain(), web_search]
        system_prompt = "\n\n".join((IDENTITY, agent.briefing(entry, session.instructions)))
        runnable = create_agent(model=MODEL, tools=tools, system_prompt=system_prompt)

        total_tokens = 0
        result = None
        for step in runnable.stream(
            {"messages": [{"role": "user", "content": TASK}]}, stream_mode="values"
        ):
            result = step
            message = step["messages"][-1]
            calls = getattr(message, "tool_calls", None) or ()
            for call in calls:
                print(f"  -> {call['name']}({call['args']})", flush=True)
            usage = getattr(message, "usage_metadata", None)
            # Skip the final answer's tokens: it's the same in both runs.
            if usage and calls:
                total_tokens += usage.get("total_tokens", 0)

    elapsed = monotonic() - started
    print(f"\n{result['messages'][-1].text}\n")
    return total_tokens, elapsed


def main() -> None:
    """Run the example twice, to show what shared memory saves the second time."""
    with Memco() as client:
        listed = client.memory.list_domains()
        entry = next((one for one in listed.domains if one.slug == DOMAIN), None)
        if entry is None:
            available = ", ".join(one.slug for one in listed.domains)
            print(f"no domain {DOMAIN!r} for this credential; available: {available}")
            return

        print("=== first run: nothing in memory yet ===\n")
        cold_tokens, cold_seconds = run_once(client, entry)

        print("=== waiting for the write to become searchable ===\n")
        sleep(10)

        print("=== second run: the first run's finding is in memory now ===\n")
        warm_tokens, warm_seconds = run_once(client, entry)

    print("=== summary ===")
    print(f"cold run: {cold_tokens} tokens, {cold_seconds:.1f}s (nothing in memory yet)")
    print(f"warm run: {warm_tokens} tokens, {warm_seconds:.1f}s (memory answered it)")
    if cold_tokens:
        saved_tokens = cold_tokens - warm_tokens
        print(f"tokens saved: {saved_tokens} ({saved_tokens / cold_tokens:.0%})")
    if cold_seconds:
        saved_seconds = cold_seconds - warm_seconds
        print(f"time saved: {saved_seconds:.1f}s ({saved_seconds / cold_seconds:.0%})")


if __name__ == "__main__":
    main()
