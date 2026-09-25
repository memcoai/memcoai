"""Map a customer's organisation into Memco: a company network, projects under it, its people.

A consulting company becomes a customer network of its own, and each of its
client projects a customer network under it. Its engineers become external
users -- no sign-in of their own; your API client acts for them -- and each is
placed in exactly one network per domain:

* placed in a project, an engineer sees that project's knowledge and the
  company's, and never another project's;
* placed in the company network, they see only what the company shares.

Moving an engineer to another project is refused unless the move is asked for.
Everything created here is removed again at the end; a real integration keeps
it.

Run it with::

    export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
    python examples/map_your_users.py
"""

import uuid

from memcoai import Memco
from memcoai.errors import MemcoAlreadyExistsError, MemcoUserAlreadyAssignedNetworkError
from memcoai.types import ExternalUser, Network

DOMAIN = "coding"
COMPANY = "Acme Consulting"
PROJECTS = {"billing": "Billing platform", "mobile": "Mobile app"}

# Each engineer by your own id for them -- Memco keeps it as their external id,
# so you never need to store an id of Memco's -- with where they work.
ENGINEERS = {
    "maria": ("Maria Lind", "company"),  # the practice lead, across every project
    "alice": ("Alice Andersson", "billing"),
    "bob": ("Bob Berg", "billing"),
    "carol": ("Carol Chen", "mobile"),
    "dave": ("Dave Dahl", "mobile"),
}


def main() -> None:
    """Run the example."""
    run = uuid.uuid4().hex[:8]  # keeps this run's names apart from any other run's

    # Reads MEMCO_CLIENT_ID and MEMCO_CLIENT_SECRET, exchanges them for a token,
    # and renews that token by itself for as long as the client is open.
    with Memco() as client:
        # The company network hangs under the domain's root network, and each
        # project under the company, taking its domain from it.
        root = client.networks.list(parent_id="root", domain=DOMAIN).networks[0]
        networks: dict[str, Network] = {}
        users: dict[str, ExternalUser] = {}
        try:
            networks["company"] = client.networks.create(
                name=f"{COMPANY} ({run})",
                parent_id=root.id,
                scope="customer",
                description="Practice that applies to every client project",
            )
            for key, title in PROJECTS.items():
                networks[key] = client.networks.create(
                    name=f"{COMPANY} / {title} ({run})",
                    parent_id=networks["company"].id,
                    scope="customer",
                )

            for handle, (name, where) in ENGINEERS.items():
                external_id = f"acme-{handle}-{run}"
                try:
                    user = client.users.create(external_id, name=name, roles=["creator"])
                except MemcoAlreadyExistsError:
                    # A real integration's ids carry no run suffix, so mapping
                    # someone a second time finds them already there.
                    user = client.users.get(external_id)
                users[handle] = user
                client.networks.add_member(networks[where].id, user.id)

            for network in networks.values():
                members = client.networks.list_members(network.id).members
                print(f"{network.name}: {', '.join(member.name for member in members)}")

            # Bob moves from billing to the mobile app. Placing him again is
            # refused while he is in another network of the same domain, as
            # USER_ALREADY_ASSIGNED_NETWORK, naming the network he is in.
            bob = users["bob"]
            try:
                client.networks.add_member(networks["mobile"].id, bob.id)
            except MemcoUserAlreadyAssignedNetworkError as refused:
                print(
                    f"\nrefused: {refused.reason}: {bob.name} is already in "
                    f"{refused.current_network_name} ({refused.current_network_id})"
                )
            # Asking for the move takes him out of billing and into mobile.
            moved = client.networks.add_member(networks["mobile"].id, bob.id, force=True)
            left = next(n.name for n in networks.values() if n.id == moved.moved_from)
            print(f"moved {bob.name} out of {left}")

            # A key lets one engineer's own agent reach Memco directly, over
            # MCP. Its value is returned here and never again: hand it over now.
            created = client.users.create_key(users["alice"].external_id, preset="mcp_ro")
            print(f"\nkey {created.key.value_prefix}... expires {created.key.valid_until}")
            client.users.delete_key(users["alice"].external_id, created.key.id)
        finally:
            # Deleting a user revokes its keys. Projects go before the company
            # network they hang under.
            for user in users.values():
                client.users.delete(user.external_id)
            for network in reversed(list(networks.values())):
                client.networks.delete(network.id)
            print("removed again")


if __name__ == "__main__":
    main()
