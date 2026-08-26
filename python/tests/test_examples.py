"""Every example must import.

They were only byte-compiled before, which does not resolve imports — so an
example could name a symbol that no longer exists and stay green. Each one
guards its entry point behind ``__main__``, so importing runs no requests.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

EXAMPLES = sorted((pathlib.Path(__file__).parent.parent / "examples").glob("*.py"))


def test_there_are_examples_to_check():
    assert EXAMPLES, "no examples found; the glob or the directory moved"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_imports(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"example_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_guards_its_entry_point(path: pathlib.Path):
    # Without the guard, importing an example would try to reach the service.
    assert 'if __name__ == "__main__":' in path.read_text()
