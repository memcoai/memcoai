"""Give a LangChain agent the team's shared memory.

The SDK supplies all of it: what each tool does, the JSON Schema for its
arguments, the results rendered as text, the guidance the service publishes
about the domain, the rule about which failures a model may see, and the handover
to LangChain itself. ``memco.agent`` also speaks Anthropic's and OpenAI's tool
formats, so the same session drives an agent on any of them.

What is left here is the shape of the run, and three things about it are
deliberate — none of them the framework's default.

The session is opened once, in code, before the agent runs, and bound with
``with_session``. The agent never sees a session id and cannot omit or invent
one, so every call it makes is recorded as part of the same series.

Domain guidance is supplied rather than exposed. ``list_domains`` is called
here and ``agent.briefing`` renders its answer into the system prompt, because a
tool the model may forget to call is a tool that does not steer it.

Validation errors go back to the agent and everything else is raised. A
malformed request is something a model can fix on the next turn; a rejected
credential is not, and letting it read that failure only invites it to keep
trying. ``memco.agent.AGENT_RECOVERABLE`` is where that line is drawn.

Run it with::

    export MEMCO_API_TOKEN=...
    export GOOGLE_API_KEY=...           # see gemini.md, next to this file

    uv run --with memco --with langchain --with langchain-google-genai \
        python examples/langchain_agent.py

Any provider LangChain speaks works — set ``MEMCO_EXAMPLE_MODEL`` to
``<provider>:<model>`` for the one you run, such as
``anthropic:claude-opus-5``, and install that provider's package instead.
"""

import os

from langchain.agents import create_agent

from memco import Memco, agent

DOMAIN = "coding"

# Provider-agnostic: any "<provider>:<model>" LangChain understands, given the
# matching provider package. Gemini Flash is the default because it is generally
# available, cheap enough to run the example repeatedly, and calls tools well.
MODEL = os.environ.get("MEMCO_EXAMPLE_MODEL", "google_genai:gemini-3.7-flash")

TASK = (
    "Find out how a client should authenticate against the Memco memory API. "
    "Rate each result you were given. If shared memory did not answer it, say "
    "so plainly rather than guessing."
)


def main() -> None:
    """Run the example."""
    with Memco() as client:
        listed = client.memory.list_domains()
        entry = next((one for one in listed.domains if one.slug == DOMAIN), None)
        if entry is None:
            available = ", ".join(one.slug for one in listed.domains)
            print(f"no domain {DOMAIN!r} for this credential; available: {available}")
            return

        # Opened here, outside the agent loop, and bound to every call the tools
        # make. Nothing the model sends can change or drop it.
        with client.memory.with_session(DOMAIN) as session:
            print(f"session {session.session_id} in {entry.slug}\n")

            runnable = create_agent(
                model=MODEL,
                tools=session.tools().to_langchain(),
                system_prompt=agent.briefing(entry, session.instructions),
            )
            result = runnable.invoke({"messages": [{"role": "user", "content": TASK}]})

        for message in result["messages"]:
            for call in getattr(message, "tool_calls", None) or ():
                print(f"  -> {call['name']}({call['args']})")
        print(f"\n{result['messages'][-1].content}")


if __name__ == "__main__":
    main()
