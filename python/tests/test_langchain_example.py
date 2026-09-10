"""The example's wiring, exercised through a real agent run.

memco.agent decides what a model is told, what it may send, and how the tools reach
LangChain; tests/test_agent covers all of that against the toolset directly. What's left
to this file is what only a real run shows: that a tool result comes back as a tool
message, that an error the SDK raises escapes the run instead of being swallowed, that
the web-search tool's own failures reach the model as text instead of crashing it, and
that a memory miss can fall back to the web and get written back — all through the same
agent loop the example builds.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

import ddgs.ddgs as _ddgs_impl
import grpc
import pytest
from ddgs import DDGS
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool

from memco import Memco
from memco.errors import MemcoAuthenticationError

from .fake_server import Harness

QUERY = "grpcio 1.78.1 Cloud Run Functions bug"


class ScriptedModel(GenericFakeChatModel):
    """Emits a fixed sequence of messages, and accepts tools without using them."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedModel:
        """Accept the tool definitions unchanged.

        Args:
            tools: The tools the agent offers. Ignored: the calls are scripted.
            kwargs: Whatever else the agent passes.

        Returns:
            This model.
        """
        return self


def web_search_tool() -> BaseTool:
    """Build the same web-search tool ``run_once`` builds."""

    @tool
    def web_search(query: str) -> str:
        """Search the web with DuckDuckGo."""
        try:
            results = DDGS().text(query, max_results=5)
            return "\n".join(f"{r['title']}: {r['body']}" for r in results) or "no results"
        except Exception as error:
            return f"web search failed: {error}"

    return web_search


@pytest.fixture(scope="module")
def example() -> Any:
    """Import the example as a module, the way the suite imports every example."""
    path = pathlib.Path(__file__).parent.parent / "examples" / "langchain_agent.py"
    spec = importlib.util.spec_from_file_location("langchain_agent", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_agent(client: Memco, calls: list[dict[str, Any]]) -> Any:
    """Run one agent turn through a scripted sequence of tool calls, then stop.

    Args:
        client: A connected client.
        calls: One ``{"name": ..., "args": ...}`` per tool call the model should
            make, in the order it should make them.

    Returns:
        The finished run's state.
    """
    with client.memory.with_session("coding") as session:
        tools = [*session.tools().to_langchain(), web_search_tool()]
        scripted = [
            AIMessage(content="", tool_calls=[{**call, "id": str(position)}])
            for position, call in enumerate(calls, start=1)
        ]
        model = ScriptedModel(messages=iter([*scripted, AIMessage(content="done")]))
        runnable = create_agent(model=model, tools=tools)
        return runnable.invoke({"messages": [{"role": "user", "content": "go"}]})


def told(state: Any) -> list[str]:
    """Collect what the model was handed back from its tool calls."""
    return [message.content for message in state["messages"] if message.type == "tool"]


def test_a_malformed_request_comes_back_to_the_agent(client: Memco):
    state = run_agent(client, [{"name": "memco_search", "args": {"query": ""}}])
    assert told(state) == ["invalid request: query must not be empty"]


def test_a_missing_argument_comes_back_to_the_agent(client: Memco):
    # LangChain does not enforce `required` on a plain JSON Schema, so the SDK
    # checking it is what keeps this a correction rather than a TypeError that
    # ends the run.
    state = run_agent(
        client, [{"name": "memco_search", "args": {"tags": [{"type": "l", "value": "python"}]}}]
    )
    assert "missing required argument(s): query" in told(state)[0]


def test_a_rejected_credential_ends_the_run(client: Memco, harness: Harness):
    # A model cannot fix a credential, and letting it read the failure only
    # invites it to keep trying against a dead one.
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "credential rejected")
    with pytest.raises(MemcoAuthenticationError):
        run_agent(client, [{"name": "memco_search", "args": {"query": "how does X work"}}])


def test_a_memory_miss_falls_back_to_the_web_and_writes_a_memory_back(
    client: Memco, harness: Harness, monkeypatch: pytest.MonkeyPatch
):
    # Keeps the web search hermetic, the same way Harness keeps the memco calls
    # hermetic: no test in this suite may make a real network call. Patched on
    # ddgs.ddgs.DDGS, not the DDGS re-exported by the package: that name is a
    # proxy whose call returns an instance of this real class, so patching the
    # proxy itself would silently do nothing and let a real search through.
    monkeypatch.setattr(
        _ddgs_impl.DDGS,
        "text",
        lambda self, query, **kwargs: [{"title": "grpc/grpc#41725", "body": "fixed in 1.79.0"}],
    )

    state = run_agent(
        client,
        [
            {"name": "memco_search", "args": {"query": QUERY}},
            {"name": "web_search", "args": {"query": QUERY}},
            {
                "name": "memco_create_memory",
                "args": {
                    "query": QUERY,
                    "title": "grpcio 1.78.1 Cloud Run Functions bug",
                    "content": "tracked as grpc/grpc#41725; fixed in 1.79.0",
                },
            },
        ],
    )

    said = told(state)
    assert said[0] == "0 memories"
    assert "grpc/grpc#41725" in said[1]
    assert "accepted as operation create-a" in said[2]
    assert harness.memory.requests["CreateMemory"].title == "grpcio 1.78.1 Cloud Run Functions bug"


def test_a_web_search_failure_reaches_the_model_as_text_instead_of_crashing(
    client: Memco, monkeypatch: pytest.MonkeyPatch
):
    # A raw exception from the underlying search call, exactly like a real DNS or
    # rate-limit failure — the wrapping in web_search_tool() (and main()'s copy of
    # it) must turn this into a tool message rather than letting it end the run.
    def boom(self: _ddgs_impl.DDGS, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("DNS error: no records found")

    monkeypatch.setattr(_ddgs_impl.DDGS, "text", boom)

    state = run_agent(client, [{"name": "web_search", "args": {"query": QUERY}}])

    assert told(state) == ["web search failed: DNS error: no records found"]


def test_the_example_defines_no_wiring_helpers_of_its_own(example: Any):
    # Everything reusable for talking to LangChain lives in memco.agent, so the
    # example defines no wiring helpers of its own. run_once is the exception:
    # it is this example's own two-runs-and-compare orchestration, not
    # framework plumbing, so it has nowhere else to live.
    defined = {
        name
        for name, value in vars(example).items()
        if callable(value) and getattr(value, "__module__", None) == example.__name__
    }
    assert defined == {"main", "run_once"}


def test_the_example_names_a_model_langchain_can_resolve(example: Any):
    # The default is a provider string, not a bare model name: init_chat_model
    # cannot infer the provider for a Gemini id, so dropping the prefix would
    # fail only at run time, with a credential already exported.
    provider, _, model = example.MODEL.partition(":")
    assert provider == "google_genai"
    assert model
