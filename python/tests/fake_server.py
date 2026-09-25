"""A real in-process gRPC server used as the test double.

Nothing about gRPC is mocked: the tests dial a genuine server over a loopback
socket, so interceptors, metadata, status codes and channel teardown are all
exercised for real.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent import futures
from typing import Any

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from memcoai.admin.v1 import admin_pb2 as admin_pb
from memcoai.admin.v1 import admin_pb2_grpc as admin_pbg
from memcoai.auth.v1 import auth_pb2 as auth_pb
from memcoai.auth.v1 import auth_pb2_grpc as auth_pbg
from memcoai.memory.v1 import memory_pb2 as pb
from memcoai.memory.v1 import memory_pb2_grpc as pbg

_ALL_TOOLS = (
    "list_domains",
    "start_session",
    "search",
    "get_memory",
    "create_memory",
    "enrich_memory",
    "share_feedback",
    "revert_memory",
    "import_memories",
    "list_tools",
)
"""Every method the contract declares, mirroring the real ``ListTools`` catalog.

Used as the default ``ListTools`` response -- every method available -- so the
tests that do not care about access-set filtering keep seeing the full toolset
without configuring one.
"""

_HOLD_LIMIT = 10.0
"""How long a held call waits to be released before it responds anyway.

A test that fails before releasing its hold would otherwise leave a server
thread blocked for good, and the harness's shutdown waiting on it.
"""


class Hold:
    """Keeps every call to one method in flight until the test lets it go.

    A credential stays leased for as long as a call using it is in flight, so
    holding one is how a test puts a renewal, or a close, in the middle of a
    call still carrying the old credential.

    Attributes:
        arrived: Set once a held call has reached the server, and so has been
            sent with whatever credential it carries.
        released: What held calls wait on. Set it to let them respond.
    """

    def __init__(self) -> None:
        self.arrived = threading.Event()
        self.released = threading.Event()


class _Recorder:
    """Records what a service was called with, and returns what it was told to.

    The per-call lists are appended to under one lock, so they stay
    index-aligned when calls arrive concurrently: the crosstalk tests zip
    ``received`` with ``metadata`` to pair each request with the credential it
    was sent with.

    Attributes:
        calls: Method names received, in order.
        received: Every request message, index-aligned with ``calls``.
        metadata: One dict of request metadata per call, index-aligned with
            ``calls``.
        raw_metadata: The same metadata as ``(key, value)`` pairs, exactly as
            received. A dict collapses a repeated key, which is the one thing a
            test counting credentials needs to see.
        requests: The last request message received for each method, so a test
            can assert what actually went on the wire.
        responses: Method name to canned response. A method with no entry
            returns its default.
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
        holds: Method name to a :class:`Hold` keeping its calls in flight.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.received: list[Any] = []
        self.metadata: list[dict[str, str]] = []
        self.raw_metadata: list[tuple[tuple[str, str], ...]] = []
        self.requests: dict[str, Any] = {}
        self.responses: dict[str, Any] = {}
        self.error: tuple[grpc.StatusCode, str] | None = None
        self.transient_errors: dict[str, list[tuple[grpc.StatusCode, str]]] = {}
        self.rich_error: grpc.Status | None = None
        self.holds: dict[str, Hold] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        """Forget every call recorded so far, keeping whatever has been staged."""
        with self._lock:
            self.calls.clear()
            self.received.clear()
            self.metadata.clear()
            self.raw_metadata.clear()
            self.requests.clear()

    def _arrive(self, name: str, context: grpc.ServicerContext, request: Any) -> None:
        """Record one call, then hold it or fail it as staged.

        Args:
            name: The method called.
            context: The call's servicer context.
            request: The request message received.
        """
        sent = tuple((item.key, item.value) for item in context.invocation_metadata())
        with self._lock:
            self.calls.append(name)
            self.received.append(request)
            self.metadata.append(dict(sent))
            self.raw_metadata.append(sent)
            self.requests[name] = request
            queued = self.transient_errors.get(name)
            failure = queued.pop(0) if queued else None
        hold = self.holds.get(name)
        if hold is not None:
            hold.arrived.set()
            hold.released.wait(_HOLD_LIMIT)
        # Recorded above the aborts, so a test can count the attempts a retry
        # policy actually made.
        if failure is not None:
            context.abort(*failure)
        if self.rich_error is not None:
            context.abort_with_status(self.rich_error)
        if self.error is not None:
            context.abort(*self.error)

    def _handle(self, name: str, context: grpc.ServicerContext, default: Any, request: Any) -> Any:
        """Record one call and answer it with the staged response or the default.

        Args:
            name: The method called.
            context: The call's servicer context.
            default: What to answer when no response is staged for ``name``.
            request: The request message received.

        Returns:
            The response message.
        """
        self._arrive(name, context, request)
        return self.responses.get(name, default)


class FakeMemoryService(_Recorder, pbg.MemoryServiceServicer):
    """The memory service double. See :class:`_Recorder` for what it records."""

    def ListDomains(self, request, context):  # noqa: N802
        return self._handle("ListDomains", context, pb.ListDomainsResponse(), request)

    def ListTools(self, request, context):  # noqa: N802
        default = pb.ListToolsResponse(
            tools=[
                pb.ToolDescriptor(name=name, description=f"{name} tool", available=True)
                for name in _ALL_TOOLS
            ]
        )
        return self._handle("ListTools", context, default, request)

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


class FakeTokenService(_Recorder, auth_pbg.TokenServiceServicer):
    """Issues a numbered bearer token to whoever asks: ``client-token-1``, then ``-2``.

    The number counts tokens actually issued, so a refused request does not
    use one up and the token a test expects next is always the next number.

    Attributes:
        expires_in: The lifetime in seconds each token is issued with. The real
            service's default is 3600, so it is this one's too.
    """

    def __init__(self) -> None:
        super().__init__()
        self.expires_in = 3600
        self._issued = 0

    def IssueToken(self, request, context):  # noqa: N802
        self._arrive("IssueToken", context, request)
        if "IssueToken" in self.responses:
            return self.responses["IssueToken"]
        with self._lock:
            self._issued += 1
            issued = self._issued
        return auth_pb.IssueTokenResponse(
            access_token=f"client-token-{issued}",
            token_type="Bearer",
            expires_in=self.expires_in,
        )


_NETWORK = admin_pb.Network(
    id="network-a",
    name="Acme",
    parent_id="network-root",
    domain="coding",
    region="global",
    scope="customer",
    owner="acme",
    description="Acme's support knowledge",
)
_MEMBER = admin_pb.Member(user_id="user-a", email="ada@example.com", name="Ada")
_GROUP = admin_pb.Group(id="group-a", name="Support", memory_network_id="network-a", member_count=2)
_EXTERNAL_USER = admin_pb.ExternalUser(
    id="xuser-a",
    external_id="customer-42",
    name="Ada",
    email="ada@example.com",
    roles=["reader"],
    active=True,
)
_KEY = admin_pb.ExternalUserKey(
    id="apikey-a",
    name="ci",
    value_prefix="mk_live_ab",
    roles=["reader"],
    scopes=["mcp:read"],
    valid_until=1767225600,
)


def _copy(message: Any, **changes: Any) -> Any:
    """Copy a canned message, with some fields replaced.

    Args:
        message: The message to copy.
        **changes: Field values to set on the copy.

    Returns:
        The copy.
    """
    copied = type(message)()
    copied.CopyFrom(message)
    for name, value in changes.items():
        setattr(copied, name, value)
    return copied


class FakeAdminService(_Recorder, admin_pbg.AdminServiceServicer):
    """The administration service double: every method answers something sensible.

    Lists answer one canned entry, and a method acting on something echoes the
    handles it was sent, so a result can be traced to its request.

    Impersonation keys are numbered in the order they are minted, shared
    across users: the first is ``impersonation-<external_id>-1`` with key id
    ``key-1``, the next ``...-2`` and ``key-2``. The value and the key id share
    the number, so a test can tell from a bearer which key it is.

    Attributes:
        key_lifetime: Seconds from minting to each impersonation key's expiry.
            The real service's default is 60 minutes, so it is this one's too.
        clock: The wall clock a key's absolute ``expires_at`` is computed from.
            A test that moves the SDK's clock points this at the same one, so
            the lifetime the SDK reads back is the one set here.
    """

    def __init__(self) -> None:
        super().__init__()
        self.key_lifetime = 3600
        self.clock: Callable[[], float] = time.time
        self._keys = 0

    def ListNetworks(self, request, context):  # noqa: N802
        default = admin_pb.ListNetworksResponse(networks=[_NETWORK], total_count=1)
        return self._handle("ListNetworks", context, default, request)

    def CreateNetwork(self, request, context):  # noqa: N802
        default = admin_pb.Network(
            id="network-new",
            name=request.name,
            parent_id=request.parent_id,
            domain=request.domain or "coding",
            region=request.region or "global",
            scope=request.scope,
            owner=request.owner,
            description=request.description,
        )
        return self._handle("CreateNetwork", context, default, request)

    def UpdateNetwork(self, request, context):  # noqa: N802
        changed = {
            name: getattr(request, name)
            for name in ("name", "parent_id", "scope", "owner", "description")
            if request.HasField(name)
        }
        default = _copy(_NETWORK, id=request.id, **changed)
        return self._handle("UpdateNetwork", context, default, request)

    def DeleteNetwork(self, request, context):  # noqa: N802
        default = admin_pb.DeleteNetworkResponse(
            id=request.id, removed={"network_members": 1, "memories": 3}
        )
        return self._handle("DeleteNetwork", context, default, request)

    def ListNetworkMembers(self, request, context):  # noqa: N802
        default = admin_pb.ListNetworkMembersResponse(members=[_MEMBER], total_count=1)
        return self._handle("ListNetworkMembers", context, default, request)

    def AddNetworkMember(self, request, context):  # noqa: N802
        default = admin_pb.AddNetworkMemberResponse(id=request.id, user_id=request.user_id)
        return self._handle("AddNetworkMember", context, default, request)

    def RemoveNetworkMember(self, request, context):  # noqa: N802
        default = admin_pb.RemoveNetworkMemberResponse(id=request.id, user_id=request.user_id)
        return self._handle("RemoveNetworkMember", context, default, request)

    def ListGroups(self, request, context):  # noqa: N802
        default = admin_pb.ListGroupsResponse(groups=[_GROUP], total_count=1)
        return self._handle("ListGroups", context, default, request)

    def ListGroupMembers(self, request, context):  # noqa: N802
        default = admin_pb.ListGroupMembersResponse(members=[_MEMBER])
        return self._handle("ListGroupMembers", context, default, request)

    def AddNetworkGroup(self, request, context):  # noqa: N802
        default = admin_pb.AddNetworkGroupResponse(id=request.id, group_id=request.group_id)
        return self._handle("AddNetworkGroup", context, default, request)

    def RemoveNetworkGroup(self, request, context):  # noqa: N802
        default = admin_pb.RemoveNetworkGroupResponse(id=request.id, group_id=request.group_id)
        return self._handle("RemoveNetworkGroup", context, default, request)

    def ListExternalUsers(self, request, context):  # noqa: N802
        default = admin_pb.ListExternalUsersResponse(external_users=[_EXTERNAL_USER], total_count=1)
        return self._handle("ListExternalUsers", context, default, request)

    def GetExternalUser(self, request, context):  # noqa: N802
        default = _copy(_EXTERNAL_USER, external_id=request.external_id)
        return self._handle("GetExternalUser", context, default, request)

    def CreateExternalUser(self, request, context):  # noqa: N802
        default = admin_pb.ExternalUser(
            id="xuser-new",
            external_id=request.external_id,
            name=request.name,
            email=request.email,
            roles=request.roles,
            active=True,
        )
        return self._handle("CreateExternalUser", context, default, request)

    def UpdateExternalUser(self, request, context):  # noqa: N802
        default = _copy(_EXTERNAL_USER, external_id=request.external_id)
        for name in ("name", "email"):
            if request.HasField(name):
                setattr(default, name, getattr(request, name))
        if request.roles:
            default.roles[:] = request.roles
        return self._handle("UpdateExternalUser", context, default, request)

    def DeleteExternalUser(self, request, context):  # noqa: N802
        default = admin_pb.DeleteExternalUserResponse(external_id=request.external_id)
        return self._handle("DeleteExternalUser", context, default, request)

    def ListExternalUserKeys(self, request, context):  # noqa: N802
        default = admin_pb.ListExternalUserKeysResponse(keys=[_KEY])
        return self._handle("ListExternalUserKeys", context, default, request)

    def CreateExternalUserKey(self, request, context):  # noqa: N802
        default = admin_pb.CreateExternalUserKeyResponse(
            key=_copy(
                _KEY,
                id="apikey-new",
                name=request.name,
                value_prefix="mk_live_ne",
                valid_until=request.valid_until or _KEY.valid_until,
            ),
            value="mk_live_new-key-value",
        )
        return self._handle("CreateExternalUserKey", context, default, request)

    def DeleteExternalUserKey(self, request, context):  # noqa: N802
        default = admin_pb.DeleteExternalUserKeyResponse(
            external_id=request.external_id, key_id=request.key_id
        )
        return self._handle("DeleteExternalUserKey", context, default, request)

    def ImpersonateExternalUser(self, request, context):  # noqa: N802
        self._arrive("ImpersonateExternalUser", context, request)
        if "ImpersonateExternalUser" in self.responses:
            return self.responses["ImpersonateExternalUser"]
        with self._lock:
            self._keys += 1
            number = self._keys
        return admin_pb.ImpersonationKey(
            value=f"impersonation-{request.external_id}-{number}",
            expires_at=int(self.clock()) + self.key_lifetime,
            roles=["reader", "creator"],
            scopes=["memory"],
            key_id=f"key-{number}",
        )

    def EndImpersonation(self, request, context):  # noqa: N802
        default = admin_pb.EndImpersonationResponse(
            external_id=request.external_id, key_id=request.key_id
        )
        return self._handle("EndImpersonation", context, default, request)


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
        tokens: The token service double.
        admin: The administration service double.
        health: The health service double.
        address: The ``host:port`` the server is listening on.
    """

    def __init__(self) -> None:
        self.memory = FakeMemoryService()
        self.tokens = FakeTokenService()
        self.admin = FakeAdminService()
        self.health = FakeHealthService()
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        pbg.add_MemoryServiceServicer_to_server(self.memory, self._server)
        auth_pbg.add_TokenServiceServicer_to_server(self.tokens, self._server)
        admin_pbg.add_AdminServiceServicer_to_server(self.admin, self._server)
        health_pb2_grpc.add_HealthServicer_to_server(self.health, self._server)
        port = self._server.add_insecure_port("localhost:0")
        self.address = f"localhost:{port}"
        self._server.start()

    def stop(self) -> None:
        """Shut the server down and wait for in-flight calls to finish."""
        self._server.stop(grace=None).wait(timeout=5)
