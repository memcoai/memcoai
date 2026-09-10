"""The copy a model reads is the service's, unchanged.

``memcoai/memory/tools.json`` carries the agent-facing descriptions the hosted MCP
server publishes, and ``scripts/sync_tool_docs.py`` writes them into the
docstrings ``memcoai.agent`` reads. Nothing at runtime opens the manifest, so
these tests are what holds the two ends together: they read it directly and
assert the copy survived the round trip into a tool definition.

The same script emits ``nodejs/src/gen/toolCopy.ts``, which has no docstrings to
read back and so states its copy outright. What that target can be held to from
here is what only Python can see — that the script and ``memcoai.agent`` still
agree on the tables both steer by, and that the emitter's own rules hold.
``nodejs/tests/toolCopy.test.ts`` checks the module against the manifest.

A failure here means the docstrings have drifted from the manifest. Run
``python3 scripts/sync_tool_docs.py`` and commit the result.
"""

from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from memcoai import Memco, agent, types
from memcoai.memory.v1 import memory_pb2 as pb

MANIFEST = Path(__file__).resolve().parents[1] / "memcoai" / "memory" / "tools.json"
MARKER = re.compile(r"\$\{tool:([a-z_]+)\}")

# The manifest describes what the MCP server accepts; these three take a
# different shape here, so `scripts/sync_tool_docs.py` leaves their copy
# hand-written. Kept in step with SDK_SHAPED there.
SDK_SHAPED = frozenset({"tags", "feedback", "source"})

# The one manifest key the SDK spells differently. Kept in step with
# PARAMETER_ALIASES in the generator.
ALIASES = {"op_id": "operation_id"}


@pytest.fixture(scope="module")
def manifest() -> dict[str, dict[str, object]]:
    """The published tool definitions, by name."""
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {tool["name"]: tool for tool in document["tools"]}


@pytest.fixture
def tools(client: Memco) -> dict[str, agent.Tool]:
    """The tools one session offers, keyed by the manifest name they came from."""
    built = client.memory.with_session("coding").tools()
    return {tool.name.removeprefix(agent._PREFIX): tool for tool in built}


def spelled(text: str) -> str:
    """Render the manifest's markers the way a model is given them."""
    return MARKER.sub(lambda match: agent._reference(match.group(1)), text)


def same(left: str, right: str) -> bool:
    """Compare copy without minding how it was wrapped.

    The manifest separates its lines with a single newline and the docstring
    renders them as paragraphs, so the whitespace differs by construction. It is
    the words that are under test.
    """
    return " ".join(left.split()) == " ".join(right.split())


def test_the_manifest_publishes_every_operation_and_then_some(manifest):
    # The toolset is the subset a bound session can offer; list_domains and
    # start_session are the questions the scope has already answered.
    assert set(manifest) >= set(agent._OPERATIONS)
    assert set(manifest) - set(agent._OPERATIONS) == {
        "import_memories",
        "list_domains",
        "start_session",
    }


def test_every_description_is_the_service_copy(tools, manifest):
    for name, tool in tools.items():
        assert same(tool.description, spelled(manifest[name]["description"])), name


def test_every_parameter_the_manifest_names_carries_its_copy(tools, manifest):
    compared = set()
    for name, tool in tools.items():
        for key, described in manifest[name]["parameters"].items():
            # Through the alias table first: the manifest calls it op_id and the
            # SDK calls it operation_id, and looking it up by the manifest's own
            # spelling silently compared nothing for revert_memory.
            parameter = ALIASES.get(key, key)
            if parameter in SDK_SHAPED:
                continue
            schema = tool.parameters["properties"].get(parameter)
            if schema is None:
                continue  # Bound by the scope, so never shown to a model.
            assert same(schema["description"], spelled(described)), f"{name}.{parameter}"
            compared.add(f"{name}.{parameter}")
    # Pinned, because every skip above is silent: a lookup that stops matching
    # would otherwise leave this passing while comparing less and less.
    assert compared == {
        "create_memory.content",
        "create_memory.query",
        "create_memory.title",
        "enrich_memory.content",
        "enrich_memory.memory_idx",
        "enrich_memory.sources",
        "enrich_memory.title",
        "get_memory.idx",
        "revert_memory.operation_id",
        "search.query",
    }


def test_a_parameter_the_sdk_shapes_differently_keeps_its_own_copy(tools):
    # The manifest describes tags as a list of XML strings. This SDK takes Tag,
    # and the model is handed an object schema built from it, so the wire copy
    # would describe an encoding the schema rejects.
    described = tools["search"].parameters["properties"]["tags"]["description"]
    assert "<tag" not in described
    assert tools["search"].parameters["properties"]["tags"]["type"] == "array"


def test_no_marker_ever_reaches_a_model(tools):
    for name, tool in tools.items():
        assert "${" not in tool.description, name
        for parameter, schema in tool.parameters["properties"].items():
            assert "${" not in schema["description"], f"{name}.{parameter}"


def test_every_tool_the_manifest_publishes_is_an_rpc_the_contract_declares(manifest):
    # Asserted against the generated descriptor rather than a literal, so a
    # codegen change that moves the spelling fails here instead of shipping.
    # The whole path, not just its last segment: a manifest naming the right
    # method on the wrong service would otherwise pass.
    service = pb.DESCRIPTOR.services_by_name["MemoryService"]
    paths = {f"/{service.full_name}/{method}" for method in service.methods_by_name}
    for name, tool in manifest.items():
        assert tool["rpc"] in paths, name


def test_a_tool_named_in_the_copy_is_named_as_a_model_would_call_it(tools, manifest):
    # "rate it with share_feedback" points a model at a tool that does not
    # exist, and it has no other way to find the one that does. Derived from the
    # manifest rather than pinned, so the next export cannot rot it.
    named = 0
    for name, tool in tools.items():
        for referenced in MARKER.findall(manifest[name]["description"]):
            if referenced not in agent._OPERATIONS:
                continue  # Not a tool here; the briefing says so.
            assert agent._PREFIX + referenced in tool.description, f"{name} -> {referenced}"
            named += 1
    assert named, "no cross-reference was exercised, so this proved nothing"


def test_the_nested_copy_reaches_the_dataclasses_that_carry_it(manifest):
    # The manifest states nested request fields as paths. Nothing else checks
    # that they landed, and the reference is what a developer reads.
    script = sync_tool_docs()
    published = manifest["import_memories"]["parameters"]
    for path, (class_name, attribute) in script.NESTED.items():
        if attribute in SDK_SHAPED:
            continue
        documented = agent._documented(
            (inspect.getdoc(getattr(types, class_name)) or "").splitlines(), "Attributes"
        )
        assert same(" ".join(documented[attribute]), published[path]), path


def sync_tool_docs() -> ModuleType:
    """Load the generator, which lives outside the package and ships with none."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "sync_tool_docs.py"
    spec = importlib.util.spec_from_file_location("sync_tool_docs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generator_refuses_a_marker_it_cannot_spell():
    # Passing an unrecognised marker through would put a literal ${tool:...} in
    # front of a model, which is a description of a tool that does not exist.
    script = sync_tool_docs()
    render = script.resolver({"search"}, {"search"}, "MemoryOperations")
    assert render("call ${tool:search}") == "call :meth:`search`"
    with pytest.raises(script.Drift, match="unknown tool"):
        render("call ${tool:teleport}")


def test_the_generator_refuses_copy_a_docstring_would_eat():
    # A backslash in a non-raw docstring is an escape, so the copy a model reads
    # would differ from the copy the service wrote.
    script = sync_tool_docs()
    assert script.checked("plain text", "where") == "plain text"
    with pytest.raises(script.Drift, match="backslash"):
        script.checked(r"a \"quoted\" example", "where")


def test_the_generator_and_this_suite_agree_on_what_it_leaves_alone():
    assert sync_tool_docs().SDK_SHAPED == SDK_SHAPED


def test_every_operation_the_copy_names_is_a_tool_or_is_answered(tools, manifest):
    # The service's copy points at list_domains and start_session, which are
    # right for the surface it was written for and are not tools here. A model
    # told to call one has been pointed at nothing, so the briefing has to say
    # so — and this is what notices when the copy starts naming a third.
    named: set[str] = set()
    for name in tools:
        tool = manifest[name]
        for text in [tool["description"], *tool["parameters"].values()]:
            named |= set(MARKER.findall(text))
    assert named - set(agent._OPERATIONS) == set(agent._ANSWERED)


def test_the_briefing_says_which_operations_are_not_tools():
    entry = types.DomainEntry(
        slug="coding",
        title="Software Development",
        summary="",
        when_to_search="",
        when_to_save="",
        what_not_to_save="",
        tags_description="",
        filter_tag_types=(),
        version_tag_types=(),
        max_tags_per_query=0,
    )
    briefing = agent.briefing(entry, types.Instructions("", "", "", "", ""))
    for name in agent._ANSWERED:
        assert name in briefing, name
    assert "not among your tools" in briefing


@pytest.fixture
def published(manifest) -> dict[str, dict[str, Any]]:
    """The manifest, deep-copied so a test can spoil a copy of it."""
    return copy.deepcopy(manifest)


def operations_source() -> str:
    return (Path(__file__).resolve().parents[1] / "memcoai" / "operations.py").read_text("utf-8")


def test_the_generator_refuses_copy_that_reads_as_a_section_header(published):
    # Prose and sections are told apart by the same pattern on the way back in,
    # so this copy has no fixed point: each run would re-file everything after
    # the word as a section and write it again. `agent._describe` stops the
    # model-facing summary there too, so the rest would be dropped in silence.
    script = sync_tool_docs()
    published["get_memory"]["description"] = "Fetch one memory.\nReturns:\nThe memory in full."
    with pytest.raises(script.Drift, match="section header"):
        script.sync_operations(operations_source(), published)


def test_the_generator_refuses_a_marker_it_cannot_parse(published):
    # Only markers the pattern matches are substituted; the rest would be
    # written out as literal text and read by a model.
    script = sync_tool_docs()
    published["search"]["description"] = "Search things. See ${tool:Search}."
    with pytest.raises(script.Drift, match="not a tool reference"):
        script.sync_operations(operations_source(), published)


def test_the_generator_refuses_to_drop_copy_the_manifest_carries(published):
    # A renamed or added field must not be skipped in silence, leaving the
    # docstring steering a model with copy the service has retired.
    script = sync_tool_docs()
    published["search"]["parameters"]["search_query"] = published["search"]["parameters"].pop(
        "query"
    )
    with pytest.raises(script.Drift, match="describes no query"):
        script.sync_operations(operations_source(), published)


def test_the_generator_refuses_a_nested_path_it_has_no_home_for(published):
    script = sync_tool_docs()
    published["import_memories"]["parameters"]["memories[].queries[].text"] = "new"
    with pytest.raises(script.Drift, match="nothing carries"):
        script.sync_operations(operations_source(), published)


def test_the_generator_is_idempotent(manifest):
    # The writer is run after every export and its output is committed, so a
    # second run that changes anything means the copy has no fixed point.
    script = sync_tool_docs()
    once = script.sync_operations(operations_source(), manifest)
    assert script.sync_operations(once, manifest) == once


# -- the Node target ------------------------------------------------------
#
# `nodejs/src/gen/toolCopy.ts` is emitted, not rewritten, so drift is plain
# string inequality and there is no AST to read the copy back out of. These
# cover what the Node suite cannot see: that the script's copies of the tables
# `memcoai.agent` steers by still match it, and that the emitter's own rules hold.


def test_the_generator_and_the_sdk_agree_on_how_a_tool_is_named():
    # The Node target spells the manifest's markers itself, so it carries its
    # own copy of the three tables agent.py steers by. Two spellings of the
    # prefix, or a list that has gained an operation on one side only, put a
    # name in front of a model that answers to no tool.
    script = sync_tool_docs()
    assert script.TOOL_PREFIX == agent._PREFIX
    assert script.OFFERED == agent._OPERATIONS
    assert script.ANSWERED == agent._ANSWERED


def test_the_node_target_spells_a_marker_as_a_model_would_call_it():
    script = sync_tool_docs()
    assert script.node_spelling("search") == "memco_search"
    # Answered by the bound session, never offered, so a prefixed name here
    # would point a model at a tool that does not exist.
    assert script.node_spelling("list_domains") == "list_domains"
    assert script.node_spelling("start_session") == "start_session"
    # Neither offered nor answered. Nothing references it today; the rule is
    # written down so the service starting to is not a failed build.
    assert script.node_spelling("import_memories") == "import_memories"
    with pytest.raises(script.Drift, match="unknown tool"):
        script.node_spelling("teleport")


def test_the_node_target_refuses_copy_a_template_literal_would_eat():
    script = sync_tool_docs()
    assert script.checked("plain text", "where", template=True) == "plain text"
    with pytest.raises(script.Drift, match="backtick"):
        script.checked("a `quoted` example", "where", template=True)
    with pytest.raises(script.Drift, match="trailing whitespace"):
        script.checked("a line \nand another", "where", template=True)
    # A docstring carries both, and the Python targets are not made to refuse
    # copy they could have written.
    assert script.checked("a `quoted` example", "where") == "a `quoted` example"
    assert script.checked("a line \nand another", "where") == "a line \nand another"


def test_the_node_target_camelises_the_keys_it_carries():
    # Alias first, then camelise: op_id is the request field the response calls
    # operation_id, which the Node SDK takes as operationId. The other order
    # would emit opId, which no method takes.
    script = sync_tool_docs()
    assert script.node_parameter("op_id") == "operationId"
    assert script.node_parameter("memory_idx") == "memoryIdx"
    assert script.node_parameter("session_id") == "sessionId"
    assert script.node_parameter("query") == "query"


def test_no_marker_reaches_the_copy_the_node_module_states(manifest):
    # Doubly load-bearing here: the copy lands in a template literal, where a
    # surviving ${ is interpolation rather than text. Measured from the copy
    # rather than over the file, because the header comment quotes the
    # marker syntax to say that it has been resolved — and a comment is inert.
    # TOOL_COPY is where the copy starts; the three consts above it are names.
    module = sync_tool_docs().tool_copy_module(manifest)
    assert "${" not in module[module.index("export const TOOL_COPY") :]


def test_the_node_target_leaves_out_the_parameters_this_sdk_shapes_differently(manifest):
    # Asserted on the key and on the copy itself rather than on a phrase from
    # it: a fragment of the service's prose is something the next export can
    # reword, which would leave this passing while checking nothing.
    script = sync_tool_docs()
    module = script.tool_copy_module(manifest)
    left_out = set()
    for name, tool in manifest.items():
        for key, described in tool["parameters"].items():
            field = key.rsplit(".", 1)[-1]
            if ALIASES.get(field, field) not in SDK_SHAPED:
                continue
            spelled_key = script.key_of(key if "[" in key else script.node_parameter(key))
            assert f"{spelled_key}: `" not in module, key
            assert script.substituted(described, script.node_spelling) not in module, key
            left_out.add(f"{name}.{key}")
    # Pinned, because the skip above is silent.
    assert left_out == {
        "create_memory.source",
        "create_memory.tags",
        "enrich_memory.source",
        "enrich_memory.tags",
        "import_memories.memories[].tags",
        "search.tags",
        "share_feedback.feedback",
    }


def test_the_node_target_carries_the_nested_copy_by_its_manifest_path(manifest):
    script = sync_tool_docs()
    module = script.tool_copy_module(manifest)
    assert "export const NESTED_COPY" in module
    carried = set()
    for path in manifest["import_memories"]["parameters"]:
        if "[" not in path:
            continue
        if path.rsplit(".", 1)[-1] in SDK_SHAPED:
            assert f"'{path}'" not in module, path
            continue
        # At NESTED_COPY's own indentation, so a path landing among a tool's
        # parameters instead — where the schema builder would read it as one —
        # does not satisfy this.
        assert f"\n  '{path}': `" in module, path
        carried.add(path)
    # Pinned, because the skip above is silent.
    assert carried == {
        "memories[].queries",
        "memories[].insights",
        "memories[].insights[].title",
        "memories[].insights[].content",
    }


def test_the_node_target_refuses_a_marker_it_cannot_spell(published):
    script = sync_tool_docs()
    published["search"]["description"] = "Search things. See ${tool:teleport}."
    with pytest.raises(script.Drift, match="unknown tool"):
        script.tool_copy_module(published)


def test_the_node_target_refuses_a_marker_it_cannot_parse(published):
    script = sync_tool_docs()
    published["search"]["description"] = "Search things. See ${tool:Search}."
    with pytest.raises(script.Drift, match="not a tool reference"):
        script.tool_copy_module(published)


def test_the_node_target_refuses_a_field_it_neither_writes_nor_names(published):
    # Every string the manifest publishes is either carried or excluded by
    # name, so a field the next export adds is a decision rather than a
    # default. Without this it would be dropped in silence.
    script = sync_tool_docs()
    published["search"]["outputSchema"] = "something new"
    with pytest.raises(script.Drift, match="neither writes nor names"):
        script.tool_copy_module(published)


def test_the_committed_node_module_is_what_the_generator_writes(manifest):
    # The emitter is pure, so this is both the drift check and the whole of its
    # idempotence: a second run produces the bytes already on disk.
    script = sync_tool_docs()
    assert script.tool_copy_module(manifest) == script.NODE_COPY.read_text(encoding="utf-8")


def test_the_node_target_refuses_a_name_a_key_cannot_spell():
    # Every description goes through `checked`; a key is written straight into
    # source, so a name carrying a quote would close the string it is spelled in.
    script = sync_tool_docs()
    assert script.key_of("get_memory") == "get_memory"
    assert script.key_of("memories[].insights[].title") == "'memories[].insights[].title'"
    with pytest.raises(script.Drift, match="a key cannot spell"):
        script.key_of("o'brien")


def test_the_generator_names_every_top_level_field_the_manifest_publishes():
    # The tool-level accounting cannot see this level, so without this a whole
    # new kind of published copy — a set of prompts, a second tool list — is
    # dropped without a word, while the emitted module states that nothing is.
    # A new key is a decision: carry it, or name it as left behind.
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(document) == sync_tool_docs().DOCUMENT
