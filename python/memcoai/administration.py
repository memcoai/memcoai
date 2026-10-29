"""Network and user administration, reached as ``client.networks`` and ``client.users``.

Held apart from the client, which owns the channel, the credential and the
health gate; this module owns only the calls.

Every call here is made under the token a client issues for an API client's
``client_id`` and ``client_secret``, and is bounded by the scopes that API
client was granted, such as ``network-management`` and ``user-management``. The
organization is always the API client's own, so no call names one.

Networks are the memory networks an organization places its people in, and
what scopes the knowledge each of them can find. External users are your own
users, known to Memco by your id for them: they have no sign-in of their own,
and act through API keys or through a memory session opened on their behalf.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

import grpc

from . import _convert, _requests
from .errors import MemcoAlreadyExistsError
from .types import (
    CreatedKey,
    DeletedNetwork,
    ExternalUser,
    ExternalUserKey,
    ExternalUserList,
    GroupList,
    Member,
    MemberList,
    MemberPlacement,
    Network,
    NetworkList,
)

__all__ = [
    "AsyncNetworkOperations",
    "AsyncUserOperations",
    "NetworkOperations",
    "UserOperations",
]


class NetworkOperations:
    """Memory networks, their members and their groups, on a synchronous client.

    Reached as :attr:`~memcoai.Memco.networks`; not constructed directly.

    Example:
        >>> with Memco(client_id="...", client_secret="...") as client:
        ...     root = client.networks.list(parent_id="root", domain="coding").networks[0]
        ...     acme = client.networks.create(name="Acme", parent_ids=[root.id], scope="customer")
    """

    def __init__(self, stub: Any, call: Callable[..., Any]) -> None:
        """Bind the namespace to its client.

        Args:
            stub: The generated service stub.
            call: The owning client's invoker, which applies the deadline and
                the credential, and translates failures into typed exceptions.
        """
        self._stub = stub
        self._call = call

    def list(
        self,
        *,
        name: str | None = None,
        scope: str | None = None,
        owner: str | None = None,
        domain: str | None = None,
        parent_id: str | None = None,
        ids: Iterable[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> NetworkList:
        """List one page of your organization's memory networks, narrowed by the filters given.

        Every filter is optional, and those given must all match.

        Args:
            name: Keeps networks with this name, matched case-insensitively.
            scope: Keeps networks of this scope: ``"internal"``, ``"customer"``,
                or ``"none"`` for networks without one.
            owner: Keeps networks with this owner, matched case-insensitively.
            domain: Keeps networks of this memory domain.
            parent_id: Keeps the children of this network. ``"root"`` keeps the
                root networks, one per memory domain.
            ids: Keeps only these networks. The service bounds how many one call
                may name. An empty collection filters nothing, since on the wire
                it reads as not given.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many networks a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of networks, and how many match in all.

        Raises:
            MemcoInvalidRequestError: If ``ids`` is a single string rather than
                a collection of them.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = client.networks.list(parent_id="root", domain="coding")
            >>> [(network.id, network.name) for network in page.networks]
        """
        request = _requests.list_networks_request(
            name=name,
            scope=scope,
            owner=owner,
            domain=domain,
            parent_id=parent_id,
            ids=ids,
            page=page,
            page_size=page_size,
        )
        return _convert.to_network_list(self._call(self._stub.ListNetworks, request, timeout))

    def create(
        self,
        *,
        name: str,
        parent_id: str | None = None,
        parent_ids: Iterable[str] | None = None,
        domain: str | None = None,
        scope: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        timeout: float | None = None,
    ) -> Network:
        """Create a memory network.

        A network is either a root, placed in a memory domain, or the child of
        other networks, whose domain it takes.

        Args:
            name: The network's name.
            parent_id: Deprecated: use ``parent_ids``, never both. The network to
                create this one under, read as ``parent_ids`` holding that one
                network.
            parent_ids: The networks to create this one under, each named once
                and all in one domain, which the new network takes. Consumed
                exactly once.
                Omitted or empty, the new network is a root.
            domain: The memory domain of a root network. Omitted, your
                organization's default. A child must not name one.
            scope: ``"internal"`` or ``"customer"``, where your organization
                uses scopes. An external user can be placed only in a customer
                network.
            owner: Who the network's knowledge belongs to.
            description: What the network is for.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network as created.

        Raises:
            MemcoAlreadyExistsError: If the name is already taken.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> acme = client.networks.create(name="Acme", parent_ids=["network-root"])
            >>> acme.domain  # taken from its parent
            'coding'
        """
        request = _requests.create_network_request(
            name=name,
            parent_id=parent_id,
            parent_ids=parent_ids,
            domain=domain,
            scope=scope,
            owner=owner,
            description=description,
        )
        return _convert.to_network(self._call(self._stub.CreateNetwork, request, timeout))

    def create_or_get(
        self,
        *,
        name: str,
        parent_id: str | None = None,
        parent_ids: Iterable[str] | None = None,
        domain: str | None = None,
        scope: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        timeout: float | None = None,
    ) -> Network:
        """Create a memory network, or return the one already holding its name.

        Safe when two callers race to create one network: the loser is handed
        the winner's. The existing network is returned only if it has the owner
        asked for (no owner matches a network without one) and, when a domain
        or a scope is given, that domain and scope; a network that merely
        shares the name is never adopted. Names are matched exactly, case
        included.

        Args:
            name: The network's name.
            parent_id: Deprecated: use ``parent_ids``, never both. As for
                :meth:`create`.
            parent_ids: The networks to create this one under, as for
                :meth:`create`.
            domain: The memory domain of a root network. Omitted, your
                organization's default, and an existing network in any domain
                matches.
            scope: ``"internal"`` or ``"customer"``, as for :meth:`create`.
                Omitted, an existing network of any scope matches.
            owner: Who the network's knowledge belongs to.
            description: What the network is for.
            timeout: Deadline in seconds for each call made, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network as created, or as it already was.

        Raises:
            MemcoAlreadyExistsError: If the name is held by a network with
                another owner, domain or scope, or by one that cannot be listed.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> acme = client.networks.create_or_get(
            ...     name="acme", domain="knowledge", scope="customer", owner="acme"
            ... )
        """
        try:
            return self.create(
                name=name,
                parent_id=parent_id,
                parent_ids=parent_ids,
                domain=domain,
                scope=scope,
                owner=owner,
                description=description,
                timeout=timeout,
            )
        except MemcoAlreadyExistsError:
            pass
        # Names are unique across domains, so the holder is listed by name alone.
        seen = page = 0
        while True:
            page += 1
            listing = self.list(name=name, page=page, timeout=timeout)
            existing, seen = _adoptable(
                listing, seen, name=name, owner=owner, domain=domain, scope=scope
            )
            if existing is not None:
                return existing

    def update(
        self,
        network_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        parent_ids: Iterable[str] | None = None,
        scope: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        timeout: float | None = None,
    ) -> Network:
        """Change a network's name, parent, scope, owner or description.

        Only what is passed changes. A field left as ``None`` stays as it is;
        an empty string is sent, and clears the field where the service allows
        it to be cleared.

        Args:
            network_id: The network to change.
            name: The new name.
            parent_id: Deprecated: use ``parent_ids``, never both. The network
                to move this one under, replacing every other parent; ``""``
                makes it a root.
            parent_ids: The network's whole new parent set, replacing the
                current one. Consumed exactly once. Empty makes it a root.
            scope: The new scope, ``"internal"`` or ``"customer"``.
            owner: The new owner.
            description: The new description.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network as it now is.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.networks.update("network-a", name="Acme Corp", description="")
        """
        request = _requests.update_network_request(
            network_id,
            name=name,
            parent_id=parent_id,
            parent_ids=parent_ids,
            scope=scope,
            owner=owner,
            description=description,
        )
        return _convert.to_network(self._call(self._stub.UpdateNetwork, request, timeout))

    def delete(self, network_id: str, *, timeout: float | None = None) -> DeletedNetwork:
        """Delete a network, and everything that hangs off it.

        Cannot be undone. The result reports what the deletion removed with the
        network, so its cost can be seen.

        Args:
            network_id: The network to delete.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network deleted, and how much the cascade removed from each
            table.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> deleted = client.networks.delete("network-a")
            >>> dict(deleted.removed)
            {'memories': 3, 'network_members': 1}
        """
        request = _requests.delete_network_request(network_id)
        return _convert.to_deleted_network(self._call(self._stub.DeleteNetwork, request, timeout))

    def list_members(
        self,
        network_id: str,
        *,
        search: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> MemberList:
        """List one page of the users placed in a network.

        Args:
            network_id: The network whose members to list.
            search: Keeps members matching this text.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many members a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of members, and how many match in all.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = client.networks.list_members("network-a", search="ada")
            >>> [member.email for member in page.members]
        """
        request = _requests.list_network_members_request(
            network_id, search=search, page=page, page_size=page_size
        )
        return _convert.to_member_list(self._call(self._stub.ListNetworkMembers, request, timeout))

    def add_member(
        self, network_id: str, user_id: str, *, force: bool = False, timeout: float | None = None
    ) -> MemberPlacement:
        """Place a user in a network.

        A user holds one network per memory domain. One already placed in
        another network of the same domain is refused, naming that network,
        unless ``force`` is set, which moves them. An external user can be
        placed only in a customer network; any other is refused with
        :class:`~memcoai.errors.MemcoPreconditionFailedError`.

        Args:
            network_id: The network to place the user in.
            user_id: The user to place. For an external user, this is
                :attr:`~memcoai.types.ExternalUser.id`, not your own id for them.
            force: Whether to move a user already placed in another network of
                the same memory domain. Off by default, so moving someone is
                never the accidental outcome.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            Where the user was placed, and the network they were moved out of,
            if any.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``user_id`` is blank.
            MemcoPreconditionFailedError: If the placement is not allowed as it
                stands, such as an external user and a network that is not a
                customer one.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> placed = client.networks.add_member("network-acme", user.id, force=True)
            >>> placed.moved_from  # the network they were moved out of, if any
        """
        request = _requests.add_network_member_request(network_id, user_id, force=force)
        return _convert.to_member_placement(
            self._call(self._stub.AddNetworkMember, request, timeout)
        )

    def remove_member(self, network_id: str, user_id: str, *, timeout: float | None = None) -> None:
        """Take a user out of a network.

        Args:
            network_id: The network to take the user out of.
            user_id: The user to take out.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``user_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.networks.remove_member("network-acme", "user-a")
        """
        request = _requests.remove_network_member_request(network_id, user_id)
        self._call(self._stub.RemoveNetworkMember, request, timeout)

    def list_groups(
        self,
        *,
        name: str | None = None,
        network_id: str | None = None,
        ids: Iterable[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> GroupList:
        """List one page of your organization's identity-provider groups.

        Groups are how an enterprise organization places its people in
        networks, and are available to enterprise organizations only; the
        service refuses this call for any other.

        Args:
            name: Keeps groups with this name, matched case-insensitively.
            network_id: Keeps groups assigned to this network.
            ids: Keeps only these groups. The service bounds how many one call
                may name. An empty collection filters nothing, since on the wire
                it reads as not given.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many groups a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of groups, and how many match in all.

        Raises:
            MemcoInvalidRequestError: If ``ids`` is a single string rather than
                a collection of them.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = client.networks.list_groups(name="Support")
            >>> [(group.id, group.member_count) for group in page.groups]
        """
        request = _requests.list_groups_request(
            name=name, network_id=network_id, ids=ids, page=page, page_size=page_size
        )
        return _convert.to_group_list(self._call(self._stub.ListGroups, request, timeout))

    def list_group_members(
        self, group_id: str, *, timeout: float | None = None
    ) -> tuple[Member, ...]:
        """List the users in an identity-provider group.

        The identity provider manages who is in a group; this only reads it.
        Available to enterprise organizations only.

        Args:
            group_id: The group whose members to list.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The group's members.

        Raises:
            MemcoInvalidRequestError: If ``group_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> [member.email for member in client.networks.list_group_members("group-a")]
        """
        request = _requests.list_group_members_request(group_id)
        return _convert.to_group_members(self._call(self._stub.ListGroupMembers, request, timeout))

    def add_group(
        self,
        network_id: str,
        group_id: str,
        *,
        limit_assignable_roles: Iterable[str] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Assign an identity-provider group to a network, placing its members there.

        A group holds one network per memory domain, so this replaces the
        group's network in this network's domain. A group limited to
        moderation instead keeps its others, and may be bound to several
        networks in one domain. Available to enterprise organizations only.

        Args:
            network_id: The network to assign the group to.
            group_id: The group to assign.
            limit_assignable_roles: The roles the group may give its members on
                every network it is bound to: ``["content_moderator"]`` gives
                moderation only, and adds this network beside the group's
                others. Omitted or empty, the group's limit stays as it is. The
                limit changes only while the group is bound to this network
                alone.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``group_id`` is blank,
                or ``limit_assignable_roles`` is a single string.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.networks.add_group("network-support", "group-a")
            >>> client.networks.add_group(
            ...     "network-review",
            ...     "group-moderators",
            ...     limit_assignable_roles=["content_moderator"],
            ... )
        """
        request = _requests.add_network_group_request(network_id, group_id, limit_assignable_roles)
        self._call(self._stub.AddNetworkGroup, request, timeout)

    def remove_group(self, network_id: str, group_id: str, *, timeout: float | None = None) -> None:
        """Take an identity-provider group out of a network.

        The group's networks in other memory domains are unaffected. Available
        to enterprise organizations only.

        Args:
            network_id: The network to take the group out of.
            group_id: The group to take out.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``group_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.networks.remove_group("network-support", "group-a")
        """
        request = _requests.remove_network_group_request(network_id, group_id)
        self._call(self._stub.RemoveNetworkGroup, request, timeout)


class AsyncNetworkOperations:
    """Memory networks, their members and their groups, on an asyncio client.

    Reached as :attr:`~memcoai.AsyncMemco.networks`; not constructed directly.
    Mirrors :class:`NetworkOperations` method for method.

    Example:
        >>> async with AsyncMemco(client_id="...", client_secret="...") as client:
        ...     acme = await client.networks.create(name="Acme", parent_ids=["network-root"])
    """

    def __init__(self, stub: Any, call: Callable[..., Any]) -> None:
        """Bind the namespace to its client.

        Args:
            stub: The generated service stub.
            call: The owning client's invoker, which applies the deadline and
                the credential, and translates failures into typed exceptions.
        """
        self._stub = stub
        self._call = call

    async def list(
        self,
        *,
        name: str | None = None,
        scope: str | None = None,
        owner: str | None = None,
        domain: str | None = None,
        parent_id: str | None = None,
        ids: Iterable[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> NetworkList:
        """List one page of your organization's memory networks, narrowed by the filters given.

        Every filter is optional, and those given must all match.

        Args:
            name: Keeps networks with this name, matched case-insensitively.
            scope: Keeps networks of this scope: ``"internal"``, ``"customer"``,
                or ``"none"`` for networks without one.
            owner: Keeps networks with this owner, matched case-insensitively.
            domain: Keeps networks of this memory domain.
            parent_id: Keeps the children of this network. ``"root"`` keeps the
                root networks, one per memory domain.
            ids: Keeps only these networks. The service bounds how many one call
                may name. An empty collection filters nothing, since on the wire
                it reads as not given.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many networks a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of networks, and how many match in all.

        Raises:
            MemcoInvalidRequestError: If ``ids`` is a single string rather than
                a collection of them.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = await client.networks.list(parent_id="root", domain="coding")
            >>> [(network.id, network.name) for network in page.networks]
        """
        request = _requests.list_networks_request(
            name=name,
            scope=scope,
            owner=owner,
            domain=domain,
            parent_id=parent_id,
            ids=ids,
            page=page,
            page_size=page_size,
        )
        return _convert.to_network_list(await self._call(self._stub.ListNetworks, request, timeout))

    async def create(
        self,
        *,
        name: str,
        parent_id: str | None = None,
        parent_ids: Iterable[str] | None = None,
        domain: str | None = None,
        scope: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        timeout: float | None = None,
    ) -> Network:
        """Create a memory network.

        A network is either a root, placed in a memory domain, or the child of
        other networks, whose domain it takes.

        Args:
            name: The network's name.
            parent_id: Deprecated: use ``parent_ids``, never both. The network to
                create this one under, read as ``parent_ids`` holding that one
                network.
            parent_ids: The networks to create this one under, each named once
                and all in one domain, which the new network takes. Consumed
                exactly once.
                Omitted or empty, the new network is a root.
            domain: The memory domain of a root network. Omitted, your
                organization's default. A child must not name one.
            scope: ``"internal"`` or ``"customer"``, where your organization
                uses scopes. An external user can be placed only in a customer
                network.
            owner: Who the network's knowledge belongs to.
            description: What the network is for.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network as created.

        Raises:
            MemcoAlreadyExistsError: If the name is already taken.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> acme = await client.networks.create(name="Acme", parent_ids=["network-root"])
            >>> acme.domain  # taken from its parent
            'coding'
        """
        request = _requests.create_network_request(
            name=name,
            parent_id=parent_id,
            parent_ids=parent_ids,
            domain=domain,
            scope=scope,
            owner=owner,
            description=description,
        )
        return _convert.to_network(await self._call(self._stub.CreateNetwork, request, timeout))

    async def create_or_get(
        self,
        *,
        name: str,
        parent_id: str | None = None,
        parent_ids: Iterable[str] | None = None,
        domain: str | None = None,
        scope: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        timeout: float | None = None,
    ) -> Network:
        """Create a memory network, or return the one already holding its name.

        Safe when two callers race to create one network: the loser is handed
        the winner's. The existing network is returned only if it has the owner
        asked for (no owner matches a network without one) and, when a domain
        or a scope is given, that domain and scope; a network that merely
        shares the name is never adopted. Names are matched exactly, case
        included.

        Args:
            name: The network's name.
            parent_id: Deprecated: use ``parent_ids``, never both. As for
                :meth:`create`.
            parent_ids: The networks to create this one under, as for
                :meth:`create`.
            domain: The memory domain of a root network. Omitted, your
                organization's default, and an existing network in any domain
                matches.
            scope: ``"internal"`` or ``"customer"``, as for :meth:`create`.
                Omitted, an existing network of any scope matches.
            owner: Who the network's knowledge belongs to.
            description: What the network is for.
            timeout: Deadline in seconds for each call made, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network as created, or as it already was.

        Raises:
            MemcoAlreadyExistsError: If the name is held by a network with
                another owner, domain or scope, or by one that cannot be listed.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> acme = await client.networks.create_or_get(
            ...     name="acme", domain="knowledge", scope="customer", owner="acme"
            ... )
        """
        try:
            return await self.create(
                name=name,
                parent_id=parent_id,
                parent_ids=parent_ids,
                domain=domain,
                scope=scope,
                owner=owner,
                description=description,
                timeout=timeout,
            )
        except MemcoAlreadyExistsError:
            pass
        # Names are unique across domains, so the holder is listed by name alone.
        seen = page = 0
        while True:
            page += 1
            listing = await self.list(name=name, page=page, timeout=timeout)
            existing, seen = _adoptable(
                listing, seen, name=name, owner=owner, domain=domain, scope=scope
            )
            if existing is not None:
                return existing

    async def update(
        self,
        network_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        parent_ids: Iterable[str] | None = None,
        scope: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        timeout: float | None = None,
    ) -> Network:
        """Change a network's name, parent, scope, owner or description.

        Only what is passed changes. A field left as ``None`` stays as it is;
        an empty string is sent, and clears the field where the service allows
        it to be cleared.

        Args:
            network_id: The network to change.
            name: The new name.
            parent_id: Deprecated: use ``parent_ids``, never both. The network
                to move this one under, replacing every other parent; ``""``
                makes it a root.
            parent_ids: The network's whole new parent set, replacing the
                current one. Consumed exactly once. Empty makes it a root.
            scope: The new scope, ``"internal"`` or ``"customer"``.
            owner: The new owner.
            description: The new description.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network as it now is.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.networks.update("network-a", name="Acme Corp", description="")
        """
        request = _requests.update_network_request(
            network_id,
            name=name,
            parent_id=parent_id,
            parent_ids=parent_ids,
            scope=scope,
            owner=owner,
            description=description,
        )
        return _convert.to_network(await self._call(self._stub.UpdateNetwork, request, timeout))

    async def delete(self, network_id: str, *, timeout: float | None = None) -> DeletedNetwork:
        """Delete a network, and everything that hangs off it.

        Cannot be undone. The result reports what the deletion removed with the
        network, so its cost can be seen.

        Args:
            network_id: The network to delete.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The network deleted, and how much the cascade removed from each
            table.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> deleted = await client.networks.delete("network-a")
            >>> dict(deleted.removed)
            {'memories': 3, 'network_members': 1}
        """
        request = _requests.delete_network_request(network_id)
        return _convert.to_deleted_network(
            await self._call(self._stub.DeleteNetwork, request, timeout)
        )

    async def list_members(
        self,
        network_id: str,
        *,
        search: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> MemberList:
        """List one page of the users placed in a network.

        Args:
            network_id: The network whose members to list.
            search: Keeps members matching this text.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many members a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of members, and how many match in all.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = await client.networks.list_members("network-a", search="ada")
            >>> [member.email for member in page.members]
        """
        request = _requests.list_network_members_request(
            network_id, search=search, page=page, page_size=page_size
        )
        return _convert.to_member_list(
            await self._call(self._stub.ListNetworkMembers, request, timeout)
        )

    async def add_member(
        self, network_id: str, user_id: str, *, force: bool = False, timeout: float | None = None
    ) -> MemberPlacement:
        """Place a user in a network.

        A user holds one network per memory domain. One already placed in
        another network of the same domain is refused, naming that network,
        unless ``force`` is set, which moves them. An external user can be
        placed only in a customer network; any other is refused with
        :class:`~memcoai.errors.MemcoPreconditionFailedError`.

        Args:
            network_id: The network to place the user in.
            user_id: The user to place. For an external user, this is
                :attr:`~memcoai.types.ExternalUser.id`, not your own id for them.
            force: Whether to move a user already placed in another network of
                the same memory domain. Off by default, so moving someone is
                never the accidental outcome.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            Where the user was placed, and the network they were moved out of,
            if any.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``user_id`` is blank.
            MemcoPreconditionFailedError: If the placement is not allowed as it
                stands, such as an external user and a network that is not a
                customer one.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> placed = await client.networks.add_member("network-acme", user.id, force=True)
            >>> placed.moved_from  # the network they were moved out of, if any
        """
        request = _requests.add_network_member_request(network_id, user_id, force=force)
        return _convert.to_member_placement(
            await self._call(self._stub.AddNetworkMember, request, timeout)
        )

    async def remove_member(
        self, network_id: str, user_id: str, *, timeout: float | None = None
    ) -> None:
        """Take a user out of a network.

        Args:
            network_id: The network to take the user out of.
            user_id: The user to take out.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``user_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.networks.remove_member("network-acme", "user-a")
        """
        request = _requests.remove_network_member_request(network_id, user_id)
        await self._call(self._stub.RemoveNetworkMember, request, timeout)

    async def list_groups(
        self,
        *,
        name: str | None = None,
        network_id: str | None = None,
        ids: Iterable[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> GroupList:
        """List one page of your organization's identity-provider groups.

        Groups are how an enterprise organization places its people in
        networks, and are available to enterprise organizations only; the
        service refuses this call for any other.

        Args:
            name: Keeps groups with this name, matched case-insensitively.
            network_id: Keeps groups assigned to this network.
            ids: Keeps only these groups. The service bounds how many one call
                may name. An empty collection filters nothing, since on the wire
                it reads as not given.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many groups a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of groups, and how many match in all.

        Raises:
            MemcoInvalidRequestError: If ``ids`` is a single string rather than
                a collection of them.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = await client.networks.list_groups(name="Support")
            >>> [(group.id, group.member_count) for group in page.groups]
        """
        request = _requests.list_groups_request(
            name=name, network_id=network_id, ids=ids, page=page, page_size=page_size
        )
        return _convert.to_group_list(await self._call(self._stub.ListGroups, request, timeout))

    async def list_group_members(
        self, group_id: str, *, timeout: float | None = None
    ) -> tuple[Member, ...]:
        """List the users in an identity-provider group.

        The identity provider manages who is in a group; this only reads it.
        Available to enterprise organizations only.

        Args:
            group_id: The group whose members to list.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The group's members.

        Raises:
            MemcoInvalidRequestError: If ``group_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> members = await client.networks.list_group_members("group-a")
            >>> [member.email for member in members]
        """
        request = _requests.list_group_members_request(group_id)
        return _convert.to_group_members(
            await self._call(self._stub.ListGroupMembers, request, timeout)
        )

    async def add_group(
        self,
        network_id: str,
        group_id: str,
        *,
        limit_assignable_roles: Iterable[str] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Assign an identity-provider group to a network, placing its members there.

        A group holds one network per memory domain, so this replaces the
        group's network in this network's domain. A group limited to
        moderation instead keeps its others, and may be bound to several
        networks in one domain. Available to enterprise organizations only.

        Args:
            network_id: The network to assign the group to.
            group_id: The group to assign.
            limit_assignable_roles: The roles the group may give its members on
                every network it is bound to: ``["content_moderator"]`` gives
                moderation only, and adds this network beside the group's
                others. Omitted or empty, the group's limit stays as it is. The
                limit changes only while the group is bound to this network
                alone.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``group_id`` is blank,
                or ``limit_assignable_roles`` is a single string.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.networks.add_group("network-support", "group-a")
            >>> await client.networks.add_group(
            ...     "network-review",
            ...     "group-moderators",
            ...     limit_assignable_roles=["content_moderator"],
            ... )
        """
        request = _requests.add_network_group_request(network_id, group_id, limit_assignable_roles)
        await self._call(self._stub.AddNetworkGroup, request, timeout)

    async def remove_group(
        self, network_id: str, group_id: str, *, timeout: float | None = None
    ) -> None:
        """Take an identity-provider group out of a network.

        The group's networks in other memory domains are unaffected. Available
        to enterprise organizations only.

        Args:
            network_id: The network to take the group out of.
            group_id: The group to take out.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``network_id`` or ``group_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.networks.remove_group("network-support", "group-a")
        """
        request = _requests.remove_network_group_request(network_id, group_id)
        await self._call(self._stub.RemoveNetworkGroup, request, timeout)


class UserOperations:
    """External users and their API keys, on a synchronous client.

    Reached as :attr:`~memcoai.Memco.users`; not constructed directly. Every
    method names a user by your own id for them, the ``external_id`` they were
    created with.

    Example:
        >>> with Memco(client_id="...", client_secret="...") as client:
        ...     user = client.users.create("customer-42", roles=["reader", "creator"])
    """

    def __init__(self, stub: Any, call: Callable[..., Any]) -> None:
        """Bind the namespace to its client.

        Args:
            stub: The generated service stub.
            call: The owning client's invoker, which applies the deadline and
                the credential, and translates failures into typed exceptions.
        """
        self._stub = stub
        self._call = call

    def list(
        self,
        *,
        search: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> ExternalUserList:
        """List one page of your organization's external users.

        Args:
            search: Keeps users whose external id, name or email matches this
                text, case-insensitively.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many users a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of users, and how many match in all.

        Raises:
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = client.users.list(search="acme")
            >>> [user.external_id for user in page.external_users]
        """
        request = _requests.list_external_users_request(
            search=search, page=page, page_size=page_size
        )
        return _convert.to_external_user_list(
            self._call(self._stub.ListExternalUsers, request, timeout)
        )

    def get(self, external_id: str, *, timeout: float | None = None) -> ExternalUser:
        """Fetch one external user, by your own id for them.

        Args:
            external_id: Your id for the user.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank.
            MemcoNotFoundError: If you have no user with that id.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.users.get("customer-42").roles
            ('reader', 'creator')
        """
        request = _requests.get_external_user_request(external_id)
        return _convert.to_external_user(self._call(self._stub.GetExternalUser, request, timeout))

    def create(
        self,
        external_id: str,
        *,
        roles: Iterable[str],
        name: str | None = None,
        email: str | None = None,
        timeout: float | None = None,
    ) -> ExternalUser:
        """Create an external user: one of your own users, known to Memco by your id for them.

        The user has no sign-in of its own, and never holds ``admin``. It starts
        in no network: place it in a customer network with
        :meth:`NetworkOperations.add_member`, passing the created user's
        :attr:`~memcoai.types.ExternalUser.id`.

        Args:
            external_id: Your id for the user, unique within your organization.
                It cannot be changed later.
            roles: The content roles the user holds, from ``"reader"``,
                ``"creator"`` and ``"auditor"``. At least one.
            name: The user's name.
            email: The user's email address.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user as created.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or ``roles``
                is a single string or names no role.
            MemcoAlreadyExistsError: If you already have a user with that id.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> user = client.users.create("customer-42", roles=["reader", "creator"], name="Ada")
        """
        request = _requests.create_external_user_request(
            external_id, roles=roles, name=name, email=email
        )
        return _convert.to_external_user(
            self._call(self._stub.CreateExternalUser, request, timeout)
        )

    def create_or_get(
        self,
        external_id: str,
        *,
        roles: Iterable[str],
        name: str | None = None,
        email: str | None = None,
        timeout: float | None = None,
    ) -> ExternalUser:
        """Create an external user, or return the one already holding its id.

        Safe when two callers race to create one user: the loser is handed the
        winner's. The existing user is returned as it is; its roles, name and
        email are not compared with the arguments or changed.

        Args:
            external_id: Your id for the user, unique within your organization.
            roles: The content roles the user holds, as for :meth:`create`.
            name: The user's name.
            email: The user's email address.
            timeout: Deadline in seconds for each call made, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user as created, or as it already was.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or ``roles``
                is a single string or names no role.
            MemcoNotFoundError: If the user that held the id was deleted before
                it could be fetched.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> user = client.users.create_or_get("customer-42", roles=["reader", "creator"])
        """
        try:
            return self.create(external_id, roles=roles, name=name, email=email, timeout=timeout)
        except MemcoAlreadyExistsError:
            return self.get(external_id, timeout=timeout)

    def update(
        self,
        external_id: str,
        *,
        name: str | None = None,
        email: str | None = None,
        roles: Iterable[str] | None = None,
        timeout: float | None = None,
    ) -> ExternalUser:
        """Change an external user's name, email or roles.

        Only what is passed changes. A field left as ``None`` stays as it is;
        an empty name or email is sent, and clears it. The external id itself
        cannot be changed.

        Args:
            external_id: Your id for the user to change.
            name: The new name.
            email: The new email address.
            roles: The roles to replace the user's with. At least one: to leave
                them as they are, omit this.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user as it now is.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or ``roles``
                is given as a single string or naming no role.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.users.update("customer-42", roles=["reader"])
        """
        request = _requests.update_external_user_request(
            external_id, name=name, email=email, roles=roles
        )
        return _convert.to_external_user(
            self._call(self._stub.UpdateExternalUser, request, timeout)
        )

    def delete(self, external_id: str, *, timeout: float | None = None) -> None:
        """Delete an external user, and every API key it holds.

        Args:
            external_id: Your id for the user to delete.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.users.delete("customer-42")
        """
        request = _requests.delete_external_user_request(external_id)
        self._call(self._stub.DeleteExternalUser, request, timeout)

    def list_keys(
        self, external_id: str, *, timeout: float | None = None
    ) -> tuple[ExternalUserKey, ...]:
        """List an external user's API keys, without their values.

        Args:
            external_id: Your id for the user whose keys to list.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user's keys. A value is shown only once, when its key is
            created, so none is here.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> [(key.name, key.valid_until) for key in client.users.list_keys("customer-42")]
        """
        request = _requests.list_external_user_keys_request(external_id)
        return _convert.to_external_user_keys(
            self._call(self._stub.ListExternalUserKeys, request, timeout)
        )

    def create_key(
        self,
        external_id: str,
        *,
        preset: str,
        name: str | None = None,
        valid_until: datetime | None = None,
        timeout: float | None = None,
    ) -> CreatedKey:
        """Create an API key acting as an external user.

        The key's value is returned here and never again, so store it now.

        Args:
            external_id: Your id for the user the key acts as.
            preset: The kind of key: ``"mcp_rw"``, ``"mcp_ro"`` or ``"audit"``.
                It must be within the user's roles.
            name: A name to recognise the key by.
            valid_until: When the key expires, as a timezone-aware datetime, at
                most three months ahead. Omitted, three months.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The key's description, and its value.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or
                ``valid_until`` carries no time zone.
            MemcoInternalError: If the service returns a value without the key
                it belongs to.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> created = client.users.create_key("customer-42", preset="mcp_ro", name="ci")
            >>> store_secret(created.value)  # shown only this once
        """
        request = _requests.create_external_user_key_request(
            external_id, preset=preset, name=name, valid_until=valid_until
        )
        return _convert.to_created_key(
            self._call(self._stub.CreateExternalUserKey, request, timeout)
        )

    def delete_key(self, external_id: str, key_id: str, *, timeout: float | None = None) -> None:
        """Revoke one of an external user's API keys.

        Args:
            external_id: Your id for the user the key belongs to.
            key_id: The key to revoke, as :attr:`~memcoai.types.ExternalUserKey.id`
                gives it.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` or ``key_id`` is blank.
            MemcoNotFoundError: If the user holds no such key.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> client.users.delete_key("customer-42", created.key.id)
        """
        request = _requests.delete_external_user_key_request(external_id, key_id)
        self._call(self._stub.DeleteExternalUserKey, request, timeout)


class AsyncUserOperations:
    """External users and their API keys, on an asyncio client.

    Reached as :attr:`~memcoai.AsyncMemco.users`; not constructed directly.
    Mirrors :class:`UserOperations` method for method.

    Example:
        >>> async with AsyncMemco(client_id="...", client_secret="...") as client:
        ...     user = await client.users.create("customer-42", roles=["reader", "creator"])
    """

    def __init__(self, stub: Any, call: Callable[..., Any]) -> None:
        """Bind the namespace to its client.

        Args:
            stub: The generated service stub.
            call: The owning client's invoker, which applies the deadline and
                the credential, and translates failures into typed exceptions.
        """
        self._stub = stub
        self._call = call

    async def list(
        self,
        *,
        search: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        timeout: float | None = None,
    ) -> ExternalUserList:
        """List one page of your organization's external users.

        Args:
            search: Keeps users whose external id, name or email matches this
                text, case-insensitively.
            page: The page to return, counting from 1. Omitted, the first.
            page_size: How many users a page holds. Omitted, the service's
                default.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The page of users, and how many match in all.

        Raises:
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> page = await client.users.list(search="acme")
            >>> [user.external_id for user in page.external_users]
        """
        request = _requests.list_external_users_request(
            search=search, page=page, page_size=page_size
        )
        return _convert.to_external_user_list(
            await self._call(self._stub.ListExternalUsers, request, timeout)
        )

    async def get(self, external_id: str, *, timeout: float | None = None) -> ExternalUser:
        """Fetch one external user, by your own id for them.

        Args:
            external_id: Your id for the user.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank.
            MemcoNotFoundError: If you have no user with that id.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> (await client.users.get("customer-42")).roles
            ('reader', 'creator')
        """
        request = _requests.get_external_user_request(external_id)
        return _convert.to_external_user(
            await self._call(self._stub.GetExternalUser, request, timeout)
        )

    async def create(
        self,
        external_id: str,
        *,
        roles: Iterable[str],
        name: str | None = None,
        email: str | None = None,
        timeout: float | None = None,
    ) -> ExternalUser:
        """Create an external user: one of your own users, known to Memco by your id for them.

        The user has no sign-in of its own, and never holds ``admin``. It starts
        in no network: place it in a customer network with
        :meth:`AsyncNetworkOperations.add_member`, passing the created user's
        :attr:`~memcoai.types.ExternalUser.id`.

        Args:
            external_id: Your id for the user, unique within your organization.
                It cannot be changed later.
            roles: The content roles the user holds, from ``"reader"``,
                ``"creator"`` and ``"auditor"``. At least one.
            name: The user's name.
            email: The user's email address.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user as created.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or ``roles``
                is a single string or names no role.
            MemcoAlreadyExistsError: If you already have a user with that id.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> user = await client.users.create("customer-42", roles=["reader"], name="Ada")
        """
        request = _requests.create_external_user_request(
            external_id, roles=roles, name=name, email=email
        )
        return _convert.to_external_user(
            await self._call(self._stub.CreateExternalUser, request, timeout)
        )

    async def create_or_get(
        self,
        external_id: str,
        *,
        roles: Iterable[str],
        name: str | None = None,
        email: str | None = None,
        timeout: float | None = None,
    ) -> ExternalUser:
        """Create an external user, or return the one already holding its id.

        Safe when two callers race to create one user: the loser is handed the
        winner's. The existing user is returned as it is; its roles, name and
        email are not compared with the arguments or changed.

        Args:
            external_id: Your id for the user, unique within your organization.
            roles: The content roles the user holds, as for :meth:`create`.
            name: The user's name.
            email: The user's email address.
            timeout: Deadline in seconds for each call made, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user as created, or as it already was.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or ``roles``
                is a single string or names no role.
            MemcoNotFoundError: If the user that held the id was deleted before
                it could be fetched.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> user = await client.users.create_or_get(
            ...     "customer-42", roles=["reader", "creator"]
            ... )
        """
        try:
            return await self.create(
                external_id, roles=roles, name=name, email=email, timeout=timeout
            )
        except MemcoAlreadyExistsError:
            return await self.get(external_id, timeout=timeout)

    async def update(
        self,
        external_id: str,
        *,
        name: str | None = None,
        email: str | None = None,
        roles: Iterable[str] | None = None,
        timeout: float | None = None,
    ) -> ExternalUser:
        """Change an external user's name, email or roles.

        Only what is passed changes. A field left as ``None`` stays as it is;
        an empty name or email is sent, and clears it. The external id itself
        cannot be changed.

        Args:
            external_id: Your id for the user to change.
            name: The new name.
            email: The new email address.
            roles: The roles to replace the user's with. At least one: to leave
                them as they are, omit this.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user as it now is.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or ``roles``
                is given as a single string or naming no role.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.users.update("customer-42", roles=["reader"])
        """
        request = _requests.update_external_user_request(
            external_id, name=name, email=email, roles=roles
        )
        return _convert.to_external_user(
            await self._call(self._stub.UpdateExternalUser, request, timeout)
        )

    async def delete(self, external_id: str, *, timeout: float | None = None) -> None:
        """Delete an external user, and every API key it holds.

        Args:
            external_id: Your id for the user to delete.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.users.delete("customer-42")
        """
        request = _requests.delete_external_user_request(external_id)
        await self._call(self._stub.DeleteExternalUser, request, timeout)

    async def list_keys(
        self, external_id: str, *, timeout: float | None = None
    ) -> tuple[ExternalUserKey, ...]:
        """List an external user's API keys, without their values.

        Args:
            external_id: Your id for the user whose keys to list.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The user's keys. A value is shown only once, when its key is
            created, so none is here.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> keys = await client.users.list_keys("customer-42")
            >>> [(key.name, key.valid_until) for key in keys]
        """
        request = _requests.list_external_user_keys_request(external_id)
        return _convert.to_external_user_keys(
            await self._call(self._stub.ListExternalUserKeys, request, timeout)
        )

    async def create_key(
        self,
        external_id: str,
        *,
        preset: str,
        name: str | None = None,
        valid_until: datetime | None = None,
        timeout: float | None = None,
    ) -> CreatedKey:
        """Create an API key acting as an external user.

        The key's value is returned here and never again, so store it now.

        Args:
            external_id: Your id for the user the key acts as.
            preset: The kind of key: ``"mcp_rw"``, ``"mcp_ro"`` or ``"audit"``.
                It must be within the user's roles.
            name: A name to recognise the key by.
            valid_until: When the key expires, as a timezone-aware datetime, at
                most three months ahead. Omitted, three months.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Returns:
            The key's description, and its value.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` is blank, or
                ``valid_until`` carries no time zone.
            MemcoInternalError: If the service returns a value without the key
                it belongs to.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> created = await client.users.create_key("customer-42", preset="mcp_ro")
            >>> store_secret(created.value)  # shown only this once
        """
        request = _requests.create_external_user_key_request(
            external_id, preset=preset, name=name, valid_until=valid_until
        )
        return _convert.to_created_key(
            await self._call(self._stub.CreateExternalUserKey, request, timeout)
        )

    async def delete_key(
        self, external_id: str, key_id: str, *, timeout: float | None = None
    ) -> None:
        """Revoke one of an external user's API keys.

        Args:
            external_id: Your id for the user the key belongs to.
            key_id: The key to revoke, as :attr:`~memcoai.types.ExternalUserKey.id`
                gives it.
            timeout: Deadline in seconds for the whole call, including any wait
                for a credential. Defaults to the client's.

        Raises:
            MemcoInvalidRequestError: If ``external_id`` or ``key_id`` is blank.
            MemcoNotFoundError: If the user holds no such key.
            MemcoAPIError: If the service returns an error status.

        Example:
            >>> await client.users.delete_key("customer-42", created.key.id)
        """
        request = _requests.delete_external_user_key_request(external_id, key_id)
        await self._call(self._stub.DeleteExternalUserKey, request, timeout)


def _adoptable(
    listing: NetworkList,
    seen: int,
    *,
    name: str,
    owner: str | None,
    domain: str | None,
    scope: str | None,
) -> tuple[Network | None, int]:
    """Read one page of the listing ``create_or_get`` walks for a taken name.

    The service's name filter is a case-insensitive substring match that also
    lists the matches' relatives, so the exact name is picked out here, on
    whichever page it falls.

    Args:
        listing: One page of a listing filtered by ``name``.
        seen: How many networks the earlier pages held.
        name: The name asked for, matched exactly, case included.
        owner: The owner asked for; ``None`` matches a network without one.
        domain: The domain asked for, or ``None`` to match any.
        scope: The scope asked for, or ``None`` to match any.

    Returns:
        The network holding the name, or ``None`` to read the next page; and
        how many networks the pages read so far held.

    Raises:
        MemcoAlreadyExistsError: If the network holding the name has another
            owner, domain or scope, or the listing ends without it.
    """
    existing = next((each for each in listing.networks if each.name == name), None)
    if existing is not None:
        # The service stores the owner trimmed.
        if (existing.owner or "") != (owner or "").strip():
            raise _taken(
                name,
                "a network without an owner"
                if existing.owner is None
                else f"owner {existing.owner!r}",
            )
        if domain and existing.domain != domain:
            raise _taken(name, f"domain {existing.domain!r}")
        if scope and existing.scope != scope:
            raise _taken(name, f"scope {existing.scope!r}")
        return existing, seen
    seen += len(listing.networks)
    if not listing.networks or seen >= listing.total_count:
        raise _taken(name, "a network that cannot be listed")
    return None, seen


def _taken(name: str, holder: str) -> MemcoAlreadyExistsError:
    """Build the refusal ``create_or_get`` raises for a name it will not adopt.

    Args:
        name: The name asked for.
        holder: What holds it instead.

    Returns:
        The error, as the service's own refusal of the create would read.
    """
    return MemcoAlreadyExistsError(
        grpc.StatusCode.ALREADY_EXISTS, f"network name {name!r} is held by {holder}"
    )
