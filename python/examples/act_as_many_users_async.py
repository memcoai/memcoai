"""Act as several of your users at once, each in a session of their own.

One client holds one connection, and each session carries its own user's key,
so sessions for different users run side by side over it without ever sharing
a credential. Each key is ended as its session's block is left.

Run it with::

    export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
    python examples/act_as_many_users_async.py
"""

import asyncio
import uuid

from memcoai import AsyncMemco
from memcoai.types import ExternalUser

DOMAIN = "coding"
QUESTION = "how should an API client act for one of its own users"


async def ask(client: AsyncMemco, external_id: str) -> str:
    """Search as one user, in a session of their own.

    Args:
        client: A connected client holding the API client's credentials.
        external_id: Your id for the user to act as.

    Returns:
        A one-line summary of what the user found.
    """
    async with client.memory.with_session(DOMAIN, external_id=external_id) as session:
        result = await session.search(QUESTION)
        return f"{external_id} sees {len(result.memories)} memories"


async def main() -> None:
    """Run the example."""
    run = uuid.uuid4().hex[:8]  # keeps this run's names apart from any other run's

    async with AsyncMemco() as client:
        # Three users to act as, in one customer network; see map_your_users.py.
        # Removed again at the end.
        root = (await client.networks.list(parent_id="root", domain=DOMAIN)).networks[0]
        network = await client.networks.create(
            name=f"Acme Corp ({run})", parent_id=root.id, scope="customer"
        )
        users: list[ExternalUser] = []
        try:
            for handle in ("alice", "bob", "carol"):
                user = await client.users.create(f"acme-{handle}-{run}", roles=["reader"])
                users.append(user)
                await client.networks.add_member(network.id, user.id)

            # All three sessions are open at the same time, each under its own key.
            for line in await asyncio.gather(*(ask(client, user.external_id) for user in users)):
                print(line)
        finally:
            for user in users:
                await client.users.delete(user.external_id)
            await client.networks.delete(network.id)
            print("removed again")


if __name__ == "__main__":
    asyncio.run(main())
