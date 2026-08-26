#!/usr/bin/env python3
"""Verify that the checked-in generated clients match the contract they claim.

Asserts three-way agreement, so a stale or hand-edited export cannot pass
review unnoticed:

1. The contract's SHA-256 matches the checksum every ``SDK_PROVENANCE.yaml``
   records, and all of them name the same server commit.
2. ``python/client/requirements.txt`` matches the ``requires.python`` block in
   the descriptor.
3. Both match the floors the generated modules assert at import time.

Standard library only, so it runs anywhere without installing anything.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO = ROOT / "proto" / "memco" / "memory" / "v1" / "memory.proto"
# Python's generated client lives inside the package it ships in; Go and Node
# keep theirs under <lang>/client/.
PYTHON_ROOT = ROOT / "python"
PYTHON_GENERATED = PYTHON_ROOT / "memco" / "memory"
CONTRACT_PATH = "memco/memory/v1/memory.proto"

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    """Record a failure unless the condition holds.

    Args:
        condition: The assertion being made.
        message: What to report when it does not hold.
    """
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FAIL {message}")
        failures.append(message)


def scalar(text: str, key: str) -> str | None:
    """Read a scalar value from a descriptor, at any indent.

    Args:
        text: The descriptor's contents.
        key: The key to read.

    Returns:
        The value with any surrounding quotes stripped, or ``None`` if absent.
    """
    found = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
    return found.group(1).strip("\"'") if found else None


def block(text: str, *path: str) -> str:
    """Extract one nested block from the descriptor by its key path.

    Needed because several languages declare the same dependency name: a
    document-wide search for "protobuf" finds Go's entry, not Python's.

    Args:
        text: The descriptor's contents.
        *path: Successive keys to descend, outermost first.

    Returns:
        The lines beneath the final key, or an empty string if not found.
    """
    lines = text.splitlines()
    start, indent, stop = 0, -1, len(lines)
    for key in path:
        for index in range(start, stop):
            # Leaving the parent block ends the search: a later key of the same
            # name under a different parent must not be picked up.
            if indent >= 0 and lines[index].strip():
                if len(lines[index]) - len(lines[index].lstrip()) <= indent:
                    return ""
            match = re.match(rf"^(\s*){re.escape(key)}:\s*$", lines[index])
            if match and len(match.group(1)) > indent:
                indent = len(match.group(1))
                start = index + 1
                break
        else:
            return ""
    end = start
    while end < len(lines) and (
        not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent
    ):
        end += 1
    return "\n".join(lines[start:end])


def main() -> int:
    """Run every check and report the outcome.

    Returns:
        ``0`` if all checks passed, ``1`` otherwise.
    """
    if not PROTO.is_file():
        print(f"contract missing: {PROTO}")
        return 1

    digest = hashlib.sha256(PROTO.read_bytes()).hexdigest()
    descriptors = sorted(
        [*ROOT.glob("*/client/SDK_PROVENANCE.yaml"), *ROOT.glob("python/memco/SDK_PROVENANCE.yaml")]
    )
    print(f"contract {PROTO.relative_to(ROOT)} sha256={digest[:16]}...")

    check(bool(descriptors), "at least one SDK_PROVENANCE.yaml is present")
    if not descriptors:
        print(f"\n{len(failures)} check(s) failed")
        return 1

    print("\n1. contract checksum and server commit")
    commits = set()
    for descriptor in descriptors:
        text = descriptor.read_text(encoding="utf-8")
        where = descriptor.relative_to(ROOT)
        recorded = dict(re.findall(r"-\s*path:\s*(\S+)\s*\n\s*sha256:\s*(\S+)", text))
        check(
            recorded.get(CONTRACT_PATH) == digest,
            f"{where} binds {CONTRACT_PATH} to the contract's checksum",
        )
        # Anchored: a nested server_commit must not be mistaken for the real one.
        top_level = re.search(r"^server_commit:\s*(\S+)", text, re.MULTILINE)
        commit = top_level.group(1).strip("\"'") if top_level else None
        check(bool(commit), f"{where} records a server_commit")
        if commit:
            commits.add(commit)
    check(len(commits) <= 1, f"all descriptors name one server_commit (found {len(commits)})")

    print("\n2. python requirements match the descriptor")
    # Guarded like the contract above: a missing portion should be reported as
    # a failed check, not an unhandled traceback that hides section 1's results.
    required = [
        PYTHON_ROOT / "requirements.txt",
        PYTHON_ROOT / "memco" / "SDK_PROVENANCE.yaml",
        PYTHON_GENERATED / "v1" / "memory_pb2_grpc.py",
        PYTHON_GENERATED / "v1" / "memory_pb2.py",
    ]
    missing = [path for path in required if not path.is_file()]
    for path in missing:
        check(False, f"{path.relative_to(ROOT)} is present")
    if missing:
        print(f"\n{len(failures)} check(s) failed")
        return 1

    requirements = (PYTHON_ROOT / "requirements.txt").read_text(encoding="utf-8")
    descriptor_text = block(
        (PYTHON_ROOT / "memco" / "SDK_PROVENANCE.yaml").read_text(encoding="utf-8"), "requires", "python"
    )
    pins: dict[str, str] = {}
    for line in requirements.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!;\s]", line, maxsplit=1)[0]
        pins[name] = line[len(name) :].strip()
    for name, specifier in pins.items():
        declared = scalar(descriptor_text, name)
        check(
            declared == specifier,
            f"{name}: requirements.txt {specifier!r} == descriptor {declared!r}",
        )

    print("\n3. requirements match what the generated modules assert")
    grpc_module = (PYTHON_GENERATED / "v1" / "memory_pb2_grpc.py").read_text("utf-8")
    stamped = re.search(r"GRPC_GENERATED_VERSION = '([^']+)'", grpc_module)
    check(stamped is not None, "memory_pb2_grpc.py stamps a grpcio version")
    if stamped:
        check(
            pins.get("grpcio", "").startswith(f">={stamped.group(1)}"),
            f"grpcio floor {pins.get('grpcio')!r} matches stamp >={stamped.group(1)}",
        )

    pb_module = (PYTHON_GENERATED / "v1" / "memory_pb2.py").read_text("utf-8")
    version = re.search(
        r"ValidateProtobufRuntimeVersion\(\s*[\w.]+,\s*(\d+),\s*(\d+),\s*(\d+)", pb_module
    )
    check(version is not None, "memory_pb2.py asserts a protobuf gencode version")
    if version:
        gencode = ".".join(version.groups())
        check(
            pins.get("protobuf", "").startswith(f">={gencode}"),
            f"protobuf floor {pins.get('protobuf')!r} matches gencode >={gencode}",
        )

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all provenance checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
