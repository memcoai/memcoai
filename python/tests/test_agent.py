"""The operations as agent tools: what a model is told, and what it may send.

These are the SDK's answers to questions every agent builder would otherwise
answer for themselves, so they are pinned here rather than in an example.
"""

from __future__ import annotations

import datetime
import inspect

import grpc
import pytest

from memco import NEW_MEMORY, AsyncMemco, Memco, agent, types
from memco.errors import (
    MemcoAuthenticationError,
)
from memco.memory.v1 import memory_pb2 as pb
from memco.operations import SessionScope

from .fake_server import Harness

BOUND = {"session_id", "domain", "timeout", "source"}


def built(client: Memco) -> dict[str, agent.Tool]:
    """Build the tools for one session, by name."""
    return {tool.name: tool for tool in client.memory.with_session("coding").tools()}


# `tools` is how a caller reaches the toolset, not an operation to put in it.
# Naming it here is the point: anything else added to the scope has to be
# decided about rather than becoming model-callable on its own.
NOT_AN_OPERATION = {"tools"}


def test_the_offered_operations_are_exactly_what_the_scope_carries():
    # The list is written out rather than read off the scope: a tool is reachable
    # by an untrusted model, so a helper added to the scope must not become one
    # by accident. This keeps the written list honest in the other direction —
    # an operation added and not offered fails here.
    carried = {
        name
        for name, _ in inspect.getmembers(SessionScope, inspect.isfunction)
        if not name.startswith("_")
    }
    assert set(agent._OPERATIONS) == carried - NOT_AN_OPERATION


def test_no_tool_lets_a_model_supply_what_the_caller_binds(client: Memco):
    # A model naming its own session would defeat the scope; one naming its own
    # source could claim a person wrote what it wrote.
    for tool in built(client).values():
        assert not BOUND & set(tool.parameters["properties"]), tool.name


def test_every_tool_and_parameter_carries_a_description(client: Memco):
    # On most surfaces a tool's description is the only text that reaches the
    # model, so an empty one is a silent loss of every steer the SDK gives.
    for tool in built(client).values():
        assert tool.description.strip(), tool.name
        for name, schema in tool.parameters["properties"].items():
            assert schema["description"].strip(), f"{tool.name}.{name}"


def test_a_description_stops_at_the_first_section(client: Memco):
    # Returns, Raises and Example are for a developer reading the reference.
    description = built(client)["memco_search"].description
    assert description.startswith("Search for memories")
    for section in ("Returns:", "Raises:", "Example:", ">>>"):
        assert section not in description


def test_sphinx_markup_never_reaches_the_model(client: Memco):
    for tool in built(client).values():
        rendered = tool.description + "".join(
            schema["description"] for schema in tool.parameters["properties"].values()
        )
        assert ":meth:" not in rendered
        assert ":class:" not in rendered
        assert "``" not in rendered


def test_required_names_the_arguments_without_a_default(client: Memco):
    tools = built(client)
    assert tools["memco_search"].parameters["required"] == ["query"]
    assert set(tools["memco_create_memory"].parameters["required"]) == {
        "query",
        "title",
        "content",
    }


def test_a_structured_argument_is_described_from_this_package_s_own_type(client: Memco):
    # Derived from types.Tag, so a field added there reaches the model without
    # anyone remembering to update a second copy of its shape.
    schema = built(client)["memco_search"].parameters["properties"]["tags"]
    assert schema["type"] == "array"
    assert set(schema["items"]["properties"]) == {"type", "value", "version"}
    assert schema["items"]["required"] == ["type", "value"]


def test_a_tool_renders_the_result_rather_than_returning_it(client: Memco, harness: Harness):
    harness.memory.responses["Search"] = _search_response()
    rendered = built(client)["memco_search"].call(query="how does X work")
    assert "memory-a-1" in rendered
    assert "the title" in rendered
    # The service's own guidance reaches the model, which is the point.
    assert "search again as questions arise" in rendered


def test_a_tag_arrives_as_an_object_and_reaches_the_wire(client: Memco, harness: Harness):
    built(client)["memco_search"].call(
        query="how does X work", tags=[{"type": "language", "value": "python"}]
    )
    sent = harness.memory.requests["Search"].tags
    assert [(tag.type, tag.value) for tag in sent] == [("language", "python")]


def test_a_rating_arrives_as_an_object_and_reaches_the_wire(client: Memco, harness: Harness):
    built(client)["memco_share_feedback"].call(
        feedback=[{"idx": "memory-a-1", "relevant": True, "correct": False}]
    )
    sent = harness.memory.requests["ShareFeedback"].feedback
    assert [(one.idx, one.relevant, one.correct) for one in sent] == [("memory-a-1", True, False)]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, "missing required argument(s): query"),
        ({"query": None}, "missing required argument(s): query"),
        ({"query": ""}, "query must not be empty"),
        ({"query": 123}, "query must be a string, not int"),
        ({"query": ["a"]}, "query must be a string, not list"),
        ({"query": "q", "tags": "python"}, "tags must be a list"),
        ({"query": "q", "tags": ["python"]}, "tags must hold objects"),
        ({"query": "q", "tags": [{"value": "python"}]}, "tags is missing type"),
        ({"query": "q", "tags": [{"type": 1, "value": "x"}]}, "tags.type must be a string"),
        ({"query": "q", "invented": 1}, "unknown argument(s): invented"),
    ],
)
def test_what_a_model_gets_wrong_comes_back_as_text(
    client: Memco, harness: Harness, arguments: dict[str, object], expected: str
):
    # Every one of these is something the model can fix on the next turn, so
    # none of them may escape as an exception and end the run.
    rendered = built(client)["memco_search"].call(**arguments)
    assert expected in rendered
    assert rendered.startswith("invalid request")


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("memco_get_memory", {"idx": 5}, "idx must be a string, not int"),
        (
            "memco_share_feedback",
            {"feedback": [{"idx": "m", "relevant": "yes", "correct": True}]},
            "feedback.relevant must be true or false, not str",
        ),
        (
            "memco_share_feedback",
            {"feedback": [{"idx": "m", "relevant": 1, "correct": True}]},
            "feedback.relevant must be true or false, not int",
        ),
        (
            "memco_enrich_memory",
            {"memory_idx": "new", "title": "t", "content": "c", "sources": [1]},
            "sources[0] must be a string, not int",
        ),
    ],
)
def test_a_scalar_of_the_wrong_type_comes_back_as_text(
    client: Memco, tool: str, arguments: dict[str, object], expected: str
):
    # Unchecked, every one of these reaches validation as an AttributeError and
    # ends the run instead of the turn.
    rendered = built(client)[tool].call(**arguments)
    assert expected in rendered
    assert rendered.startswith("invalid request")


def test_a_null_optional_field_is_the_same_as_leaving_it_out(client: Memco, harness: Harness):
    built(client)["memco_search"].call(
        query="q", tags=[{"type": "language", "value": "python", "version": None}]
    )
    assert harness.memory.requests["Search"].tags[0].value == "python"


def test_a_key_the_shape_does_not_have_is_ignored(client: Memco, harness: Harness):
    # An invented key costs nothing; a refused call costs the model a turn.
    built(client)["memco_search"].call(
        query="q", tags=[{"type": "language", "value": "python", "invented": "x"}]
    )
    assert harness.memory.requests["Search"].tags[0].type == "language"


def test_a_handle_that_resolves_to_nothing_comes_back_as_text(client: Memco, harness: Harness):
    tool = built(client)["memco_get_memory"]
    harness.memory.error = (grpc.StatusCode.NOT_FOUND, "no such memory")
    assert tool.call(idx="memory-invented-9").startswith("nothing found")


def test_a_rejected_credential_is_raised_rather_than_rendered(client: Memco, harness: Harness):
    tool = built(client)["memco_search"]
    harness.memory.error = (grpc.StatusCode.UNAUTHENTICATED, "credential rejected")
    with pytest.raises(MemcoAuthenticationError):
        tool.call(query="how does X work")


def test_a_reference_tells_the_model_how_to_fetch_what_it_stands_for():
    memory = types.Memory(
        idx="memory-a-2",
        kind="",
        times_served=1,
        intents=(),
        insights=(),
        reference="memory-a-1",
    )
    rendered = agent.render(memory)
    assert "memory-a-1" in rendered
    assert "memco_get_memory" in rendered


def test_render_refuses_what_it_has_no_rendering_for():
    # The overloads stop a typed caller reaching this; the guard is for the
    # untyped ones, which is most agent code.
    with pytest.raises(TypeError):
        agent.render(object())  # type: ignore[arg-type]


def test_the_briefing_carries_what_the_service_said_about_the_domain():
    entry = types.DomainEntry(
        slug="coding",
        title="Software Development",
        summary="What one engineer learned the hard way.",
        when_to_search="At the start of a task.",
        when_to_save="After discovering something non-obvious.",
        what_not_to_save="Secrets.",
        tags_description="| language | python |",
        filter_tag_types=("language",),
        version_tag_types=("library",),
        max_tags_per_query=4,
    )
    rendered = agent.briefing(entry, types.Instructions("open with a search", "", "", "", ""))
    for expected in (
        "coding",
        "What one engineer learned the hard way.",
        "At the start of a task.",
        "After discovering something non-obvious.",
        "Secrets.",
        "language",
        "library",
        "4 tags",
        "open with a search",
    ):
        assert expected in rendered


def _search_response() -> pb.SearchResponse:
    """Build a search response carrying an insight and some guidance."""
    return pb.SearchResponse(
        session_id="session-a",
        memories=[
            pb.MemoryResult(
                idx="memory-a-1",
                times_served=3,
                insights=[
                    pb.InsightResult(
                        idx="memory-a-1-insight-1",
                        title="the title",
                        content="the content",
                        updated="2026-08-01",
                    )
                ],
            )
        ],
        instructions=pb.Instructions(content="search again as questions arise"),
    )


# -- framework adapters ---------------------------------------------------


def test_the_anthropic_shape_carries_the_schema_unchanged(client: Memco):
    toolset = client.memory.with_session("coding").tools()
    described = {one["name"]: one for one in toolset.to_anthropic()}
    assert set(described) == {tool.name for tool in toolset}
    for tool in toolset:
        assert described[tool.name]["description"] == tool.description
        assert described[tool.name]["input_schema"] == tool.parameters


def test_the_openai_shape_nests_the_schema_where_that_api_wants_it(client: Memco):
    toolset = client.memory.with_session("coding").tools()
    described = {one["function"]["name"]: one for one in toolset.to_openai()}
    assert set(described) == {tool.name for tool in toolset}
    for tool in toolset:
        assert described[tool.name]["type"] == "function"
        assert described[tool.name]["function"]["parameters"] == tool.parameters


def test_the_langchain_shape_restates_nothing(client: Memco):
    toolset = client.memory.with_session("coding").tools()
    built = {one.name: one for one in toolset.to_langchain()}
    assert set(built) == {tool.name for tool in toolset}
    for tool in toolset:
        assert built[tool.name].description == tool.description
        assert built[tool.name].args_schema == tool.parameters
    assert built["memco_search"].invoke({"query": "how does X work"}) == "0 memories"


def test_a_toolset_runs_the_tool_a_model_named(client: Memco, harness: Harness):
    # The definition-only shapes leave dispatch to the host, so the toolset has
    # to be able to do it.
    toolset = client.memory.with_session("coding").tools()
    assert toolset.call("memco_search", {"query": "how does X work"}) == "0 memories"


def test_a_toolset_reports_an_invented_tool_name_rather_than_raising(client: Memco):
    # A model that hallucinates a name can pick again from the list it is given.
    toolset = client.memory.with_session("coding").tools()
    rendered = toolset.call("memco_delete_everything", {})
    assert "no tool named" in rendered
    assert "memco_search" in rendered


def test_saving_new_knowledge_reports_the_operation_that_can_undo_it(
    client: Memco, harness: Harness
):
    rendered = built(client)["memco_create_memory"].call(
        query="how do I authenticate", title="Bearer token", content="The prefix is checked."
    )
    assert "create-a" in rendered
    assert harness.memory.requests["CreateMemory"].title == "Bearer token"


def test_a_write_that_cannot_be_undone_says_so(client: Memco, harness: Harness):
    tool = built(client)["memco_create_memory"]
    harness.memory.responses["CreateMemory"] = pb.CreateMemoryResponse()
    assert "cannot be undone" in tool.call(query="q", title="t", content="c")


def test_adding_to_a_memory_reaches_the_wire(client: Memco, harness: Harness):
    rendered = built(client)["memco_enrich_memory"].call(
        memory_idx=NEW_MEMORY, title="t", content="c", sources=["memory-a-1"]
    )
    assert "enrich-a" in rendered
    assert harness.memory.requests["EnrichMemory"].memory_idx == NEW_MEMORY
    assert list(harness.memory.requests["EnrichMemory"].sources) == ["memory-a-1"]


def test_undoing_a_write_reports_the_outcome_rather_than_failing(client: Memco):
    assert built(client)["memco_revert_memory"].call(operation_id="create-a") == (
        "create-a: merged"
    )


def test_a_rating_comes_back_with_the_advice_it_earned(client: Memco, harness: Harness):
    tool = built(client)["memco_share_feedback"]
    harness.memory.responses["ShareFeedback"] = pb.ShareFeedbackResponse(
        session_id="session-a",
        entries=[
            pb.FeedbackEntry(
                idx="memory-a-1", relevant=True, correct=False, advice="say what was wrong"
            )
        ],
    )
    rendered = tool.call(feedback=[{"idx": "memory-a-1", "relevant": True, "correct": False}])
    assert "say what was wrong" in rendered


# -- what a framework hands back ------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"query": "how does X work"}', "0 memories"),
        ("not json at all", "arguments are not valid JSON"),
        ('"just a string"', "arguments must be an object, not str"),
        ("[1, 2]", "arguments must be an object, not list"),
        (None, "arguments must be an object, not NoneType"),
        (42, "arguments must be an object, not int"),
        ({1: "x"}, "argument names must be strings"),
    ],
)
def test_a_toolset_survives_whatever_a_framework_hands_back(
    client: Memco, arguments: object, expected: str
):
    # to_openai's docstring points callers at call(), and that API delivers
    # arguments as a JSON string. Splatting one — or anything else that is not a
    # mapping — is a TypeError that ends the run rather than the turn.
    toolset = client.memory.with_session("coding").tools()
    assert expected in toolset.call("memco_search", arguments)  # type: ignore[arg-type]


# -- what the model is told about a result --------------------------------


def test_an_insight_carries_the_signal_that_says_whether_to_trust_it():
    # times_served counts deliveries; endorsed and disputed are what say whether
    # a result was any good, so a model that never sees them cannot discount one.
    memory = types.Memory(
        idx="memory-a-1",
        kind="policy",
        times_served=9,
        intents=("how do I authenticate",),
        insights=(
            types.Insight(
                idx="memory-a-1-insight-1",
                title="T",
                content="C",
                updated=datetime.date(2026, 8, 1),
                times_served=4,
                endorsed=7,
                disputed=3,
            ),
        ),
        reference=None,
    )
    rendered = agent.render(memory)
    for expected in ("policy", "how do I authenticate", "2026-08-01", "endorsed 7x", "disputed 3x"):
        assert expected in rendered


def test_an_insight_that_was_never_updated_does_not_render_the_word_none():
    memory = types.Memory(
        idx="memory-a-1",
        kind="",
        times_served=1,
        intents=(),
        insights=(
            types.Insight(
                idx="i",
                title="T",
                content="C",
                updated=None,
                times_served=1,
                endorsed=0,
                disputed=0,
            ),
        ),
        reference=None,
    )
    assert "None" not in agent.render(memory)


def test_one_result_is_not_reported_as_one_memories(client: Memco, harness: Harness):
    harness.memory.responses["Search"] = _search_response()
    assert built(client)["memco_search"].call(query="q").startswith("1 memory\n")


# -- schema hygiene -------------------------------------------------------


def test_a_schema_fragment_is_never_shared_between_tools(client: Memco):
    # A framework that normalises schemas in place would otherwise reach through
    # and corrupt every string schema in the process.
    toolset = client.memory.with_session("coding").tools()
    by_name = {tool.name: tool for tool in toolset}
    query = by_name["memco_search"].parameters["properties"]["query"]
    query["title"] = "mutated by a framework"
    fresh = {tool.name: tool for tool in client.memory.with_session("coding").tools()}
    assert "title" not in fresh["memco_search"].parameters["properties"]["query"]
    assert "title" not in by_name["memco_get_memory"].parameters["properties"]["idx"]


def test_a_nested_field_is_described_too(client: Memco):
    # Tool.parameters promises a description on each argument; the ones a model
    # most needs are inside the objects.
    for tool in built(client).values():
        for name, schema in tool.parameters["properties"].items():
            if schema.get("type") == "array" and schema["items"].get("type") == "object":
                for field, described in schema["items"]["properties"].items():
                    assert described["description"].strip(), f"{tool.name}.{name}.{field}"


def test_a_cross_reference_to_an_operation_names_the_tool():
    # "rate it with share_feedback" points a model at a tool that does not
    # exist, and it has no other way to find the one that does.
    assert agent._reference("share_feedback") == "memco_share_feedback"
    assert agent._reference("MemoryOperations.revert_memory") == "memco_revert_memory"
    # Anything that is not an operation keeps only its last component.
    assert agent._reference("memco.types.DataSource.AGENT") == "AGENT"
    assert agent._reference("describe_domains") == "describe_domains"


def test_the_descriptions_that_cross_reference_a_tool_name_it_in_full(client: Memco):
    tools = built(client)
    assert "memco_share_feedback" in tools["memco_search"].description
    assert "memco_revert_memory" in tools["memco_create_memory"].description


def test_the_builder_refuses_anything_that_is_not_its_own_scope():
    # Reached through session.tools() a caller cannot get this wrong, but the
    # builder is what enforces it: given the other surface's scope it would
    # otherwise fail as "no rendering for coroutine", which says nothing.
    with pytest.raises(TypeError, match="SessionScope"):
        agent._tools(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AsyncSessionScope"):
        agent._async_tools(object())  # type: ignore[arg-type]


async def test_both_surfaces_tell_a_model_exactly_the_same_thing(
    async_client: AsyncMemco, client: Memco
):
    # Nothing forces the two to agree, and a model steered differently by the
    # async client would be a difference nobody would think to look for.
    asynchronous = await async_client.memory.with_session("coding")
    left = {tool.name: tool for tool in client.memory.with_session("coding").tools()}
    right = {tool.name: tool for tool in asynchronous.tools()}
    assert set(left) == set(right)
    for name, tool in left.items():
        assert tool.description == right[name].description, name
        assert tool.parameters == right[name].parameters, name


async def test_the_async_toolset_awaits_the_call_and_speaks_the_same_shapes(
    async_client: AsyncMemco,
):
    toolset = (await async_client.memory.with_session("coding")).tools()
    assert {one["name"] for one in toolset.to_anthropic()} == {tool.name for tool in toolset}
    assert {one["function"]["name"] for one in toolset.to_openai()} == {t.name for t in toolset}
    assert await toolset.call("memco_search", {"query": "how does X work"}) == "0 memories"
    assert "no tool named" in await toolset.call("memco_invented", {})
    assert "invalid request" in await toolset.call("memco_search", {"query": ""})
    built = {one.name: one for one in toolset.to_langchain()}
    assert await built["memco_search"].ainvoke({"query": "how does X work"}) == "0 memories"
