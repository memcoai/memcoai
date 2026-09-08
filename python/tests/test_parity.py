"""The sync and async surfaces must stay interchangeable.

Nothing forces them to agree, and they are written out separately so each reads
naturally. These tests are what stops them drifting.
"""

from __future__ import annotations

import inspect
from collections.abc import Set as AbstractSet

import memco as package
from memco import AsyncMemco, Memco, errors, operations, types
from memco.operations import (
    AsyncMemoryOperations,
    AsyncSession,
    MemoryOperations,
    Session,
)
from memco.types import AsyncMemory, Memory

from .fake_server import Harness

# Namespaces the client exposes, paired sync-to-async. A second service added
# here is automatically held to the same parity rules.
NAMESPACES = [(MemoryOperations, AsyncMemoryOperations)]

# Sessions are not reached as a client attribute, so the tests that derive one
# from the class name run over NAMESPACES alone. Every other parity rule
# applies to both.
SESSIONS = [(Session, AsyncSession)]

# Neither a client attribute nor named after a namespace, for the same reason
# sessions are held apart -- but feedback() still has to stay in step.
MEMORIES = [(Memory, AsyncMemory)]

PAIRS = NAMESPACES + SESSIONS + MEMORIES

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


def test_every_namespace_is_exposed_on_both_clients(harness: Harness):
    # The sync client connects in its constructor, so it needs the server. The
    # async one does no I/O until connect(), which this never calls.
    sync = Memco(token="t", host=harness.address, tls=False)
    asynchronous = AsyncMemco(token="t", host="localhost:1", tls=False)
    try:
        for sync_ns, async_ns in NAMESPACES:
            attribute = sync_ns.__name__.replace("Operations", "").lower()
            assert isinstance(getattr(sync, attribute), sync_ns)
            assert isinstance(getattr(asynchronous, attribute), async_ns)
    finally:
        sync.close()


def test_namespaces_expose_the_same_operations():
    for sync_ns, async_ns in PAIRS:
        assert public_methods(sync_ns) == public_methods(async_ns)


def test_namespace_signatures_match():
    for sync_ns, async_ns in PAIRS:
        assert_signatures_match(sync_ns, async_ns, public_methods(sync_ns))


def test_every_operation_is_documented_with_an_example():
    # The docstrings are the source for generated docs, so an operation without
    # a worked example is a gap in the published documentation. They are also
    # what memco.agent renders every tool description from, so a missing Args
    # entry costs a model its guidance on that argument.
    for sync_ns, async_ns in PAIRS:
        for cls in (sync_ns, async_ns):
            for name in sorted(public_methods(cls)):
                method = getattr(cls, name)
                doc = inspect.getdoc(method) or ""
                takes = [
                    parameter
                    for parameter in inspect.signature(method).parameters
                    if parameter != "self"
                ]
                if takes:
                    assert "Args:" in doc, f"{cls.__name__}.{name} lacks Args"
                    for parameter in takes:
                        assert f"{parameter}:" in doc, (
                            f"{cls.__name__}.{name} does not document {parameter}"
                        )
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


def test_the_scope_covers_every_operation_that_takes_a_session():
    # The scope exists so a session id cannot be dropped. An operation added to
    # the namespace with a session_id and not to the scope reopens that hole
    # silently, so the coverage is asserted rather than remembered.
    pairs = (
        (MemoryOperations, Session),
        (AsyncMemoryOperations, AsyncSession),
    )
    for namespace, scope in pairs:
        for name in sorted(public_methods(namespace)):
            parameters = inspect.signature(getattr(namespace, name)).parameters
            if "session_id" not in parameters:
                continue
            assert name in public_methods(scope), f"{scope.__name__} is missing {name}"
            assert "session_id" not in inspect.signature(getattr(scope, name)).parameters, (
                f"{scope.__name__}.{name} still asks for a session_id"
            )


def test_the_scope_never_asks_for_a_domain():
    # A session supplies the domain. Accepting one anyway would let a caller
    # name a domain the session does not belong to.
    for scope in (Session, AsyncSession):
        for name in sorted(public_methods(scope)):
            assert "domain" not in inspect.signature(getattr(scope, name)).parameters, (
                f"{scope.__name__}.{name} takes a domain"
            )
