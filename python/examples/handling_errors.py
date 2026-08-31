"""Every failure mode the SDK raises, and what to do about each.

No raw grpc.RpcError ever reaches you: everything is a MemcoError subclass, so
you can be as coarse or as precise as you like.

Run it with::

    export MEMCO_API_TOKEN=...
    python examples/handling_errors.py
"""

import time

from memco import Memco
from memco.errors import (
    MemcoAPIError,
    MemcoAuthenticationError,
    MemcoConfigError,
    MemcoInvalidRequestError,
    MemcoNotFoundError,
    MemcoPermissionError,
    MemcoPreconditionFailedError,
    MemcoResourceExhaustedError,
    MemcoSunsetError,
    MemcoTimeoutError,
    MemcoUnavailableError,
    MemcoUnhealthyError,
    ResourceExhaustedKind,
    SunsetKind,
)


def connect() -> Memco | None:
    """Build a client, reporting anything that stops it connecting.

    Returns:
        The connected client, or ``None`` if it could not be built.
    """
    try:
        return Memco()
    except MemcoConfigError as exc:
        # Nothing was sent: no token, or an unusable host or port.
        print(f"configuration problem: {exc}")
    except MemcoSunsetError as exc:
        # Past its sunset date and no longer served. Nothing to retry and
        # nothing to reconfigure. Long before this, the same service sends a
        # MemcoDeprecationWarning through the warnings module, on every version
        # that still works, carrying the date this happens.
        if exc.kind is SunsetKind.API_VERSION:
            # Not "reinstall the package": the API version this build speaks is
            # the thing that stopped being served.
            print(f"API version no longer served: {exc.message}")
        else:
            print(f"upgrade required: {exc.message}")
    except MemcoPreconditionFailedError as exc:
        # Same status code, unrelated cause: billing, account state, terms.
        # MemcoSunsetError subclasses this, so order matters.
        print(f"precondition unmet: {exc.message}")
    except MemcoTimeoutError as exc:
        # Neither a MemcoUnavailableError nor a MemcoConfigError, so it needs
        # its own branch even in a connection helper.
        print(f"timed out reaching the service: {exc}")
    except MemcoUnhealthyError as exc:
        # The service answered and said it is not ready. The endpoint, TLS and
        # credential are all fine; the backend is not taking traffic.
        print(f"service is not serving: {exc}")
    except MemcoUnavailableError as exc:
        # Could not reach it at all. MemcoUnhealthyError subclasses this, so
        # order matters: catch the specific one first.
        print(f"unreachable: {exc}")
    return None


def search_with_retry(client: Memco, query: str, domain: str) -> None:
    """Search, handling each failure the way that failure deserves.

    Args:
        client: A connected client.
        query: The search query.
        domain: The domain to search.
    """
    for attempt in range(3):
        try:
            result = client.memory.search(query, domain=domain, timeout=10.0)
            print(f"{len(result.memories)} memories")
            return

        except MemcoInvalidRequestError as exc:
            # Malformed request. Retrying is pointless — fix the call. Some of
            # these are raised locally, before anything is sent.
            print(f"bad request: {exc.message}")
            return

        except MemcoAuthenticationError:
            # Missing, expired or revoked credential. The service returns one
            # indistinguishable message for all of them by design.
            print("credential rejected; check MEMCO_API_TOKEN is current")
            return

        except MemcoPermissionError:
            # Authenticated, but this credential lacks the scope or role.
            print("credential lacks permission for this operation")
            return

        except MemcoNotFoundError:
            print("nothing there")
            return

        except MemcoResourceExhaustedError as exc:
            # Rate limit and usage quota share a status code, so the SDK infers
            # which one from the message. Only the rate limit is worth waiting
            # out; a spent quota will not clear on this timescale.
            if exc.kind is ResourceExhaustedKind.QUOTA:
                print("usage quota exhausted; retrying will not help")
                return
            wait = 2**attempt
            print(f"rate limited; retrying in {wait}s")
            time.sleep(wait)

        except MemcoTimeoutError:
            print("timed out; pass a larger timeout=")
            return

        except MemcoUnavailableError:
            wait = 2**attempt
            print(f"service unavailable; retrying in {wait}s")
            time.sleep(wait)

        except MemcoAPIError as exc:
            # Anything the service reported that is not modelled above.
            print(f"unexpected {exc.code.name}: {exc.message}")
            return

    print("gave up after 3 attempts")


def main() -> None:
    """Run the example."""
    client = connect()
    if client is None:
        return
    with client:
        search_with_retry(client, "how does authentication work", "coding")

        # Structural problems are caught before anything is sent. Field length
        # limits belong to the service, so an over-long value is reported by it.
        try:
            client.memory.search("", domain="coding")
        except MemcoInvalidRequestError as exc:
            print(f"caught locally, nothing sent: {exc.message}")


if __name__ == "__main__":
    main()
