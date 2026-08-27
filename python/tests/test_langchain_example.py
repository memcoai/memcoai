"""The example's wiring, exercised through a real agent run.

memco.agent decides what a model is told, what it may send, and how the tools
reach LangChain; tests/test_agent covers all of that against the toolset
directly. What is left to this file is what only a real run shows: that a tool
result comes back as a tool message, and that an error the SDK raises escapes
the run instead of being swallowed into the model's context. The second is a
dependency's default rather than ours, so a version that starts swallowing auth
errors fails the build rather than shipping an agent that hammers a dead
credential.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

import grpc
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from memco import Memco
from memco.errors import MemcoAuthenticationError

from .fake_server import Harness


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


def run_agent(client: Memco, name: str, arguments: dict[str, Any]) -> Any:
    """Run one agent turn that calls exactly one tool.

    Args:
        client: A connected client.
        name: The tool the model should call.
        arguments: The arguments it should call it with.

    Returns:
        The finished run's state.
    """
    with client.memory.with_session("coding") as session:
        model = ScriptedModel(
            messages=iter(
                [
                    AIMessage(
                        content="", tool_calls=[{"name": name, "args": arguments, "id": "1"}]
                    ),
                    AIMessage(content="done"),
                ]
            )
        )
        runnable = create_agent(model=model, tools=session.tools().to_langchain())
        return runnable.invoke({"messages": [{"role": "user", "content": "go"}]})


def told(state: Any) -> list[str]:
    """Collect what the model was handed back from its tool calls."""
    return [message.content for message in state["messages"] if message.type == "tool"]


def test_a_result_reaches_the_model_as_a_tool_message(client: Memco):
    assert told(run_agent(client, "memco_search", {"query": "how does X work"})) == ["0 memories"]


def test_a_malformed_request_comes_back_to_the_agent(client: Memco):
    assert told(run_agent(client, "memco_search", {"query": ""})) == [
        "invalid request: query must not be empty"
    ]


def test_a_missing_argument_comes_back_to_the_agent(client: Memco):
    # LangChain does not enforce `required` on a plain JSON Schema, so the SDK
    # checking it is what keeps this a correction rather than a TypeError that
    # ends the run.
    state = run_agent(client, "memco_search", {"tags": [{"type": "l", "value": "python"}]})
    assert "missing required argument(s): query" in told(state)[0]


def test_a_rejected_credential_ends_the_run(client: Memco, harness: Harness):
    # A model cannot fix a credential, and letting it read the failure only
    # invites it to keep trying against a dead one.
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "credential rejected")
    with pytest.raises(MemcoAuthenticationError):
        run_agent(client, "memco_search", {"query": "how does X work"})


def test_the_example_is_only_the_shape_of_the_run(example: Any):
    # Everything reusable lives in memco.agent, so the example defines no
    # helpers of its own. A helper appearing here is machinery that belongs in
    # the SDK, where every agent builder gets it.
    defined = [
        name
        for name, value in vars(example).items()
        if callable(value) and getattr(value, "__module__", None) == example.__name__
    ]
    assert defined == ["main"]


def test_the_example_names_a_model_langchain_can_resolve(example: Any):
    # The default is a provider string, not a bare model name: init_chat_model
    # cannot infer the provider for a Gemini id, so dropping the prefix would
    # fail only at run time, with a credential already exported.
    provider, _, model = example.MODEL.partition(":")
    assert provider == "google_genai"
    assert model
