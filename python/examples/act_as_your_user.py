"""Act as one of your users: search and write in a session that carries their key.

Opening a session with an ``external_id`` mints a short-lived key acting as
that user, and every call through the session carries it -- so what the session
finds and writes is that user's, bounded by the network they are in. The key
renews itself while the session is open and is ended when it closes.

Run it with::

    export MEMCO_CLIENT_ID=... MEMCO_CLIENT_SECRET=...
    python examples/act_as_your_user.py
"""

import time
import uuid

from memcoai import Memco
from memcoai.types import RevertOutcome

DOMAIN = "coding"
REVERT_ATTEMPTS = 60  # a second apart: how long to wait for the write to be ingested


def main() -> None:
    """Run the example."""
    run = uuid.uuid4().hex[:8]  # keeps this run's names apart from any other run's

    with Memco() as client:
        # The user to act as, placed in a customer network of their own; see
        # map_your_users.py. Removed again at the end.
        root = client.networks.list(parent_id="root", domain=DOMAIN).networks[0]
        network = client.networks.create(
            name=f"Acme Corp ({run})", parent_id=root.id, scope="customer"
        )
        user = client.users.create(f"acme-alice-{run}", name="Alice Andersson", roles=["creator"])
        try:
            client.networks.add_member(network.id, user.id)

            # Leaving the block ends the key. A session opened with
            # start_session instead must be closed with session.close().
            with client.memory.with_session(DOMAIN, external_id=user.external_id) as session:
                result = session.search("how should an API client act for one of its own users")
                print(f"{user.external_id} sees {len(result.memories)} memories")

                written = session.create_memory(
                    query="how should an API client act for one of its own users",
                    title="Act for a user through a session opened with their external id",
                    content=(
                        "An API client acts for one of its own users by opening a memory "
                        "session with that user's external id. The session carries a "
                        "short-lived key acting as the user, so what it finds and writes is "
                        "bounded by the network the user was placed in, and the key is "
                        "ended as soon as the session closes."
                    ),
                )
                print(f"written as {user.external_id}: {written.operation_id}")

                # The write is the user's, so it is undone through their
                # session. It is ingested asynchronously, and until it has
                # been, reverting reports NOT_FOUND rather than removing it.
                if written.operation_id:
                    for _ in range(REVERT_ATTEMPTS):
                        reverted = session.revert_memory(written.operation_id)
                        if reverted.outcome is not RevertOutcome.NOT_FOUND:
                            break
                        time.sleep(1)
                    print(f"revert: {reverted.outcome.name}")
        finally:
            client.users.delete(user.external_id)
            client.networks.delete(network.id)
            print("removed again")


if __name__ == "__main__":
    main()
