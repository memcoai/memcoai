#!/usr/bin/env python3
"""Write the service's agent-facing tool copy into the SDKs.

``python/memco/memory/tools.json`` arrives with every export and carries the same
copy the hosted MCP server publishes, so an agent built on this SDK and one
connected over MCP are told the same things. This script is the whole of that
seam: it is the only thing that reads the manifest, and no SDK opens it at
runtime.

    python3 scripts/sync_tool_docs.py           # write every target
    python3 scripts/sync_tool_docs.py --check   # fail if any has drifted

There are three targets, in two languages, because five of the tables below are
shared and four copies across two runtimes could not be held to agreeing.

``memco.agent`` builds a model's tool descriptions by reading the docstrings in
``memco.operations`` back out with ``inspect.getdoc``, so for the Python targets
the docstrings are where the copy lands and this script is a rewriter: it owns
the leading prose of each operation's docstring and the description of every
parameter the manifest names, and leaves Returns, Raises, Example and any
parameter the manifest does not name exactly as they are.

TypeScript keeps no doc comments at runtime, so the Node SDK cannot read its
copy back out of anything. Its target is a checked-in module,
``nodejs/src/gen/toolCopy.ts``, emitted whole rather than rewritten — which
makes its drift check plain string inequality.

Standard library only, so it runs in CI with nothing installed.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "python" / "memco" / "memory" / "tools.json"
OPERATIONS = ROOT / "python" / "memco" / "operations.py"
TYPES = ROOT / "python" / "memco" / "types.py"
NODE_COPY = ROOT / "nodejs" / "src" / "gen" / "toolCopy.ts"

LINE_LENGTH = 100
"""Matches ruff's line-length, so the result needs no reformatting."""

LINE_WIDTH = 80
"""Matches prettier's printWidth, which is what breaks the emitted arrays.

The copy is exempt: prettier never reflows a template literal, so one manifest
line stays one file line however long it is.
"""

TOOL_PREFIX = "memco_"
"""Prepended to each operation's name, so the tools are recognisable as a set.

The SDK's own copy of this is ``memco.agent._PREFIX``, and
``python/tests/test_tool_copy.py`` holds the two together: a marker spelled with
one prefix and a tool registered under another names nothing.
"""

OFFERED = (
    "search",
    "get_memory",
    "create_memory",
    "enrich_memory",
    "share_feedback",
    "revert_memory",
)
"""The operations offered to a model, in the order a task uses them.

Mirrors ``memco.agent._OPERATIONS``. A marker naming one of these is spelled as
the name a model calls, because that is the only way it can reach the tool.
"""

ANSWERED = ("list_domains", "start_session")
"""Operations the copy names that a bound session has already answered.

Mirrors ``memco.agent._ANSWERED``. The service's copy is written for a caller
who picks a domain and opens a session per call, so it points at both; bound to
a session neither is a tool, and a prefixed name would send a model after one
that does not exist.
"""

BARE = frozenset({*ANSWERED, "import_memories"})
"""Manifest tools a marker spells by their bare name.

The two the session has already answered, and ``import_memories``, which the
manifest neither offers nor answers. Nothing references it today; the rule is
written down so that the service starting to is not a failed build.
"""

OPERATION_CLASSES = {
    "MemoryOperations": "MemoryOperations",
    "AsyncMemoryOperations": "AsyncMemoryOperations",
    "SessionScope": "MemoryOperations",
    "AsyncSessionScope": "AsyncMemoryOperations",
}
"""The classes carrying the operations, and the class each one's out-of-class
references name.

A scope cannot write ``:meth:`list_domains``` bare — it has no such method, and
Sphinx runs nitpicky — so a reference to an operation the class does not carry is
qualified against the namespace that does.
"""

IGNORED = frozenset({"self", "timeout"})
"""Parameters the manifest has no business describing: the receiver, and a
deadline that is this SDK's own idea rather than a request field."""

PARAMETER_ALIASES = {"op_id": "operation_id"}
"""Manifest keys are request-message field names. One is spelled differently
here: ``RevertMemoryRequest.op_id`` is taken as ``operation_id``, which is what
the response calls the same handle."""

SDK_SHAPED = frozenset({"tags", "feedback", "source"})
"""Parameters whose copy stays hand-written, because this SDK's shape is not the
wire's.

The manifest describes what the MCP server accepts: ``tags`` as a list of XML
strings, ``feedback`` as a list of ``<feedback ...>`` tags, ``source`` as the
literal ``'user'`` or ``'agent'``. This SDK takes ``Tag``, ``FeedbackRating`` and
``DataSource``, and ``memco.agent`` builds a model an object schema from those
types. Writing the wire copy onto them would describe an encoding the schema does
not accept — the one case where the service's words are wrong for this surface.
"""

NESTED = {
    "memories[].queries": ("ImportedMemory", "queries"),
    "memories[].insights": ("ImportedMemory", "insights"),
    "memories[].tags": ("ImportedMemory", "tags"),
    "memories[].insights[].title": ("ImportedInsight", "title"),
    "memories[].insights[].content": ("ImportedInsight", "content"),
}
"""Manifest paths for nested request fields, and the dataclass attribute each
documents.

The manifest states nested fields as paths rather than as a flat map, so they
have to be walked and placed rather than looked up. These reach a developer
through the Sphinx reference, not a model: ``import_memories`` is not an offered
tool, so nothing here is built into a tool schema.
"""

CARRIED = frozenset({"name", "description", "parameters"})
"""What the Node module takes out of each of the manifest's tool entries."""

DOCUMENT = frozenset({"version", "service", "instructions", "tools"})
"""The manifest's own top-level keys.

The tool-level accounting below cannot see this level, so without it a whole new
kind of published copy — a set of prompts, a second tool list — would be dropped
without a word. ``instructions`` is the one that is deliberately left where it
is: every SDK's briefing is built from what the service returns per session, not
from a blob fixed at export time.
"""

EXCLUDED = frozenset({"rpc", "title", "annotations"})
"""What it deliberately leaves behind, named so nothing is dropped in silence.

``rpc`` is already checked against the contract by
``scripts/verify_provenance.py``; ``annotations`` are MCP transport hints, which
``memco.agent`` ignores too; ``title`` is a short label with no caller. The
manifest's top-level ``instructions`` is left behind for a reason of its own:
``agent.briefing`` builds from the ``DomainEntry`` and ``Instructions`` the
service returns, not from the manifest.
"""

SECTION = re.compile(r"^(Args|Attributes|Returns|Raises|Example|Examples|Note|Yields):$")
ENTRY = re.compile(r"^ {4}(\w+): (.*)$")
MARKER = re.compile(r"\$\{tool:([a-z_]+)\}")
IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
KEY = re.compile(r"^[A-Za-z0-9_$.\[\]]+$")
"""What a key the Node module is written with may be made of.

Every description goes through :func:`checked`; a key is written straight into
source, so a name carrying a quote, a backslash or a newline would close the
string it is spelled in. The manifest's names and paths are identifiers and
dotted paths, so this refuses rather than escapes — an escape would be a
spelling nobody chose.
"""


class Drift(Exception):
    """Something the manifest names cannot be written where it belongs."""


def substituted(text: str, spell) -> str:
    """Replace every ``${tool:...}`` marker, refusing any that survives.

    The one guard both languages depend on, so it lives in one place. The
    substitution only reaches markers the pattern matches: a malformed one —
    ${tool:Search}, ${tool:}, ${tool:get_memory2} — would otherwise be written
    out as literal text and read by a model. In the Node target it is worse
    still, because there the copy lands in a template literal where ``${`` is
    interpolation rather than text.
    """
    rendered = MARKER.sub(lambda match: spell(match.group(1)), text)
    if "${" in rendered:
        raise Drift(f"copy carries a marker that is not a tool reference: {rendered!r}")
    return rendered


def resolver(tools: set[str], carried: set[str], qualifier: str):
    """Build the renderer that spells this class's ``${tool:...}`` markers.

    A marker becomes a Sphinx cross-reference, which serves both readers: Sphinx
    renders a link for a developer, and ``memco.agent`` reduces the role to the
    tool name for a model.
    """

    def spell(name: str) -> str:
        if name not in tools:
            # Never passed through: a ${tool:...} reaching a model is a
            # description of a tool that does not exist.
            raise Drift(f"unknown tool referenced in the manifest copy: {name}")
        if name in carried:
            return f":meth:`{name}`"
        return f":meth:`~memco.operations.{qualifier}.{name}`"

    def render(text: str) -> str:
        return substituted(text, spell)

    return render


def node_spelling(name: str) -> str:
    """Spell one ``${tool:...}`` marker as the Node SDK's copy names it.

    Args:
        name: The operation the marker names.

    Returns:
        The name a model calls: prefixed for an operation offered as a tool,
        bare for one that is not.

    Raises:
        Drift: If the target is neither offered nor named in :data:`BARE`.
            Which of the two a new operation is is a decision, and guessing
            either way points a model at a tool that does not exist.
    """
    if name in OFFERED:
        return TOOL_PREFIX + name
    if name in BARE:
        return name
    raise Drift(f"unknown tool referenced in the manifest copy: {name}")


def checked(text: str, where: str, *, template: bool = False) -> str:
    """Refuse copy that would not survive being written where it is going.

    Args:
        text: The copy the manifest published.
        where: What is being written, for the message.
        template: Whether the destination is a TypeScript template literal
            rather than a docstring, which it can be eaten by in two more ways.

    Returns:
        The copy, unchanged.

    Raises:
        Drift: If it could not be written verbatim.
    """
    destination = "a template literal" if template else "a docstring"
    if "\\" in text or '"""' in text:
        raise Drift(f"{where}: copy carries a backslash or a triple quote, which {destination} eats")
    if template:
        if "`" in text:
            raise Drift(f"{where}: copy carries a backtick, which ends the template literal")
        if any(line != line.rstrip() for line in text.split("\n")):
            # Inside a template literal that whitespace is copy, and invisible.
            # Anything that strips it — an editor on save, a whitespace hook —
            # silently changes what a model reads, and the two then fight on
            # every run, because this writes it straight back.
            raise Drift(f"{where}: copy carries trailing whitespace a stripping tool would eat")
    return text


def wrapped_prose(description: str, render, width: int) -> list[str]:
    """Lay the manifest's description out as docstring prose.

    Its lines are separated by a single newline; they become paragraphs, so that
    ``agent._reflowed`` keeps them apart instead of running them together.
    """
    lines: list[str] = []
    started = False
    for index, paragraph in enumerate(description.split("\n")):
        if index:
            lines.append("")
        # The very first line shares its row with the opening triple quote, so
        # it is wrapped three columns short and the placeholder taken back off.
        # Keyed on having emitted something, not on `lines`: a leading empty
        # paragraph puts a blank line there and would otherwise spend the room.
        opening = "" if started else " " * 3
        wrapped = textwrap.wrap(
            render(paragraph),
            width=width,
            initial_indent=opening,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if wrapped:
            if opening:
                wrapped[0] = wrapped[0][len(opening) :]
            started = True
        lines.extend(wrapped)
    for line in lines:
        if SECTION.match(line):
            # Prose and sections are read back apart by this same pattern, so a
            # bare section word in the copy is not a fixed point: the next run
            # would re-file everything after it as a section and write it twice.
            # `agent._describe` stops the model-facing summary there too, so the
            # rest of the service's copy would be dropped without a word.
            raise Drift(f"copy contains a line a section header would be read from: {line!r}")
    return lines


def wrapped_entry(name: str, description: str, render, width: int) -> list[str]:
    """Lay one Args or Attributes entry out, continuations indented under it."""
    return textwrap.wrap(
        f"{name}: {render(description)}",
        width=width,
        initial_indent=" " * 4,
        subsequent_indent=" " * 8,
        break_long_words=False,
        break_on_hyphens=False,
    )


def trimmed(lines: list[str]) -> list[str]:
    """Drop the blank lines around a block, which the layout supplies itself."""
    body = list(lines)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return body


def split_sections(body: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split a docstring into its leading prose and its named sections."""
    prose: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in body:
        header = SECTION.match(line)
        if header:
            sections.append((header.group(1), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            prose.append(line)
    return prose, sections


def rewrite_entries(lines: list[str], described: dict[str, str], render, width: int) -> list[str]:
    """Replace the description of each named entry, keeping the rest verbatim."""
    out: list[str] = []
    index = 0
    while index < len(lines):
        match = ENTRY.match(lines[index])
        if match is None:
            out.append(lines[index])
            index += 1
            continue
        end = index + 1
        while end < len(lines) and lines[end].strip() and not ENTRY.match(lines[end]):
            end += 1
        name = match.group(1)
        if name in described:
            out.extend(wrapped_entry(name, described[name], render, width))
        else:
            out.extend(lines[index:end])
        index = end
    return out


def read_docstring(lines: list[str], node: ast.Constant) -> tuple[list[str], int]:
    """Read a docstring's body out of the source, dedented, without its quotes."""
    indent = node.col_offset
    raw = lines[node.lineno - 1 : node.end_lineno]
    opening = raw[0][indent:]
    if not opening.startswith('"""'):
        raise Drift(f"line {node.lineno}: docstring does not open with a plain triple quote")
    body = [opening[3:]]
    body.extend(line[indent:] if not line[:indent].strip() else line.lstrip() for line in raw[1:])
    if not body[-1].rstrip().endswith('"""'):
        raise Drift(f"line {node.end_lineno}: docstring does not close with a plain triple quote")
    body[-1] = body[-1].rstrip()[:-3]
    if not body[-1].strip():
        body.pop()
    return body, indent


def write_docstring(body: list[str], indent: int) -> list[str]:
    """Render a docstring body back as source lines."""
    pad = " " * indent
    out = [f'{pad}"""{body[0]}']
    out.extend(f"{pad}{line}" if line.strip() else "" for line in body[1:])
    out.append(f'{pad}"""')
    return out


def laid_out(prose: list[str], sections: list[tuple[str, list[str]]]) -> list[str]:
    """Reassemble a docstring from its prose and sections."""
    body = trimmed(prose)
    for header, lines in sections:
        body.append("")
        body.append(f"{header}:")
        body.extend(trimmed(lines))
    return body


def docstring_node(node: ast.AST) -> ast.Constant | None:
    """Return the string constant holding a definition's docstring, if it has one."""
    first = next(iter(getattr(node, "body", [])), None)
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.value
    return None


def definitions(tree: ast.Module, classes) -> dict[str, dict[str, ast.AST]]:
    """Index the class and function definitions this script writes into."""
    found: dict[str, dict[str, ast.AST]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in classes:
            found[node.name] = {
                member.name: member
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
            }
    return found


def parameters_of(node: ast.AST) -> set[str]:
    """The argument names a definition takes, however they are passed."""
    args = node.args
    return {arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]}


def sync_operations(source: str, tools: dict[str, dict]) -> str:
    """Write each operation's description and parameter copy into operations.py."""
    lines = sourcelines(source)
    tree = ast.parse(source)
    found = definitions(tree, OPERATION_CLASSES)
    missing = sorted(set(OPERATION_CLASSES) - set(found))
    if missing:
        raise Drift(f"classes absent from {OPERATIONS.name}: {', '.join(missing)}")

    edits: list[tuple[int, int, list[str]]] = []
    written: set[str] = set()
    consumed: set[str] = set()
    for class_name, qualifier in OPERATION_CLASSES.items():
        members = found[class_name]
        render = resolver(set(tools), set(members) & set(tools), qualifier)
        for name, tool in tools.items():
            node = members.get(name)
            if node is None:
                continue
            written.add(name)
            constant = docstring_node(node)
            if constant is None:
                raise Drift(f"{class_name}.{name} has no docstring to write into")

            body, indent = read_docstring(lines, constant)
            prose, sections = split_sections(body)
            width = LINE_LENGTH - indent

            where = f"{class_name}.{name}"
            new_prose = wrapped_prose(
                checked(tool["description"], where), render, width
            )

            takes = parameters_of(node)
            described = {}
            for key, text in tool["parameters"].items():
                parameter = PARAMETER_ALIASES.get(key, key)
                if "[" in key or parameter not in takes:
                    # Bound by the scope, or nested: accounted for below rather
                    # than here, so a key that is neither cannot slip through.
                    continue
                consumed.add(key)
                if parameter not in SDK_SHAPED:
                    described[parameter] = checked(text, f"{where}.{key}")
            undescribed = sorted(takes - IGNORED - SDK_SHAPED - set(described))
            if undescribed:
                raise Drift(f"{where}: the manifest describes no {', '.join(undescribed)}")
            new_sections = [
                (header, rewrite_entries(body_lines, described, render, width))
                if header == "Args"
                else (header, body_lines)
                for header, body_lines in sections
            ]
            documented = {
                match.group(1)
                for header, body_lines in sections
                if header == "Args"
                for match in map(ENTRY.match, body_lines)
                if match
            }
            undocumented = sorted(set(described) - documented)
            if undocumented:
                raise Drift(f"{where}: no Args entry for {', '.join(undocumented)}")

            edits.append(
                (
                    constant.lineno - 1,
                    constant.end_lineno,
                    write_docstring(laid_out(new_prose, new_sections), indent),
                )
            )

    unwritten = sorted(set(tools) - written)
    if unwritten:
        raise Drift(f"the manifest names tools this SDK does not expose: {', '.join(unwritten)}")

    # Anything the manifest describes that reached no docstring. Without this a
    # renamed or added field is dropped in silence, and the docstring keeps copy
    # the service has retired — the one failure this script exists to prevent.
    published = {key for tool in tools.values() for key in tool["parameters"]}
    accounted = consumed | set(NESTED)
    accounted |= {key for key in published if PARAMETER_ALIASES.get(key, key) in SDK_SHAPED}
    unaccounted = sorted(published - accounted)
    if unaccounted:
        raise Drift(f"the manifest describes fields nothing carries: {', '.join(unaccounted)}")
    return apply(lines, edits)


def sync_types(source: str, tools: dict[str, dict]) -> str:
    """Write the nested request-field copy onto the dataclasses that carry it."""
    lines = sourcelines(source)
    tree = ast.parse(source)
    wanted = {target for target, _ in NESTED.values()}
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in wanted
    }
    missing = sorted(wanted - set(classes))
    if missing:
        raise Drift(f"classes absent from {TYPES.name}: {', '.join(missing)}")

    described: dict[str, dict[str, str]] = {name: {} for name in wanted}
    for tool in tools.values():
        for path, text in tool["parameters"].items():
            target = NESTED.get(path)
            if target is None:
                continue
            class_name, attribute = target
            if attribute in SDK_SHAPED:
                continue
            described[class_name][attribute] = checked(text, path)

    unresolved = sorted(set(NESTED) - {p for t in tools.values() for p in t["parameters"]})
    if unresolved:
        raise Drift(f"nested paths absent from the manifest: {', '.join(unresolved)}")

    # A dataclass is not a tool, so nothing in its copy references one; the
    # renderer is still supplied so a marker appearing later is caught, not shipped.
    render = resolver(set(tools), set(), "MemoryOperations")
    edits: list[tuple[int, int, list[str]]] = []
    for class_name, node in classes.items():
        constant = docstring_node(node)
        if constant is None:
            raise Drift(f"{class_name} has no docstring to write into")
        body, indent = read_docstring(lines, constant)
        prose, sections = split_sections(body)
        width = LINE_LENGTH - indent
        new_sections = [
            (header, rewrite_entries(body_lines, described[class_name], render, width))
            if header == "Attributes"
            else (header, body_lines)
            for header, body_lines in sections
        ]
        edits.append(
            (
                constant.lineno - 1,
                constant.end_lineno,
                write_docstring(laid_out(prose, new_sections), indent),
            )
        )
    return apply(lines, edits)


# -- the Node target ------------------------------------------------------


NODE_HEADER = """/**
 * The service's agent-facing tool copy, as the Node SDK hands it to a model.
 *
 * Generated by `scripts/sync_tool_docs.py` from the tool manifest, which
 * arrives with every export carrying the copy the hosted MCP server publishes.
 * The generator reads one checked-in copy of it; `client/src/gen/tools.json`
 * is the same bytes, which `scripts/verify_provenance.py` checks. Do not edit:
 * `make tool-docs-check` and a pre-commit hook fail on any difference, and the
 * next run puts it back.
 *
 * TypeScript keeps no doc comments at runtime, so the copy cannot be read back
 * out of the source that documents it: it has to be a module. Compiled in at
 * build time it is reachable from the ESM build, the CommonJS build and any
 * bundler alike, with no filesystem read and no JSON import assertion — and,
 * being checked in rather than read off the manifest on startup, the
 * description a model is handed stays reviewable in a diff.
 *
 * The manifest's `${tool:...}` markers are already resolved. An offered
 * operation is spelled as a model calls it, `{prefix}search`; one a bound
 * session has already answered — `list_domains`, `start_session` — keeps its
 * bare name, because prefixing it would name a tool that does not exist.
 * Nothing below carries a marker.
 *
 * What the manifest publishes and this module leaves behind, named so that
 * nothing is dropped in silence:
 *
 * - `rpc`, the method each tool maps to. `scripts/verify_provenance.py` already
 *   checks it against the contract.
 * - `annotations`, MCP transport hints, which the agent layer never reads.
 * - `title`, a short label with no caller here.
 * - the manifest's top-level `instructions`, because a briefing is built from
 *   the `DomainEntry` and `Instructions` the service returns, not from this.
 * - the parameters in `SDK_SHAPED`, for the reason given there.
 */

/** One operation's copy: what it is for, and what each parameter means. */
export interface ToolCopy {
  /** What the operation is for, as a model should read it. */
  readonly description: string
  /**
   * One description per parameter the manifest names, keyed as this SDK spells
   * it: aliased, then camelised, so `op_id` is `operationId`.
   *
   * Every parameter the manifest names is here, including the ones a bound
   * session supplies itself. The generator does not know which those are; the
   * schema builder takes the subset its own method accepts.
   */
  readonly parameters: { readonly [name: string]: string }
}

/**
 * Prepended to each operation's name, so the tools are recognisable as a set.
 *
 * The keys of `TOOL_COPY` are the manifest's own names, so the name a model
 * calls is composed rather than stored and a change here is one constant.
 */
export const TOOL_PREFIX = '{prefix}'
"""
"""Everything above the generated tables. ``{prefix}`` is the one hole in it."""

NODE_OFFERED = """/**
 * The operations offered to a model, in the order a task uses them.
 *
 * Written out rather than read off the manifest. Everything a tool exposes
 * crosses into an untrusted model's reach, so an operation is offered because
 * someone decided it should be.
 */"""
"""The doc comment above ``OFFERED``."""

NODE_ANSWERED = """/**
 * Operations the copy names that a bound session has already answered.
 *
 * The service's copy is written for a caller who picks a domain and opens a
 * session per call, so it points at both — and it is right to, on the surface
 * it was written for. Neither is a tool here, which is why their markers are
 * spelled bare above and why a briefing has to say so.
 */"""
"""The doc comment above ``ANSWERED``."""

NODE_SHAPED = """/**
 * The parameters this module leaves out, because this SDK's shape is not the
 * wire's.
 *
 * The manifest describes what the MCP server accepts: tags and feedback as
 * lists of XML elements, and a source as one of two bare literals. This SDK
 * takes its own types and builds a model an object schema from them, so the
 * wire copy would describe an encoding that schema does not accept — the one
 * case where the service's words are wrong for this surface. Their
 * descriptions are hand-written where the schema is built.
 */"""
"""The doc comment above ``SDK_SHAPED``."""

NODE_TOOLS = """/**
 * Every tool the manifest publishes, by the manifest's own name for it.
 *
 * `as const satisfies Record<string, ToolCopy>` on purpose: the literal keys
 * survive, so a mistyped name is a compile error at the call site rather than
 * an undefined description handed to a model.
 */"""
"""The doc comment above ``TOOL_COPY``."""

NODE_NESTED = """/**
 * The copy for the nested request fields of `import_memories`, by the
 * manifest's own path for each.
 *
 * Nothing here reaches a model — `import_memories` is not an offered tool. It
 * is carried so that every string the manifest publishes is either written
 * above or excluded by name, which is what makes "nothing was dropped"
 * something a reader can check rather than take on trust.
 */"""
"""The doc comment above ``NESTED_COPY``."""


def camelised(name: str) -> str:
    """Spell a snake_case manifest key the way the Node SDK spells a field."""
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


def node_parameter(key: str) -> str:
    """Spell one flat manifest parameter key as the Node SDK's schema does.

    Aliased first, then camelised: ``op_id`` is the request field the response
    calls ``operation_id``, which this SDK takes as ``operationId``. The other
    order would emit ``opId``, which no method takes.
    """
    return camelised(PARAMETER_ALIASES.get(key, key))


def sdk_shaped(key: str) -> bool:
    """Whether a manifest key names one of the parameters this SDK reshapes.

    A nested path is judged by its last segment, which is the field it
    describes: ``memories[].tags`` is the same ``tags`` as the flat one.
    """
    field = key.rsplit(".", 1)[-1]
    return PARAMETER_ALIASES.get(field, field) in SDK_SHAPED


def key_of(name: str) -> str:
    """Spell an object key, quoting only the ones JavaScript cannot take bare.

    Raises:
        Drift: If the name carries anything a quoted key could not hold.
    """
    if not KEY.match(name):
        raise Drift(f"the manifest names something a key cannot spell: {name!r}")
    return name if IDENTIFIER.match(name) else f"'{name}'"


def member(key: str, text: str, indent: str) -> list[str]:
    """Lay one ``key: `copy``` member out, its continuations at column 0.

    A template literal keeps every byte between its backticks, so one manifest
    line becomes one file line and a wording change reads as one line of diff.
    Indenting the continuations would indent the string.
    """
    lines = f"`{text}`".split("\n")
    lines[0] = f"{indent}{key}: {lines[0]}"
    return lines


def joined(members: list[list[str]]) -> list[str]:
    """Lay object members out one after another, comma-separated.

    Prettier runs with ``trailingComma: none``, so the last member carries no
    comma and every other one carries it on its closing line.
    """
    lines: list[str] = []
    for index, block in enumerate(members):
        last = index == len(members) - 1
        lines.extend(block if last else [*block[:-1], block[-1] + ","])
    return lines


def table(name: str, members: list[list[str]], typed: str) -> list[str]:
    """Render one of the module's object tables, collapsed when it holds nothing.

    Prettier prints an object with no members as ``{}``, so a brace on a line of
    its own around nothing would be reformatted and the emitter would have no
    fixed point. Reachable: every nested path belongs to one tool, which the
    manifest is free to reshape.
    """
    if not members:
        return [f"export const {name} = {{}} as const satisfies {typed}"]
    return [
        f"export const {name} = {{",
        *joined(members),
        f"}} as const satisfies {typed}",
    ]


def array(name: str, values: list[str] | tuple[str, ...]) -> list[str]:
    """Render a readonly tuple, broken the way prettier breaks one at 80 columns."""
    items = [f"'{value}'" for value in values]
    one = f"export const {name} = [{', '.join(items)}] as const"
    if len(one) <= LINE_WIDTH:
        return [one]
    return [
        f"export const {name} = [",
        *[f"  {item}," for item in items[:-1]],
        f"  {items[-1]}",
        "] as const",
    ]


def tool_entry(name: str, tool: dict) -> tuple[list[str], list[list[str]]]:
    """Render one tool's entry, and the nested-path members it carries.

    The two go to different tables — a nested path is a request field of
    ``import_memories``, not a parameter a schema is built from — so they are
    handed back apart rather than mixed and sorted out afterwards.
    """
    unnamed = sorted(set(tool) - CARRIED - EXCLUDED)
    if unnamed:
        raise Drift(
            f"{name}: the manifest carries fields this module neither writes "
            f"nor names: {', '.join(unnamed)}"
        )

    described: list[list[str]] = []
    nested: list[list[str]] = []
    for key, text in tool["parameters"].items():
        if sdk_shaped(key):
            continue
        where = f"{name}.{key}"
        body = checked(substituted(text, node_spelling), where, template=True)
        if "[" in key:
            nested.append(member(key_of(key), body, "  "))
        else:
            described.append(member(key_of(node_parameter(key)), body, "      "))

    summary = checked(substituted(tool["description"], node_spelling), name, template=True)
    parameters = (
        ["    parameters: {", *joined(described), "    }"] if described else ["    parameters: {}"]
    )
    entry = [
        f"  {key_of(name)}: {{",
        *joined([member("description", summary, "    "), parameters]),
        "  }",
    ]
    return entry, nested


def tool_copy_module(tools: dict[str, dict]) -> str:
    """Render the Node SDK's checked-in copy module, whole.

    Args:
        tools: The manifest's tool definitions, by name.

    Returns:
        The contents of ``nodejs/src/gen/toolCopy.ts``.

    Raises:
        Drift: If any of the copy cannot be written verbatim, or the manifest
            carries a field this module neither writes nor names.
    """
    entries: list[list[str]] = []
    nested: list[list[str]] = []
    for name, tool in tools.items():
        entry, paths = tool_entry(name, tool)
        entries.append(entry)
        nested.extend(paths)
    return "\n".join(
        [
            NODE_HEADER.replace("{prefix}", TOOL_PREFIX),
            NODE_OFFERED,
            *array("OFFERED", OFFERED),
            "",
            NODE_ANSWERED,
            *array("ANSWERED", ANSWERED),
            "",
            NODE_SHAPED,
            *array("SDK_SHAPED", sorted(SDK_SHAPED)),
            "",
            NODE_TOOLS,
            *table("TOOL_COPY", entries, "Record<string, ToolCopy>"),
            "",
            NODE_NESTED,
            *table("NESTED_COPY", nested, "Record<string, string>"),
            "",
        ]
    )


def sourcelines(source: str) -> list[str]:
    """Split a module the way ``ast`` counts its lines.

    Not ``str.splitlines``: that also breaks on U+000B, U+000C, U+001C-U+001F,
    U+0085, U+2028 and U+2029, none of which Python's tokenizer treats as a line
    break. One of them inside a docstring would shift every line number after it,
    so a well-formed docstring is spliced at the wrong offset — and rejoining
    would replace the character with a newline, silently.
    """
    if "\r" in source:
        raise Drift("source has carriage returns; this writer emits LF, as the repository does")
    return source.split("\n")


def apply(lines: list[str], edits: list[tuple[int, int, list[str]]]) -> str:
    """Splice the rewritten docstrings in, last first so the spans stay valid."""
    out = list(lines)
    # Sorted on the span alone: the replacement is a list, and comparing two of
    # those to break a tie would be an accident rather than an ordering.
    for start, end, replacement in sorted(edits, key=lambda edit: edit[:2], reverse=True):
        out[start:end] = replacement
    return "\n".join(out)


def main() -> int:
    """Rewrite the docstrings, or report that they have drifted."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero instead of rewriting",
    )
    arguments = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    unnamed = sorted(set(manifest) - DOCUMENT)
    if unnamed:
        print(
            f"{MANIFEST.relative_to(ROOT)}: the manifest carries top-level fields nothing "
            f"names: {', '.join(unnamed)}",
            file=sys.stderr,
        )
        return 2
    tools = {tool["name"]: tool for tool in manifest["tools"]}

    # Every target is rendered before any is written: a failure on the second
    # would otherwise leave the first rewritten, which is a half-applied run.
    targets = (
        (OPERATIONS, sync_operations),
        (TYPES, sync_types),
        (NODE_COPY, lambda _current, published: tool_copy_module(published)),
    )
    rendered: list[tuple[Path, str, str]] = []
    for path, sync in targets:
        # The Node module is generated in full, so a deleted one is written
        # again rather than being a crash. The two Python files are hand-written
        # apart from their docstrings and are never absent.
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        try:
            rendered.append((path, current, sync(current, tools)))
        except Drift as drift:
            print(f"{path.relative_to(ROOT)}: {drift}", file=sys.stderr)
            return 2

    drifted = False
    for path, current, updated in rendered:
        if updated == current:
            continue
        relative = path.relative_to(ROOT)
        if arguments.check:
            drifted = True
            sys.stdout.writelines(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
        else:
            path.write_text(updated, encoding="utf-8")
            print(f"updated {relative}")

    if drifted:
        print(
            "\nThe tool copy has changed. Run scripts/sync_tool_docs.py and commit the result.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
