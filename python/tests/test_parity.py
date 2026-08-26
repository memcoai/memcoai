"""The sync and async surfaces must stay interchangeable.

Nothing forces them to agree, and they are written out separately so each reads
naturally. These tests are what stops them drifting.
"""

from __future__ import annotations

import inspect
from collections.abc import Set as AbstractSet

import memco as package
from memco import AsyncMemco, Memco, errors, operations, types
from memco.operations import AsyncMemoryOperations, MemoryOperations

# Namespaces the client exposes, paired sync-to-async. A second service added
# here is automatically held to the same parity rules.
NAMESPACES = [(MemoryOperations, AsyncMemoryOperations)]

CLIENT_SKIP = {"connect"}  # async-only: the sync client verifies in its constructor


def public_methods(cls: type, skip: AbstractSet[str] = frozenset()) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls, callable)
        if not name.startswith("_") and name not in skip
    }


def assert_signatures_match(sync: type, asynchronous: type, names: set[str]) -> None:
    for name in sorted(names):
        left = inspect.signature(getattr(sync, name))
        right = inspect.signature(getattr(asynchronous, name))
        assert list(left.parameters) == list(right.parameters), name
        for parameter in left.parameters:
            assert left.parameters[parameter].kind == right.parameters[parameter].kind, (
                f"{name}.{parameter} kind"
            )
            assert left.parameters[parameter].default == right.parameters[parameter].default, (
                f"{name}.{parameter} default"
            )


def test_both_clients_expose_the_same_lifecycle():
    assert public_methods(Memco, CLIENT_SKIP) == public_methods(AsyncMemco, CLIENT_SKIP)


def test_client_lifecycle_signatures_match():
    assert_signatures_match(Memco, AsyncMemco, public_methods(Memco, CLIENT_SKIP))


def test_constructors_match():
    assert list(inspect.signature(Memco.__init__).parameters) == list(
        inspect.signature(AsyncMemco.__init__).parameters
    )


def test_every_namespace_is_exposed_on_both_clients():
    # Constructing against a dead port does no I/O once the health probe is
    # off, so this checks the wiring without needing a server.
    sync = Memco(token="t", host="localhost:1", tls=False, check_health=False)
    asynchronous = AsyncMemco(token="t", host="localhost:1", tls=False, check_health=False)
    try:
        for sync_ns, async_ns in NAMESPACES:
            attribute = sync_ns.__name__.replace("Operations", "").lower()
            assert isinstance(getattr(sync, attribute), sync_ns)
            assert isinstance(getattr(asynchronous, attribute), async_ns)
    finally:
        sync.close()


def test_namespaces_expose_the_same_operations():
    for sync_ns, async_ns in NAMESPACES:
        assert public_methods(sync_ns) == public_methods(async_ns)


def test_namespace_signatures_match():
    for sync_ns, async_ns in NAMESPACES:
        assert_signatures_match(sync_ns, async_ns, public_methods(sync_ns))


def test_every_operation_is_documented_with_an_example():
    # The docstrings are the source for generated docs, so an operation without
    # a worked example is a gap in the published documentation.
    for sync_ns, async_ns in NAMESPACES:
        for cls in (sync_ns, async_ns):
            for name in sorted(public_methods(cls)):
                doc = inspect.getdoc(getattr(cls, name)) or ""
                assert "Args:" in doc, f"{cls.__name__}.{name} lacks Args"
                assert "Example:" in doc, f"{cls.__name__}.{name} lacks an Example"


def test_no_public_module_documents_a_flat_call():
    # The operations moved onto client.memory. Any docstring anywhere in the
    # public surface that still shows client.<operation>( is a copy-pasteable
    # AttributeError, and every one of these modules is published by Sphinx.
    names = public_methods(MemoryOperations)
    for module in (package, errors, types, operations):
        source = inspect.getsource(module)
        for number, line in enumerate(source.splitlines(), 1):
            for operation in names:
                flat = f"client.{operation}("
                assert flat not in line, (
                    f"{module.__name__}:{number} documents a flat call: {line.strip()}"
                )


def test_examples_use_the_namespaced_call_form():
    # The operations moved onto client.memory; their examples were written when
    # they hung off the client directly. A copy-pasted flat call is an
    # AttributeError, so the examples must follow the code.
    for sync_ns, async_ns in NAMESPACES:
        attribute = sync_ns.__name__.replace("Operations", "").replace("Async", "").lower()
        for cls in (sync_ns, async_ns):
            for name in sorted(public_methods(cls)):
                doc = inspect.getdoc(getattr(cls, name)) or ""
                example = doc[doc.index("Example:") :] if "Example:" in doc else ""
                for line in example.splitlines():
                    if "client." in line:
                        assert f"client.{attribute}." in line, (
                            f"{cls.__name__}.{name} example uses a flat call: {line.strip()}"
                        )


def test_revert_takes_the_name_the_write_result_carries():
    # WriteResult.operation_id feeds straight into revert_memory, so the
    # argument must be callable by that name.
    for sync_ns, async_ns in NAMESPACES:
        for cls in (sync_ns, async_ns):
            assert "operation_id" in inspect.signature(cls.revert_memory).parameters
