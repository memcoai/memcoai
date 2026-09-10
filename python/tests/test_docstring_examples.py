"""An ``await`` in a docstring example must name something awaitable.

The examples are what the reference publishes, and the published reference is
what an agent building against this SDK reads — nothing executes them, so a
wrong one is wrong in the documentation and nowhere else. The asyncio surface
is where that bites: the two clients mirror each other method for method, so an
example copied from the async side to the sync side, or written from memory,
awaits whatever the sibling example awaited.

Not every awaitable in an example is ours -- the agent tools are shown beside
``await client.messages.create(...)`` -- so only receivers this SDK owns are
resolved, and anything else is left alone.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re

import pytest

from memcoai import AsyncMemco
from memcoai.agent import AsyncToolset
from memcoai.operations import AsyncMemoryOperations, AsyncSession
from memcoai.types import AsyncMemory

PACKAGE = pathlib.Path(__file__).parent.parent / "memcoai"

# The generated client is exported from the server repository and rewritten on
# every export, so its docstrings are not this repository's to hold to anything.
GENERATED = "memory"

# The name an example binds a receiver to, and the async class it stands for.
# A receiver missing from here belongs to somebody else's SDK and is skipped.
RECEIVERS = {
    "client": AsyncMemco,
    "client.memory": AsyncMemoryOperations,
    "session": AsyncSession,
    "toolset": AsyncToolset,
    "memory": AsyncMemory,
}

AWAITED = re.compile(r"await\s+([\w.]+)\.(\w+)\(")


def docstrings() -> list[tuple[str, str]]:
    """Every docstring the reference renders, paired with where it came from."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if GENERATED in path.relative_to(PACKAGE).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(
                node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
            ):
                continue
            text = ast.get_docstring(node)
            if text:
                where = f"{path.name}:{getattr(node, 'lineno', 1)}"
                found.append((where, text))
    return found


def awaited_calls() -> list[tuple[str, str, str]]:
    """Each ``await receiver.method(`` an example makes on a receiver we own."""
    return [
        (where, receiver, method)
        for where, text in docstrings()
        for receiver, method in AWAITED.findall(text)
        if receiver in RECEIVERS
    ]


def test_there_are_awaited_examples_to_check():
    # The regex and the receiver table are both easy to break silently; an
    # empty sweep would pass every assertion below without reading anything.
    assert awaited_calls(), "no awaited examples found; the regex or the receivers moved"


@pytest.mark.parametrize(
    ("where", "receiver", "method"),
    awaited_calls(),
    ids=lambda value: str(value).replace(".", "_"),
)
def test_an_awaited_example_names_a_coroutine(where: str, receiver: str, method: str):
    cls = RECEIVERS[receiver]
    member = getattr(cls, method, None)
    assert member is not None, f"{where}: {cls.__name__} has no {method!r} to await"
    assert inspect.iscoroutinefunction(member), (
        f"{where}: `await {receiver}.{method}()` is wrong -- "
        f"{cls.__name__}.{method} is not a coroutine function"
    )
