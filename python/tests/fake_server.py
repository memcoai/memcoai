"""A real in-process gRPC server used as the test double.

Nothing about gRPC is mocked: the tests dial a genuine server over a loopback
socket, so interceptors, metadata, status codes and channel teardown are all
exercised for real.
"""

from __future__ import annotations

from concurrent import futures
from typing import Any

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from memco.memory.v1 import memory_pb2 as pb
from memco.memory.v1 import memory_pb2_grpc as pbg


class FakeMemoryService(pbg.MemoryServiceServicer):
    """Records what it was called with and returns whatever it was told to.

    Attributes:
        calls: Method names received, in order.
        metadata: One dict of request metadata per call, in the same order.
        requests: The last request message received for each method, so a test
            can assert what actually went on the wire.
        responses: Method name to canned response. A method with no entry
            returns an empty message of the right type.
        error: When set to a ``(code, details)`` pair, every method aborts with
            it instead of responding.
        transient_errors: Method name to a queue of ``(code, details)`` pairs,
            one consumed per call. A method whose queue is empty responds
            normally, which is how "fails once, then succeeds" is staged for the
            retry tests.
        rich_error: When set to a :class:`grpc.Status`, every method aborts with
            it. Unlike ``error`` this carries structured details in the
            ``grpc-status-details-bin`` trailer, which is what a sunset cutoff
            is recognised by.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.metadata: list[dict[str, str]] = []
        self.requests: dict[str, Any] = {}
        self.responses: dict[str, Any] = {}
        self.error: tuple[grpc.StatusCode, str] | None = None
        self.transient_errors: dict[str, list[tuple[grpc.StatusCode, str]]] = {}
        self.rich_error: grpc.Status | None = None

    def _handle(self, name: str, context: grpc.ServicerContext, default: Any, request: Any) -> Any:
        self.calls.append(name)
        self.metadata.append(dict(context.invocation_metadata()))
        self.requests[name] = request
        # Recorded above the aborts, so a test can count the attempts a retry
        # policy actually made.
        queued = self.transient_errors.get(name)
        if queued:
            context.abort(*queued.pop(0))
        if self.rich_error is not None:
            context.abort_with_status(self.rich_error)
        if self.error is not None:
            context.abort(*self.error)
        return self.responses.get(name, default)

    def ListDomains(self, request, context):  # noqa: N802
        return self._handle("ListDomains", context, pb.ListDomainsResponse(), request)

    def StartSession(self, request, context):  # noqa: N802
        return self._handle(
            "StartSession", context, pb.StartSessionResponse(session_id="session-a"), request
        )

    def Search(self, request, context):  # noqa: N802
        return self._handle("Search", context, pb.SearchResponse(session_id="session-a"), request)

    def GetMemory(self, request, context):  # noqa: N802
        return self._handle(
            "GetMemory",
            context,
            pb.GetMemoryResponse(memory=pb.MemoryResult(idx=request.idx)),
            request,
        )

    def CreateMemory(self, request, context):  # noqa: N802
        return self._handle(
            "CreateMemory", context, pb.CreateMemoryResponse(operation_id="create-a"), request
        )

    def EnrichMemory(self, request, context):  # noqa: N802
        return self._handle(
            "EnrichMemory", context, pb.EnrichMemoryResponse(operation_id="enrich-a"), request
        )

    def ShareFeedback(self, request, context):  # noqa: N802
        return self._handle(
            "ShareFeedback", context, pb.ShareFeedbackResponse(session_id="session-a"), request
        )

    def RevertMemory(self, request, context):  # noqa: N802
        return self._handle(
            "RevertMemory",
            context,
            pb.RevertMemoryResponse(operation_id=request.op_id, outcome=pb.REVERT_OUTCOME_MERGED),
            request,
        )

    def ImportMemories(self, request, context):  # noqa: N802
        # One outcome per memory submitted, in the order they were sent: an
        # import addresses its entries by position, so a test that maps them
        # wrongly has to be able to show it.
        return self._handle(
            "ImportMemories",
            context,
            pb.ImportMemoriesResponse(
                results=[
                    pb.ImportOutcome(index=at, status=pb.IMPORT_STATUS_QUEUED)
                    for at, _ in enumerate(request.memories)
                ]
            ),
            request,
        )


class FakeHealthService(health_pb2_grpc.HealthServicer):  # type: ignore[misc]
    """Reports whatever status it was set to, and refuses Watch as the real one does.

    Attributes:
        status: The status every Check returns.
        checked_services: The service name from each Check, in order.
        metadata: One dict of request metadata per Check, in the same order.
        transient_errors: A queue of ``(code, details)`` pairs, one consumed per
            Check, for staging a blip during client construction.
        rich_error: When set to a :class:`grpc.Status`, every Check aborts with
            it. The health service is grpc's, not Memco's, so this is how a
            test proves a status carrying structured details from a service
            this SDK does not own is not read as a Memco condition.
    """

    def __init__(self) -> None:
        self.status = health_pb2.HealthCheckResponse.SERVING
        self.checked_services: list[str] = []
        self.metadata: list[dict[str, str]] = []
        self.transient_errors: list[tuple[grpc.StatusCode, str]] = []
        self.rich_error: grpc.Status | None = None

    def Check(self, request, context):  # noqa: N802
        self.checked_services.append(request.service)
        self.metadata.append(dict(context.invocation_metadata()))
        if self.transient_errors:
            context.abort(*self.transient_errors.pop(0))
        if self.rich_error is not None:
            context.abort_with_status(self.rich_error)
        return health_pb2.HealthCheckResponse(status=self.status)

    def Watch(self, request, context):  # noqa: N802
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "watch is not supported; poll Check instead")


class Harness:
    """A running server plus the services behind it.

    Attributes:
        memory: The memory service double.
        health: The health service double.
        address: The ``host:port`` the server is listening on.
    """

    def __init__(self) -> None:
        self.memory = FakeMemoryService()
        self.health = FakeHealthService()
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        pbg.add_MemoryServiceServicer_to_server(self.memory, self._server)
        health_pb2_grpc.add_HealthServicer_to_server(self.health, self._server)
        port = self._server.add_insecure_port("localhost:0")
        self.address = f"localhost:{port}"
        self._server.start()

    def stop(self) -> None:
        """Shut the server down and wait for in-flight calls to finish."""
        self._server.stop(grace=None).wait(timeout=5)
