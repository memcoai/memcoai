"""A LangChain agent wired to shared memory and a web-search tool.

The SDK supplies the tool-building blocks: what each memory tool does, its JSON
Schema, results rendered as text, the service's own guidance on the domain, and
the rule for which failures a model may see versus which end the run. This file
adds only what's specific to the run.

It runs the same task twice, back to back. The first run has nothing in memory
to go on, so it does the full research; if it saves what it found, the second
run can just search memory and skip the research. Both runs report how many
tokens the research took and how long it ran, and the final line compares
them — the cost of writing something down once, against paying to rediscover
it every time.

From a checkout of this repo, with the dev extras synced
(``uv sync --extra dev``), the project's own environment already has
everything this file needs::

    export MEMCO_API_TOKEN=...
    export GOOGLE_API_KEY=...

    uv run examples/langchain_agent.py

From outside the repo, or without syncing first, install the same packages
yourself::

    pip install memco langchain langchain-google-genai ddgs
    python examples/langchain_agent.py

or in one command, with nothing installed up front::

    uv run --with memco --with langchain --with langchain-google-genai --with ddgs \
        python examples/langchain_agent.py

Any provider LangChain speaks works — set ``MEMCO_EXAMPLE_MODEL`` to
``<provider>:<model>`` for the one you run, such as
``anthropic:claude-opus-5``, and install that provider's package instead.

Run for real, this writes a new memory into whatever domain and credential you
point it at, the same as ``contribute.py`` does.
"""

import os
from time import monotonic, sleep

from ddgs import DDGS
from langchain.agents import create_agent
from langchain_core.tools import tool

from memco import Memco, agent
from memco.types import DomainEntry

DOMAIN = "coding"

MODEL = os.environ.get("MEMCO_EXAMPLE_MODEL", "google_genai:gemini-3.1-pro-preview")

IDENTITY = "You are an engineering assistant for the team that builds the Memco SDKs."

# A symptom of (grpc/grpc#41725).
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
        The tokens spent on research (tool-calling turns only — both runs land
        on the same answer, so the final response's tokens are excluded; what
        differs between runs is the research, not restating the result), and
        how long the whole run took in seconds. Tool calls — a web search, a
        memory operation — take real time a token count alone doesn't show.
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
            # Only a tool-calling turn's tokens count as research; the final
            # turn (no tool_calls) is just the answer, the same in both runs.
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
