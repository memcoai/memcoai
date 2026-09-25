"""Network and user administration against the real server, as an API client.

The story follows the end-to-end script the service's own team runs against
AdminService — a customer network under a root, its groups, an external user,
that user's keys, and where the user may be placed — split by concern, so a
failure names the part that broke rather than everything after it.

Everything is created fresh, under names this run alone carries, and removed
again: each test removes what it made and asserts that it went, and the
`customer_networks` and `external_users` fixtures remove whatever a failing test
left behind.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from memcoai import Memco
from memcoai.errors import (
    MemcoAlreadyExistsError,
    MemcoExternalUserNeedsCustomerNetworkError,
    MemcoInvalidRequestError,
    MemcoNotFoundError,
    MemcoPermissionError,
    MemcoPreconditionFailedError,
    MemcoUserAlreadyAssignedNetworkError,
)
from memcoai.types import ExternalUser, Network

# A key created without an expiry is given three months, which the calendar
# makes anything from 89 to 92 days; the margin either side absorbs the clock
# and the service's rounding.
KEY_LIFETIME_FLOOR = timedelta(days=88)
KEY_LIFETIME_CEILING = timedelta(days=93)


def test_a_customer_network_is_created_changed_found_and_deleted(
    admin: Memco, root_network: Network, customer_networks: Callable[[], Network]
) -> None:
    """Create a customer network under a root, change it, find it, and delete it."""
    roots = admin.networks.list(parent_id="root", domain=root_network.domain).networks
    assert root_network.id in {root.id for root in roots}
    assert {root.domain for root in roots} == {root_network.domain}
    # Pages count from 1: asking for page 1 returns what asking for no page
    # does, where a count from 0 would return the second page instead.
    first = admin.networks.list(parent_id="root", page_size=1).networks
    assert admin.networks.list(parent_id="root", page=1, page_size=1).networks == first

    network = customer_networks()
    print(f"\ncreated network {network.id} under {root_network.id} in {network.domain}")
    assert (network.parent_id, network.domain, network.scope) == (
        root_network.id,
        root_network.domain,
        "customer",
    )

    description = "A network the Python SDK system test has changed."
    changed = admin.networks.update(network.id, description=description)
    assert changed.description == description
    assert changed.name == network.name, "a field left as None was changed"

    found = admin.networks.list(ids=[network.id]).networks
    assert [(each.id, each.description) for each in found] == [(network.id, description)]

    with pytest.raises(MemcoInvalidRequestError):
        admin.networks.update("", description=description)

    assert admin.networks.delete(network.id).id == network.id
    assert not admin.networks.list(ids=[network.id]).networks


def test_groups_are_refused_outside_an_enterprise_and_assigned_within_one(
    admin: Memco, customer_networks: Callable[[], Network]
) -> None:
    """List, read and assign identity-provider groups, or be refused all of it.

    Groups belong to enterprise organisations only, and the service refuses
    the whole surface to any other rather than listing none, so either outcome
    passes; what fails is anything in between.
    """
    network = customer_networks()
    try:
        groups = admin.networks.list_groups().groups
    except MemcoPermissionError:
        print("\ngroups are refused: this is not an enterprise organisation")
        with pytest.raises(MemcoPermissionError):
            admin.networks.add_group(network.id, "group-that-is-never-reached")
        return
    # Assigning replaces a group's network in the network's domain, and a
    # group reports one network id however many domains it is placed in, so a
    # real group's placement could not be put back for certain. Only a group
    # placed nowhere is used: taking it out again restores exactly that.
    unplaced = [group for group in groups if group.memory_network_id is None]
    if not unplaced:
        pytest.skip("the organisation has no identity-provider group placed in no network")
    group = unplaced[0]
    members = admin.networks.list_group_members(group.id)
    print(f"\nassigning group {group.id}, of {len(members)} members, to {network.id}")

    admin.networks.add_group(network.id, group.id)
    try:
        assigned = admin.networks.list_groups(network_id=network.id).groups
        assert group.id in {each.id for each in assigned}
    finally:
        admin.networks.remove_group(network.id, group.id)
    remaining = admin.networks.list_groups(network_id=network.id).groups
    assert group.id not in {each.id for each in remaining}
    (restored,) = admin.networks.list_groups(ids=[group.id]).groups
    assert restored.memory_network_id is None


def test_an_external_user_is_created_found_changed_and_deleted(
    admin: Memco, external_users: Callable[..., ExternalUser]
) -> None:
    """Create an external user, refuse a duplicate and an admin, then read, rename and delete."""
    user = external_users()
    print(f"\ncreated external user {user.external_id} as {user.id}")
    # A creator can read what it can write, and the service says so.
    assert set(user.roles) == {"creator", "reader"}

    with pytest.raises(MemcoAlreadyExistsError):
        admin.users.create(user.external_id, roles=["creator"])
    # An external user never holds admin.
    with pytest.raises(MemcoInvalidRequestError):
        external_users(roles=["admin"])

    fetched = admin.users.get(user.external_id)
    assert (fetched.id, set(fetched.roles)) == (user.id, set(user.roles))
    listed = admin.users.list(search=user.external_id).external_users
    assert [each.id for each in listed] == [user.id]

    name = "Python SDK system test"
    renamed = admin.users.update(user.external_id, name=name)
    assert renamed.name == name
    assert set(renamed.roles) == set(user.roles), "roles left as None were changed"
    assert admin.users.get(user.external_id).name == name

    admin.users.delete(user.external_id)
    with pytest.raises(MemcoNotFoundError):
        admin.users.get(user.external_id)


def test_an_external_users_key_is_shown_once_listed_and_revoked(
    admin: Memco, external_users: Callable[..., ExternalUser]
) -> None:
    """Create a key, refuse one beyond the user's roles, list it, and revoke it twice."""
    user = external_users()
    created = admin.users.create_key(user.external_id, preset="mcp_ro", name="python-systemtest")
    # Computed first: pytest explains a failed assertion by printing its
    # operands, and the value is a live key.
    prefixed = created.value.startswith(created.key.value_prefix)
    assert prefixed, "the key's value does not start with its listed prefix"
    assert created.key.valid_until is not None
    left = created.key.valid_until - datetime.now(timezone.utc)
    assert KEY_LIFETIME_FLOOR <= left <= KEY_LIFETIME_CEILING, f"a key valid for {left}"

    # The audit stream is for auditors, which a creator is not.
    with pytest.raises(MemcoInvalidRequestError):
        admin.users.create_key(user.external_id, preset="audit")

    assert created.key.id in {key.id for key in admin.users.list_keys(user.external_id)}
    admin.users.delete_key(user.external_id, created.key.id)
    assert created.key.id not in {key.id for key in admin.users.list_keys(user.external_id)}
    with pytest.raises(MemcoNotFoundError):
        admin.users.delete_key(user.external_id, created.key.id)


def test_an_external_user_is_placed_only_in_a_customer_network(
    admin: Memco,
    root_network: Network,
    customer_networks: Callable[[], Network],
    external_users: Callable[..., ExternalUser],
) -> None:
    """Refuse the root, place the user in a customer network, and keep that network one."""
    network = customer_networks()
    user = external_users()

    with pytest.raises(MemcoExternalUserNeedsCustomerNetworkError) as refused:
        admin.networks.add_member(root_network.id, user.id)
    assert refused.value.required_network_scope == "customer"

    placed = admin.networks.add_member(network.id, user.id)
    assert (placed.network_id, placed.user_id, placed.moved_from) == (network.id, user.id, None)
    members = admin.networks.list_members(network.id).members
    assert user.id in {member.user_id for member in members}

    # A second network of the same domain refuses them, naming where they are.
    with pytest.raises(MemcoUserAlreadyAssignedNetworkError) as moving:
        admin.networks.add_member(customer_networks().id, user.id)
    assert (moving.value.current_network_id, moving.value.current_network_name) == (
        network.id,
        network.name,
    )

    # A network holding an external user cannot stop being a customer one.
    with pytest.raises(MemcoPreconditionFailedError):
        admin.networks.update(network.id, scope="internal")

    admin.networks.remove_member(network.id, user.id)
    members = admin.networks.list_members(network.id).members
    assert user.id not in {member.user_id for member in members}
