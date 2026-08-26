"""Credential injection for outgoing calls.

The credential travels as a gRPC metadata header on every request. It is
attached by a channel interceptor rather than by
:func:`grpc.metadata_call_credentials`, because gRPC refuses call credentials on
an insecure channel and the SDK must also support plaintext endpoints for local
development and tests.
"""

from __future__ import annotations

from collections import namedtuple
from typing import Any, Sequence

import grpc

AUTH_HEADER = "authorization"
"""Metadata key carrying the credential."""

AUTH_SCHEME = "Bearer "
"""Scheme prefix placed before the credential.

The service matches this with a case-sensitive prefix check, so the capital
``B`` and the trailing space are both load-bearing. A lowercase ``bearer``
is rejected as an invalid credential.
"""

UNAUTHENTICATED_PREFIX = "/grpc.health.v1."
"""Methods the credential is deliberately withheld from.

The health service bypasses authentication on the server, so sending the
credential there would achieve nothing and would widen its exposure: health
endpoints are the ones routinely reached by load balancers and sidecars, which
log request headers far more liberally than the application path does, and the
probe fires on every client construction before the caller has made a request.
"""

__all__ = ["AUTH_HEADER", "AUTH_SCHEME", "AsyncAuthInterceptor", "AuthInterceptor"]


class _ClientCallDetails(
    namedtuple(
        "_ClientCallDetails",
        ("method", "timeout", "metadata", "credentials", "wait_for_ready", "compression"),
    ),
    grpc.ClientCallDetails,  # type: ignore[misc]
):
    """Call details rebuilt with the credential added.

    ``grpc.ClientCallDetails`` is read-only, so an interceptor that wants to add
    metadata has to construct a replacement.
    """


def _is_unauthenticated(method: str | bytes) -> bool:
    """Report whether a method is one the credential is withheld from.

    Args:
        method: The full method name from the call details. The synchronous
            channel supplies this as ``str`` and the asyncio channel as
            ``bytes``, so both are accepted.

    Returns:
        ``True`` if the credential must not be attached to this call.
    """
    name = method.decode("ascii", "replace") if isinstance(method, bytes) else method
    return name.startswith(UNAUTHENTICATED_PREFIX)


def _header(token: str) -> tuple[str, str]:
    """Build the metadata entry carrying the credential.

    Args:
        token: The API key or JWT to send.

    Returns:
        The metadata key and value pair.
    """
    return (AUTH_HEADER, f"{AUTH_SCHEME}{token}")


def _merged(metadata: Sequence[tuple[str, Any]] | None, token: str) -> list[tuple[str, Any]]:
    """Combine caller metadata with the credential.

    Any credential already present is dropped rather than kept alongside the
    real one. gRPC permits repeated keys, and servers disagree about which
    occurrence wins, so appending would let caller-supplied metadata decide
    which credential the server reads.

    Args:
        metadata: Metadata already on the call, if any.
        token: The credential to add.

    Returns:
        The combined metadata, with exactly one credential entry, last.
    """
    kept = [
        (key, value)
        for key, value in (metadata or ())
        if key.lower() != AUTH_HEADER
    ]
    return [*kept, _header(token)]


class AuthInterceptor(grpc.UnaryUnaryClientInterceptor):  # type: ignore[misc]
    """Adds the credential to every unary call on a synchronous channel."""

    def __init__(self, token: str) -> None:
        """Initialise the interceptor.

        Args:
            token: The API key or JWT to send on each call.
        """
        self._token = token

    def intercept_unary_unary(  # noqa: D102 - signature fixed by grpc
        self,
        continuation: Any,
        client_call_details: grpc.ClientCallDetails,
        request: Any,
    ) -> Any:
        if _is_unauthenticated(client_call_details.method):
            return continuation(client_call_details, request)
        details = _ClientCallDetails(
            client_call_details.method,
            client_call_details.timeout,
            _merged(client_call_details.metadata, self._token),
            client_call_details.credentials,
            getattr(client_call_details, "wait_for_ready", None),
            getattr(client_call_details, "compression", None),
        )
        return continuation(details, request)


class AsyncAuthInterceptor(grpc.aio.UnaryUnaryClientInterceptor):  # type: ignore[misc]
    """Adds the credential to every unary call on an asyncio channel."""

    def __init__(self, token: str) -> None:
        """Initialise the interceptor.

        Args:
            token: The API key or JWT to send on each call.
        """
        self._token = token

    async def intercept_unary_unary(  # noqa: D102 - signature fixed by grpc
        self,
        continuation: Any,
        client_call_details: Any,
        request: Any,
    ) -> Any:
        if _is_unauthenticated(client_call_details.method):
            return await continuation(client_call_details, request)
        details = client_call_details._replace(
            metadata=_merged(list(client_call_details.metadata or []), self._token)
        )
        return await continuation(details, request)
