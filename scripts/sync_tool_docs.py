#!/usr/bin/env python3
"""Write the service's agent-facing tool copy into the SDK's docstrings.

``python/memco/memory/tools.json`` arrives with every export and carries the same
copy the hosted MCP server publishes, so an agent built on this SDK and one
connected over MCP are told the same things. ``memco.agent`` builds a model's
tool descriptions by reading the docstrings in ``memco.operations``, so those
docstrings are where the copy has to land. This script is the whole of that
seam: it is the only thing that reads the manifest, and the SDK itself never
opens it.

    python3 scripts/sync_tool_docs.py           # rewrite the docstrings
    python3 scripts/sync_tool_docs.py --check   # fail if they have drifted

It owns the leading prose of each operation's docstring and the description of
every parameter the manifest names. Returns, Raises, Example and any parameter
the manifest does not name are left exactly as they are.

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

LINE_LENGTH = 100
"""Matches ruff's line-length, so the result needs no reformatting."""

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

SECTION = re.compile(r"^(Args|Attributes|Returns|Raises|Example|Examples|Note|Yields):$")
ENTRY = re.compile(r"^ {4}(\w+): (.*)$")
MARKER = re.compile(r"\$\{tool:([a-z_]+)\}")


class Drift(Exception):
    """Something the manifest names cannot be written where it belongs."""


def resolver(tools: set[str], carried: set[str], qualifier: str):
    """Build the renderer that spells this class's ``${tool:...}`` markers.

    A marker becomes a Sphinx cross-reference, which serves both readers: Sphinx
    renders a link for a developer, and ``memco.agent`` reduces the role to the
    tool name for a model.
    """

    def render(text: str) -> str:
        def one(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in tools:
                # Never passed through: a ${tool:...} reaching a model is a
                # description of a tool that does not exist.
                raise Drift(f"unknown tool referenced in the manifest copy: {name}")
            if name in carried:
                return f":meth:`{name}`"
            return f":meth:`~memco.operations.{qualifier}.{name}`"

        rendered = MARKER.sub(one, text)
        if "${" in rendered:
            # The substitution only reaches markers the pattern matches. A
            # malformed one — ${tool:Search}, ${tool:}, ${tool:get_memory2} —
            # would otherwise be written out as literal text and read by a model.
            raise Drift(f"copy carries a marker that is not a tool reference: {rendered!r}")
        return rendered

    return render


def checked(text: str, where: str) -> str:
    """Refuse copy that would not survive being written into a docstring."""
    if "\\" in text or '"""' in text:
        raise Drift(f"{where}: copy carries a backslash or a triple quote, which a docstring eats")
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
    tools = {tool["name"]: tool for tool in manifest["tools"]}

    # Both files are rendered before either is written: a failure on the second
    # would otherwise leave the first rewritten, which is a half-applied run.
    rendered: list[tuple[Path, str, str]] = []
    for path, sync in ((OPERATIONS, sync_operations), (TYPES, sync_types)):
        current = path.read_text(encoding="utf-8")
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
