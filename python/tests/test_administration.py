"""Network and user administration, reached as ``client.networks`` and ``client.users``.

What each method sends is read off the fake server and compared as a whole
message, so a field sent that should not have been fails as surely as one left
out. Results are compared as the SDK's own types, since presenting the
service's answer in Python's terms is this layer's whole job.
"""

from __future__ import annotations

import dataclasses
import inspect
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import grpc
import pytest
from google.protobuf import any_pb2
from google.rpc import code_pb2, error_details_pb2, status_pb2
from grpc_status import rpc_status

import memcoai
from memcoai import AsyncMemco, Memco, administration, errors, types
from memcoai.admin.v1 import admin_pb2 as admin_pb
from memcoai.administration import (
    AsyncNetworkOperations,
    AsyncUserOperations,
    NetworkOperations,
    UserOperations,
)
from memcoai.types import (
    CreatedKey,
    DeletedNetwork,
    ExternalUser,
    ExternalUserKey,
    ExternalUserList,
    Group,
    GroupList,
    Member,
    MemberList,
    MemberPlacement,
    Network,
    NetworkList,
)

from .fake_server import Harness

XID = "customer-42"
NEW_YEAR = datetime(2026, 1, 1, tzinfo=timezone.utc)
NEW_YEAR_SECONDS = 1767225600


@dataclass(frozen=True)
class Call:
    """One method, and what it is called with.

    Attributes:
        namespace: The client attribute the method is reached through.
        method: The method's name.
        args: Positional arguments.
        kwargs: Keyword arguments.
    """

    namespace: str
    method: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)

    def on(self, client: Memco | AsyncMemco) -> Any:
        return getattr(getattr(client, self.namespace), self.method)(*self.args, **self.kwargs)


@dataclass(frozen=True)
class Case:
    """One call, what it must send, and what comes back.

    Attributes:
        call: The method and its arguments.
        sent: The request the service must receive, exactly.
        returns: The result's type, or ``None`` for a method returning nothing.
        of: For a tuple result, the type of each entry.
    """

    call: Call
    sent: Any
    returns: type | None
    of: type | None = None

    @property
    def rpc(self) -> str:
        return type(self.sent).__name__.removesuffix("Request")


CASES = [
    Case(
        Call(
            "networks",
            "list",
            kwargs={
                "name": "Acme",
                "scope": "customer",
                "owner": "acme",
                "domain": "coding",
                "parent_id": "root",
                "ids": ["network-a", "network-b"],
                "page": 2,
                "page_size": 50,
            },
        ),
        admin_pb.ListNetworksRequest(
            name="Acme",
            scope="customer",
            owner="acme",
            domain="coding",
            parent_id="root",
            ids=["network-a", "network-b"],
            page=2,
            page_size=50,
        ),
        NetworkList,
    ),
    Case(
        Call(
            "networks",
            "create",
            kwargs={
                "name": "Acme",
                "parent_id": "network-root",
                "domain": "coding",
                "region": "eu",
                "scope": "customer",
                "owner": "acme",
                "description": "Acme's support knowledge",
            },
        ),
        admin_pb.CreateNetworkRequest(
            name="Acme",
            parent_id="network-root",
            domain="coding",
            region="eu",
            scope="customer",
            owner="acme",
            description="Acme's support knowledge",
        ),
        Network,
    ),
    Case(
        Call("networks", "update", ("network-a",), {"name": "Acme Corp", "scope": "customer"}),
        admin_pb.UpdateNetworkRequest(id="network-a", name="Acme Corp", scope="customer"),
        Network,
    ),
    Case(
        Call("networks", "delete", ("network-a",)),
        admin_pb.DeleteNetworkRequest(id="network-a"),
        DeletedNetwork,
    ),
    Case(
        Call(
            "networks",
            "list_members",
            ("network-a",),
            {"search": "ada", "page": 1, "page_size": 20},
        ),
        admin_pb.ListNetworkMembersRequest(id="network-a", search="ada", page=1, page_size=20),
        MemberList,
    ),
    Case(
        Call("networks", "add_member", ("network-a", "user-a"), {"force": True}),
        admin_pb.AddNetworkMemberRequest(id="network-a", user_id="user-a", force=True),
        MemberPlacement,
    ),
    Case(
        Call("networks", "remove_member", ("network-a", "user-a")),
        admin_pb.RemoveNetworkMemberRequest(id="network-a", user_id="user-a"),
        None,
    ),
    Case(
        Call(
            "networks",
            "list_groups",
            kwargs={
                "name": "Support",
                "network_id": "network-a",
                "ids": ["group-a"],
                "page": 1,
                "page_size": 20,
            },
        ),
        admin_pb.ListGroupsRequest(
            name="Support", network_id="network-a", ids=["group-a"], page=1, page_size=20
        ),
        GroupList,
    ),
    Case(
        Call("networks", "list_group_members", ("group-a",)),
        admin_pb.ListGroupMembersRequest(id="group-a"),
        tuple,
        of=Member,
    ),
    Case(
        Call("networks", "add_group", ("network-a", "group-a")),
        admin_pb.AddNetworkGroupRequest(id="network-a", group_id="group-a"),
        None,
    ),
    Case(
        Call("networks", "remove_group", ("network-a", "group-a")),
        admin_pb.RemoveNetworkGroupRequest(id="network-a", group_id="group-a"),
        None,
    ),
    Case(
        Call("users", "list", kwargs={"search": "ada", "page": 1, "page_size": 20}),
        admin_pb.ListExternalUsersRequest(search="ada", page=1, page_size=20),
        ExternalUserList,
    ),
    Case(
        Call("users", "get", (XID,)),
        admin_pb.GetExternalUserRequest(external_id=XID),
        ExternalUser,
    ),
    Case(
        Call(
            "users",
            "create",
            (XID,),
            {"roles": ["reader", "creator"], "name": "Ada", "email": "ada@example.com"},
        ),
        admin_pb.CreateExternalUserRequest(
            external_id=XID, name="Ada", email="ada@example.com", roles=["reader", "creator"]
        ),
        ExternalUser,
    ),
    Case(
        Call("users", "update", (XID,), {"name": "Ada L.", "roles": ["reader"]}),
        admin_pb.UpdateExternalUserRequest(external_id=XID, name="Ada L.", roles=["reader"]),
        ExternalUser,
    ),
    Case(
        Call("users", "delete", (XID,)),
        admin_pb.DeleteExternalUserRequest(external_id=XID),
        None,
    ),
    Case(
        Call("users", "list_keys", (XID,)),
        admin_pb.ListExternalUserKeysRequest(external_id=XID),
        tuple,
        of=ExternalUserKey,
    ),
    Case(
        Call(
            "users",
            "create_key",
            (XID,),
            {"preset": "mcp_ro", "name": "ci", "valid_until": NEW_YEAR},
        ),
        admin_pb.CreateExternalUserKeyRequest(
            external_id=XID, name="ci", preset="mcp_ro", valid_until=NEW_YEAR_SECONDS
        ),
        CreatedKey,
    ),
    Case(
        Call("users", "delete_key", (XID, "apikey-a")),
        admin_pb.DeleteExternalUserKeyRequest(external_id=XID, key_id="apikey-a"),
        None,
    ),
]


def named(case: Case) -> str:
    return f"{case.call.namespace}.{case.call.method}"


def check_round_trip(case: Case, result: Any, harness: Harness) -> None:
    assert harness.admin.calls == [case.rpc]
    assert harness.admin.received == [case.sent]
    assert harness.admin.metadata[0]["authorization"] == "Bearer client-token-1"
    if case.returns is None:
        assert result is None
    else:
        assert isinstance(result, case.returns)
    if case.of is not None:
        assert result, "the canned answer holds an entry to convert"
        assert all(isinstance(entry, case.of) for entry in result)


def test_the_round_trip_covers_every_method():
    # Written out rather than read off the classes, so a method added without
    # a case fails here instead of going untested.
    covered = {(case.call.namespace, case.call.method) for case in CASES}
    offered = {
        (attribute, name)
        for attribute, cls in (("networks", NetworkOperations), ("users", UserOperations))
        for name, _ in inspect.getmembers(cls, inspect.isfunction)
        if not name.startswith("_")
    }
    assert covered == offered


@pytest.mark.parametrize("case", CASES, ids=named)
def test_each_method_sends_its_request_and_returns_its_type(
    credentialed: Memco, harness: Harness, case: Case
):
    check_round_trip(case, case.call.on(credentialed), harness)


@pytest.mark.parametrize("case", CASES, ids=named)
async def test_each_async_method_sends_its_request_and_returns_its_type(
    async_credentialed: AsyncMemco, harness: Harness, case: Case
):
    check_round_trip(case, await case.call.on(async_credentialed), harness)


def test_unset_filters_and_pages_send_nothing(credentialed: Memco, harness: Harness):
    # Page 0 is the service's default page; the first page is 1.
    credentialed.networks.list()
    credentialed.networks.list_groups()
    credentialed.networks.list_members("network-a")
    credentialed.users.list()
    assert harness.admin.received == [
        admin_pb.ListNetworksRequest(),
        admin_pb.ListGroupsRequest(),
        admin_pb.ListNetworkMembersRequest(id="network-a"),
        admin_pb.ListExternalUsersRequest(),
    ]


def test_ids_may_be_any_iterable(credentialed: Memco, harness: Harness):
    credentialed.networks.list(ids=iter(["network-a", "network-b"]))
    assert list(harness.admin.received[0].ids) == ["network-a", "network-b"]


def test_every_method_takes_a_timeout_last():
    for cls in (NetworkOperations, AsyncNetworkOperations, UserOperations, AsyncUserOperations):
        for name, method in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_"):
                continue
            last = list(inspect.signature(method).parameters.values())[-1]
            assert (last.name, last.kind, last.default) == (
                "timeout",
                inspect.Parameter.KEYWORD_ONLY,
                None,
            ), f"{cls.__name__}.{name}"


def test_the_module_is_exported_from_the_package():
    assert memcoai.administration is administration
    assert "administration" in memcoai.__all__


@pytest.mark.parametrize(
    "result_type",
    [
        Network,
        NetworkList,
        Member,
        MemberList,
        MemberPlacement,
        DeletedNetwork,
        Group,
        GroupList,
        ExternalUser,
        ExternalUserList,
        ExternalUserKey,
        CreatedKey,
    ],
    ids=lambda result_type: result_type.__name__,
)
def test_every_result_type_is_an_immutable_public_value(result_type: type):
    # Frozen so a result can be cached or shared between threads, like every
    # other result this SDK returns.
    params = result_type.__dataclass_params__  # type: ignore[attr-defined]
    assert dataclasses.is_dataclass(result_type)
    assert params.frozen
    assert "__slots__" in vars(result_type)
    assert result_type.__name__ in types.__all__


# --- results in Python's terms -------------------------------------------


def test_a_network_reads_its_absent_fields_as_none(credentialed: Memco, harness: Harness):
    harness.admin.responses["ListNetworks"] = admin_pb.ListNetworksResponse(
        networks=[
            admin_pb.Network(id="network-root", name="Root", domain="coding", region="global")
        ],
        total_count=7,
    )
    assert credentialed.networks.list() == NetworkList(
        networks=(
            Network(
                id="network-root",
                name="Root",
                parent_id=None,
                domain="coding",
                region="global",
                scope=None,
                owner=None,
                description="",
            ),
        ),
        total_count=7,
    )


def test_a_network_reads_every_field_it_carries(credentialed: Memco, harness: Harness):
    harness.admin.responses["CreateNetwork"] = admin_pb.Network(
        id="network-a",
        name="Acme",
        parent_id="network-root",
        domain="coding",
        region="eu",
        scope="customer",
        owner="acme",
        description="Acme's support knowledge",
    )
    created = credentialed.networks.create(name="Acme", parent_id="network-root")
    assert created == Network(
        id="network-a",
        name="Acme",
        parent_id="network-root",
        domain="coding",
        region="eu",
        scope="customer",
        owner="acme",
        description="Acme's support knowledge",
    )


def test_members_arrive_with_their_total(credentialed: Memco, harness: Harness):
    harness.admin.responses["ListNetworkMembers"] = admin_pb.ListNetworkMembersResponse(
        members=[admin_pb.Member(user_id="user-a", email="ada@example.com", name="Ada")],
        total_count=3,
    )
    assert credentialed.networks.list_members("network-a") == MemberList(
        members=(Member(user_id="user-a", email="ada@example.com", name="Ada"),), total_count=3
    )


def test_a_placement_names_the_network_a_moved_user_left(credentialed: Memco, harness: Harness):
    harness.admin.responses["AddNetworkMember"] = admin_pb.AddNetworkMemberResponse(
        id="network-b", user_id="user-a", moved_from="network-a"
    )
    placed = credentialed.networks.add_member("network-b", "user-a", force=True)
    assert placed == MemberPlacement(
        network_id="network-b", user_id="user-a", moved_from="network-a"
    )


def test_a_placement_that_moved_nobody_reads_moved_from_as_none(
    credentialed: Memco, harness: Harness
):
    assert credentialed.networks.add_member("network-a", "user-a").moved_from is None
    # force defaults off: relocating someone is never the accidental outcome.
    assert harness.admin.received == [
        admin_pb.AddNetworkMemberRequest(id="network-a", user_id="user-a")
    ]


def test_a_deletion_reports_what_the_cascade_removed_sorted_by_table(
    credentialed: Memco, harness: Harness
):
    harness.admin.responses["DeleteNetwork"] = admin_pb.DeleteNetworkResponse(
        id="network-a", removed={"network_members": 2, "memories": 5, "groups": 1}
    )
    assert credentialed.networks.delete("network-a") == DeletedNetwork(
        id="network-a", removed=(("groups", 1), ("memories", 5), ("network_members", 2))
    )


def test_a_group_bound_to_no_network_reads_as_none(credentialed: Memco, harness: Harness):
    harness.admin.responses["ListGroups"] = admin_pb.ListGroupsResponse(
        groups=[admin_pb.Group(id="group-a", name="Support", member_count=4)], total_count=1
    )
    assert credentialed.networks.list_groups() == GroupList(
        groups=(Group(id="group-a", name="Support", memory_network_id=None, member_count=4),),
        total_count=1,
    )


def test_a_group_s_members_arrive_as_a_tuple(credentialed: Memco, harness: Harness):
    harness.admin.responses["ListGroupMembers"] = admin_pb.ListGroupMembersResponse(
        members=[admin_pb.Member(user_id="user-a", email="ada@example.com", name="Ada")]
    )
    assert credentialed.networks.list_group_members("group-a") == (
        Member(user_id="user-a", email="ada@example.com", name="Ada"),
    )


def test_an_external_user_reads_its_roles_as_a_tuple(credentialed: Memco, harness: Harness):
    user = admin_pb.ExternalUser(
        id="xuser-a",
        external_id=XID,
        name="Ada",
        email="ada@example.com",
        roles=["reader", "creator"],
        active=True,
    )
    harness.admin.responses["ListExternalUsers"] = admin_pb.ListExternalUsersResponse(
        external_users=[user], total_count=9
    )
    expected = ExternalUser(
        id="xuser-a",
        external_id=XID,
        name="Ada",
        email="ada@example.com",
        roles=("reader", "creator"),
        active=True,
    )
    assert credentialed.users.list() == ExternalUserList(external_users=(expected,), total_count=9)


def test_a_key_expiry_arrives_as_an_aware_utc_datetime(credentialed: Memco, harness: Harness):
    harness.admin.responses["ListExternalUserKeys"] = admin_pb.ListExternalUserKeysResponse(
        keys=[
            admin_pb.ExternalUserKey(
                id="apikey-a",
                name="ci",
                value_prefix="mk_live_ab",
                roles=["reader"],
                scopes=["mcp:read"],
                valid_until=NEW_YEAR_SECONDS,
            )
        ]
    )
    (key,) = credentialed.users.list_keys(XID)
    assert key == ExternalUserKey(
        id="apikey-a",
        name="ci",
        value_prefix="mk_live_ab",
        roles=("reader",),
        scopes=("mcp:read",),
        valid_until=NEW_YEAR,
    )
    assert key.valid_until is not None
    assert key.valid_until.utcoffset() == timedelta(0)


def test_a_key_that_never_expires_reads_its_expiry_as_none(credentialed: Memco, harness: Harness):
    harness.admin.responses["ListExternalUserKeys"] = admin_pb.ListExternalUserKeysResponse(
        keys=[admin_pb.ExternalUserKey(id="apikey-a", valid_until=0)]
    )
    (key,) = credentialed.users.list_keys(XID)
    assert key.valid_until is None


def test_an_expiry_in_any_zone_is_sent_as_the_same_instant(credentialed: Memco, harness: Harness):
    in_oslo = NEW_YEAR.astimezone(timezone(timedelta(hours=1)))
    credentialed.users.create_key(XID, preset="mcp_ro", valid_until=in_oslo)
    assert harness.admin.received[-1].valid_until == NEW_YEAR_SECONDS


def test_no_expiry_sends_zero_for_the_service_default(credentialed: Memco, harness: Harness):
    credentialed.users.create_key(XID, preset="mcp_ro")
    assert harness.admin.received == [
        admin_pb.CreateExternalUserKeyRequest(external_id=XID, preset="mcp_ro")
    ]


def test_a_created_key_carries_its_value_but_never_shows_it(credentialed: Memco, harness: Harness):
    # The value is shown once, here, and is a working credential: it must
    # not reach a log line or a crash report through a repr.
    harness.admin.responses["CreateExternalUserKey"] = admin_pb.CreateExternalUserKeyResponse(
        key=admin_pb.ExternalUserKey(id="apikey-new", name="ci", value_prefix="mk_live_ne"),
        value="mk_live_new-secret-value",
    )
    created = credentialed.users.create_key(XID, preset="mcp_ro", name="ci")
    assert created.value == "mk_live_new-secret-value"
    assert created.key.id == "apikey-new"
    assert "mk_live_new-secret-value" not in repr(created)
    assert "mk_live_new-secret-value" not in str(created)


def test_a_created_key_arriving_without_its_key_is_an_internal_error(
    credentialed: Memco, harness: Harness
):
    harness.admin.responses["CreateExternalUserKey"] = admin_pb.CreateExternalUserKeyResponse(
        value="mk_live_new-secret-value"
    )
    with pytest.raises(errors.MemcoInternalError) as caught:
        credentialed.users.create_key(XID, preset="mcp_ro")
    assert "mk_live_new-secret-value" not in str(caught.value)
    # Nor in a frame of its traceback, which an error tracker capturing locals
    # ships: the caller never received the key, so could not know to revoke it.
    held = [
        (frame.f_code.co_name, name)
        for frame, _ in traceback.walk_tb(caught.value.__traceback__)
        for name, value in frame.f_locals.items()
        if "mk_live_new-secret-value" in repr(value)
    ]
    assert held == []


def test_a_created_key_whose_expiry_cannot_be_read_is_an_internal_error(
    credentialed: Memco, harness: Harness
):
    # Milliseconds where seconds belong put the expiry past the year 9999;
    # the value must not escape in the frames of a raw conversion error.
    harness.admin.responses["CreateExternalUserKey"] = admin_pb.CreateExternalUserKeyResponse(
        key=admin_pb.ExternalUserKey(id="apikey-new", valid_until=1767225600000),
        value="mk_live_new-secret-value",
    )
    with pytest.raises(errors.MemcoInternalError) as caught:
        credentialed.users.create_key(XID, preset="mcp_ro")
    assert "mk_live_new-secret-value" not in str(caught.value)
    held = [
        (frame.f_code.co_name, name)
        for frame, _ in traceback.walk_tb(caught.value.__traceback__)
        for name, value in frame.f_locals.items()
        if "mk_live_new-secret-value" in repr(value)
    ]
    assert held == []


# --- patches -------------------------------------------------------------

NETWORK_PATCHABLE = ["name", "parent_id", "scope", "owner", "description"]


@pytest.mark.parametrize("field", NETWORK_PATCHABLE)
def test_a_network_patch_sends_only_what_it_was_given(
    credentialed: Memco, harness: Harness, field: str
):
    patch: dict[str, Any] = {field: "changed"}
    credentialed.networks.update("network-a", **patch)
    sent = harness.admin.received[-1]
    assert [name for name in NETWORK_PATCHABLE if sent.HasField(name)] == [field]
    assert getattr(sent, field) == "changed"


@pytest.mark.parametrize("field", NETWORK_PATCHABLE)
def test_an_empty_string_is_sent_to_clear_a_network_field(
    credentialed: Memco, harness: Harness, field: str
):
    # None leaves a field alone; "" is a value, and clearing it is the
    # service's to allow or refuse.
    patch: dict[str, Any] = {field: ""}
    credentialed.networks.update("network-a", **patch)
    sent = harness.admin.received[-1]
    assert sent.HasField(field)
    assert getattr(sent, field) == ""


def test_a_network_patch_given_nothing_sends_no_field(credentialed: Memco, harness: Harness):
    credentialed.networks.update("network-a")
    sent = harness.admin.received[-1]
    assert [name for name in NETWORK_PATCHABLE if sent.HasField(name)] == []


def test_a_user_patch_sends_only_what_it_was_given(credentialed: Memco, harness: Harness):
    credentialed.users.update(XID, email="")
    sent = harness.admin.received[-1]
    assert not sent.HasField("name")
    assert sent.HasField("email")
    assert sent.email == ""
    # No roles given leaves them as they are.
    assert list(sent.roles) == []


# --- rejected before anything is sent ------------------------------------

REJECTED = [
    pytest.param(Call("networks", "update", (" ",)), id="update blank network"),
    pytest.param(Call("networks", "delete", ("",)), id="delete empty network"),
    pytest.param(Call("networks", "list_members", ("  ",)), id="list_members blank network"),
    pytest.param(Call("networks", "add_member", ("", "user-a")), id="add_member empty network"),
    pytest.param(Call("networks", "add_member", ("network-a", " ")), id="add_member blank user"),
    pytest.param(Call("networks", "remove_member", ("network-a", "")), id="remove_member no user"),
    pytest.param(Call("networks", "list_group_members", ("",)), id="list_group_members empty"),
    pytest.param(Call("networks", "add_group", ("network-a", "")), id="add_group empty group"),
    pytest.param(Call("networks", "remove_group", (" ", "group-a")), id="remove_group blank"),
    # A bare string is iterable, so it would go out one character per id.
    pytest.param(Call("networks", "list", kwargs={"ids": "network-a"}), id="list ids as a str"),
    pytest.param(
        Call("networks", "list_groups", kwargs={"ids": "group-a"}), id="groups ids as a str"
    ),
    pytest.param(Call("users", "get", ("",)), id="get empty user"),
    pytest.param(Call("users", "create", ("  ",), {"roles": ["reader"]}), id="create blank user"),
    pytest.param(Call("users", "create", (XID,), {"roles": "reader"}), id="create roles as a str"),
    # On the wire an empty role list reads as "not given", so on create the
    # service would see no roles at all, and on update no change.
    pytest.param(Call("users", "create", (XID,), {"roles": []}), id="create no roles"),
    pytest.param(Call("users", "update", ("",)), id="update empty user"),
    pytest.param(Call("users", "update", (XID,), {"roles": "reader"}), id="update roles as a str"),
    pytest.param(Call("users", "update", (XID,), {"roles": []}), id="update no roles"),
    pytest.param(Call("users", "delete", (" ",)), id="delete blank user"),
    pytest.param(Call("users", "list_keys", ("",)), id="list_keys empty user"),
    pytest.param(Call("users", "create_key", ("",), {"preset": "mcp_ro"}), id="create_key no user"),
    # A naive datetime names no instant: which zone it meant is a guess.
    pytest.param(
        Call(
            "users",
            "create_key",
            (XID,),
            {"preset": "mcp_ro", "valid_until": datetime(2026, 1, 1)},  # noqa: DTZ001
        ),
        id="create_key naive expiry",
    ),
    pytest.param(Call("users", "delete_key", (XID, "")), id="delete_key empty key"),
    pytest.param(Call("users", "delete_key", ("", "apikey-a")), id="delete_key empty user"),
]


@pytest.mark.parametrize("call", REJECTED)
def test_a_bad_argument_is_rejected_before_anything_is_sent(
    credentialed: Memco, harness: Harness, call: Call
):
    with pytest.raises(errors.MemcoInvalidRequestError):
        call.on(credentialed)
    assert harness.admin.calls == []


@pytest.mark.parametrize("call", REJECTED)
async def test_async_a_bad_argument_is_rejected_before_anything_is_sent(
    async_credentialed: AsyncMemco, harness: Harness, call: Call
):
    with pytest.raises(errors.MemcoInvalidRequestError):
        await call.on(async_credentialed)
    assert harness.admin.calls == []


# --- what the service refuses --------------------------------------------


def test_a_duplicate_raises_already_exists(credentialed: Memco, harness: Harness):
    harness.admin.error = (grpc.StatusCode.ALREADY_EXISTS, f"external user {XID} already exists")
    with pytest.raises(errors.MemcoAlreadyExistsError) as caught:
        credentialed.users.create(XID, roles=["reader"])
    assert isinstance(caught.value, errors.MemcoAPIError)


def placement_refusal(
    reason: str,
    message: str,
    domain: str = "memco.ai",
    metadata: dict[str, str] | None = None,
) -> grpc.Status:
    """Build the FAILED_PRECONDITION a refused placement arrives as, reason and all."""
    detail = any_pb2.Any()
    detail.Pack(error_details_pb2.ErrorInfo(reason=reason, domain=domain, metadata=metadata))
    return rpc_status.to_status(
        status_pb2.Status(code=code_pb2.FAILED_PRECONDITION, message=message, details=[detail])
    )


Refusal = type[
    errors.MemcoUserAlreadyAssignedNetworkError | errors.MemcoExternalUserNeedsCustomerNetworkError
]

PLACEMENT_REFUSALS = [
    pytest.param(
        "USER_ALREADY_ASSIGNED_NETWORK",
        errors.MemcoUserAlreadyAssignedNetworkError,
        "the user already belongs to another network in this domain",
        id="already-assigned",
    ),
    pytest.param(
        "EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK",
        errors.MemcoExternalUserNeedsCustomerNetworkError,
        "an external user can only join a customer network",
        id="needs-customer-network",
    ),
]


@pytest.mark.parametrize(("reason", "expected", "message"), PLACEMENT_REFUSALS)
def test_a_refused_placement_raises_the_error_its_reason_names(
    credentialed: Memco, harness: Harness, reason: str, expected: Refusal, message: str
):
    harness.admin.rich_error = placement_refusal(reason, message)
    with pytest.raises(expected) as caught:
        credentialed.networks.add_member("network-b", "xuser-a")
    # Still caught by a handler written for any precondition, and never read as
    # a sunset, which arrives with the same status code.
    assert isinstance(caught.value, errors.MemcoPreconditionFailedError)
    assert not isinstance(caught.value, errors.MemcoSunsetError)
    assert type(caught.value) is expected
    assert expected.reason == reason
    assert caught.value.message == message


@pytest.mark.parametrize(("reason", "expected", "message"), PLACEMENT_REFUSALS)
async def test_async_a_refused_placement_raises_the_error_its_reason_names(
    async_credentialed: AsyncMemco, harness: Harness, reason: str, expected: Refusal, message: str
):
    harness.admin.rich_error = placement_refusal(reason, message)
    with pytest.raises(expected) as caught:
        await async_credentialed.networks.add_member("network-b", "xuser-a")
    assert type(caught.value) is expected
    assert expected.reason == reason


def test_a_refused_move_names_the_network_the_user_is_in(credentialed: Memco, harness: Harness):
    # What the service attaches as debug detail, so a caller need not parse it
    # out of the message.
    harness.admin.rich_error = placement_refusal(
        "USER_ALREADY_ASSIGNED_NETWORK",
        "network_move_required: the user already belongs to another network",
        metadata={"current_network_id": "network-a", "current_network_name": "Billing platform"},
    )
    with pytest.raises(errors.MemcoUserAlreadyAssignedNetworkError) as caught:
        credentialed.networks.add_member("network-b", "xuser-a")
    assert caught.value.current_network_id == "network-a"
    assert caught.value.current_network_name == "Billing platform"
    assert dict(caught.value.metadata) == {
        "current_network_id": "network-a",
        "current_network_name": "Billing platform",
    }


def test_a_customer_network_refusal_names_the_scope_it_needs(credentialed: Memco, harness: Harness):
    harness.admin.rich_error = placement_refusal(
        "EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK",
        "external_user_needs_customer_network: an external user may only join a customer network",
        metadata={"required_network_scope": "customer"},
    )
    with pytest.raises(errors.MemcoExternalUserNeedsCustomerNetworkError) as caught:
        credentialed.networks.add_member("network-root", "xuser-a")
    assert caught.value.required_network_scope == "customer"


def test_a_refusal_without_metadata_reports_none_rather_than_failing(
    credentialed: Memco, harness: Harness
):
    # The metadata is the service's to send. Absent, the fields are unknown,
    # not an error of the SDK's.
    harness.admin.rich_error = placement_refusal("USER_ALREADY_ASSIGNED_NETWORK", "refused")
    with pytest.raises(errors.MemcoUserAlreadyAssignedNetworkError) as caught:
        credentialed.networks.add_member("network-b", "xuser-a")
    assert (caught.value.current_network_id, caught.value.current_network_name) == (None, None)
    assert dict(caught.value.metadata) == {}


def test_any_precondition_carries_the_metadata_the_service_sent(
    credentialed: Memco, harness: Harness
):
    # A reason this build does not model still hands its debug detail over.
    harness.admin.rich_error = placement_refusal(
        "SOME_FUTURE_REASON", "refused", metadata={"detail": "value"}
    )
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        credentialed.networks.add_member("network-b", "xuser-a")
    assert type(caught.value) is errors.MemcoPreconditionFailedError
    assert dict(caught.value.metadata) == {"detail": "value"}


def test_the_metadata_cannot_be_changed(credentialed: Memco, harness: Harness):
    harness.admin.rich_error = placement_refusal(
        "USER_ALREADY_ASSIGNED_NETWORK", "refused", metadata={"current_network_id": "network-a"}
    )
    with pytest.raises(errors.MemcoUserAlreadyAssignedNetworkError) as caught:
        credentialed.networks.add_member("network-b", "xuser-a")
    with pytest.raises(TypeError):
        caught.value.metadata["current_network_id"] = "network-z"  # type: ignore[index]


def test_a_placement_reason_from_another_domain_is_not_read_as_memcos(
    credentialed: Memco, harness: Harness
):
    harness.admin.rich_error = placement_refusal(
        "USER_ALREADY_ASSIGNED_NETWORK", "refused elsewhere", domain="example.com"
    )
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        credentialed.networks.add_member("network-b", "xuser-a")
    assert type(caught.value) is errors.MemcoPreconditionFailedError


def test_an_external_user_outside_a_customer_network_is_a_precondition_not_a_sunset(
    credentialed: Memco, harness: Harness
):
    # FAILED_PRECONDITION is also how a sunset arrives; this one carries no
    # sunset detail, so telling the caller to upgrade would be wrong.
    harness.admin.error = (
        grpc.StatusCode.FAILED_PRECONDITION,
        "external_user_needs_customer_network",
    )
    with pytest.raises(errors.MemcoPreconditionFailedError) as caught:
        credentialed.networks.add_member("network-internal", "xuser-a")
    assert not isinstance(caught.value, errors.MemcoSunsetError)
    assert caught.value.message == "external_user_needs_customer_network"
