"""Reader for the provenance descriptor the export ships inside the package.

The descriptor records which version of the contract the generated client was
built from, so an installed artifact can be traced back to its source without
consulting the repository.

It is parsed by a small strict reader rather than a YAML library: the file is
machine-generated with a fixed shape, and a public SDK should not oblige every
consumer to install PyYAML to read one metadata file. Anything that does not
match the expected shape raises rather than being silently tolerated.
"""

from __future__ import annotations

import functools
from importlib import resources

from .errors import MemcoConfigError
from .types import ProtoRecord, Provenance

RESOURCE = "SDK_PROVENANCE.yaml"
"""Name of the descriptor within the ``memco`` package."""

__all__ = ["RESOURCE", "parse", "provenance"]


def _strip_comment(raw: str) -> str:
    """Remove a trailing YAML comment.

    A ``#`` opens a comment only at the start of a line or after whitespace,
    and never inside quotes. Splitting on every ``#`` would corrupt a value
    such as ``"abc#123"``.

    Args:
        raw: The text following a key.

    Returns:
        The text with any comment removed.
    """
    quote = ""
    for index, char in enumerate(raw):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or raw[index - 1].isspace()):
            return raw[:index]
    return raw


def _value(raw: str, *, where: str) -> str:
    """Read one scalar value.

    Args:
        raw: The text following a key.
        where: What is being read, for the error message.

    Returns:
        The bare value, with any surrounding quotes removed.

    Raises:
        MemcoConfigError: If the value is a block scalar. ``|`` and ``>`` put
            the content on following lines, which this reader does not model,
            and returning the indicator itself would be silently wrong.
    """
    value = _strip_comment(raw).strip()
    if value in ("|", ">") or value[:2] in ("|-", ">-", "|+", ">+"):
        raise MemcoConfigError(f"{RESOURCE} uses an unsupported block scalar for {where}")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":  # noqa: PLR2004
        return value[1:-1]
    return value


def _significant(text: str) -> list[tuple[int, str]]:
    """Split into (indent, content) pairs, dropping blank and comment lines.

    Args:
        text: The descriptor's contents.

    Returns:
        One pair per meaningful line.
    """
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = _strip_comment(raw).rstrip()
        if stripped.strip():
            lines.append((len(stripped) - len(stripped.lstrip()), stripped.strip()))
    return lines


def _entry_fields(body: list[tuple[int, str]]) -> dict[str, str]:
    """Read the direct fields of one list entry.

    Only the entry's own keys count. A nested block — a signature, a
    dependency list — must not override the entry's real values, so anything
    indented deeper than the entry's own fields is skipped.

    Args:
        body: The entry's lines, as (indent, content) pairs.

    Returns:
        The entry's ``path`` and ``sha256``, where present.

    Raises:
        MemcoConfigError: If the entry names either key twice.
    """
    fields: dict[str, str] = {}
    base = body[0][0] if body else 0
    for indent, content in body:
        if indent != base:
            continue
        key, separator, rest = content.partition(":")
        if not separator or key not in ("path", "sha256"):
            continue
        if key in fields:
            raise MemcoConfigError(f"{RESOURCE} has a protos entry naming {key} twice")
        fields[key] = _value(rest, where=key)
    return fields


def parse(text: str) -> Provenance:
    """Parse the descriptor.

    Reads only the fields this SDK exposes — the server commit and the contract
    files with their checksums — and ignores the rest of the document, so the
    export can add sections without breaking older clients.

    Structure is read by indentation rather than by pattern matching, because
    the descriptor nests several languages' sections that reuse the same key
    names. A key only counts where it actually belongs: ``server_commit`` at the
    top level, ``path`` and ``sha256`` as direct fields of a ``protos`` entry.

    Args:
        text: The descriptor's contents.

    Returns:
        The parsed provenance.

    Raises:
        MemcoConfigError: If the server commit is missing, no contract files are
            listed, or an entry lacks or repeats a path or checksum. A malformed
            descriptor means the package was assembled wrongly, so guessing
            would hide a real packaging fault.
    """
    lines = _significant(text)
    commit = ""
    protos: list[ProtoRecord] = []

    for position, (indent, content) in enumerate(lines):
        if indent != 0:
            continue
        key, separator, rest = content.partition(":")
        if not separator:
            continue
        if key == "server_commit":
            commit = _value(rest, where="server_commit")
        elif key == "protos":
            protos = _read_protos(lines, position)

    if not commit:
        raise MemcoConfigError(f"{RESOURCE} is missing server_commit")
    if not protos:
        raise MemcoConfigError(f"{RESOURCE} lists no protos")
    return Provenance(server_commit=commit, protos=tuple(protos))


def _read_protos(lines: list[tuple[int, str]], start: int) -> list[ProtoRecord]:
    """Read the ``protos`` list that begins at the given line.

    A YAML sequence may be indented under its key or written flush with it —
    the latter is what most emitters produce by default — so both are accepted.

    Args:
        lines: Every meaningful line of the document.
        start: Index of the ``protos:`` line itself.

    Returns:
        One record per entry.

    Raises:
        MemcoConfigError: If an entry lacks a path or a checksum.
    """
    base = lines[start][0]
    body: list[tuple[int, str]] = []
    for indent, content in lines[start + 1 :]:
        if indent > base or (indent == base and content.startswith("-")):
            body.append((indent, content))
        else:
            break

    records: list[ProtoRecord] = []
    entry: list[tuple[int, str]] = []
    entry_indent = min((indent for indent, _ in body), default=0)

    def flush() -> None:
        if not entry:
            return
        fields = _entry_fields(entry)
        if not fields.get("path") or not fields.get("sha256"):
            raise MemcoConfigError(
                f"{RESOURCE} has a protos entry missing path or sha256: {entry!r}"
            )
        records.append(ProtoRecord(path=fields["path"], sha256=fields["sha256"]))

    for indent, content in body:
        if indent == entry_indent and content.startswith("-"):
            flush()
            # The dash may be followed by any amount of space; the first field
            # sits wherever that lands, and its siblings align with it.
            rest = content[1:]
            entry = [(indent + 1 + (len(rest) - len(rest.lstrip())), rest.strip())]
        elif entry:
            entry.append((indent, content))
    flush()
    return records


@functools.lru_cache(maxsize=1)
def provenance() -> Provenance:
    """Read the provenance of the installed generated client.

    The result is cached, so repeated calls do not re-read the file.

    Returns:
        The provenance recorded when this package was built.

    Raises:
        MemcoConfigError: If the descriptor is missing from the installed
            package or does not match the expected shape.

    Example:
        >>> provenance().server_commit  # the commit this wheel was built from
        '762721a87ab0...'
    """
    try:
        text = (resources.files("memco") / RESOURCE).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise MemcoConfigError(
            f"{RESOURCE} is missing from the installed memco package; the wheel was built wrongly"
        ) from exc
    return parse(text)
