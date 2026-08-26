"""Typed exceptions raised by the Memco SDK.

Every failure reaching a caller is one of these. Transport and protocol failures
arrive as :class:`MemcoAPIError` subclasses chosen by the gRPC status code;
problems with the client's own configuration arrive as :class:`MemcoConfigError`
before any request is sent.

The hierarchy is arranged so a caller can be as coarse or as precise as it likes::

    try:
        result = client.search("how does X work", domain="coding")
    except MemcoResourceExhaustedError:   # just this one condition
        ...
    except MemcoAPIError:             # anything the server reported
        ...
    except MemcoError:                # anything this SDK raises
        ...
"""

from __future__ import annotations

import enum

import grpc

__all__ = [
    "MemcoAPIError",
    "MemcoAuthenticationError",
    "MemcoConfigError",
    "MemcoError",
    "MemcoInternalError",
    "MemcoInvalidRequestError",
    "MemcoNotFoundError",
    "MemcoPermissionError",
    "MemcoResourceExhaustedError",
    "MemcoTimeoutError",
    "MemcoUnavailableError",
    "MemcoUnhealthyError",
    "ResourceExhaustedKind",
    "from_rpc_error",
]


class MemcoError(Exception):
    """Base class for every exception this SDK raises.

    Catch this to handle any Memco failure without distinguishing a
    misconfigured client from a server-reported error.
    """


class MemcoConfigError(MemcoError):
    """The client was configured incorrectly and no request was attempted.

    Raised for a missing credential, an unparseable host or port, or a
    non-positive timeout. It never indicates a problem with the service.

    Example:
        >>> Client(token=None)  # with no MEMCO_API_TOKEN set
        Traceback (most recent call last):
        MemcoConfigError: no API token: pass token=... or set MEMCO_API_TOKEN
    """


class MemcoAPIError(MemcoError):
    """The service returned a gRPC error status.

    Prefer catching one of the subclasses. This class is useful as a catch-all
    for "the call reached the server and failed", and carries the raw status for
    logging or for conditions the SDK does not model separately.

    Attributes:
        code: The gRPC status code the server returned.
        message: The status details string, as sent by the server. Treat this as
            human-readable text: it is not a stable API and should not be
            matched on except where this SDK already does so deliberately.
        debug_error_string: gRPC's internal diagnostic string, when the
            underlying error supplied one. Useful in bug reports; not parseable.
    """

    def __init__(
        self,
        code: grpc.StatusCode,
        message: str,
        debug_error_string: str | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            code: The gRPC status code the server returned.
            message: The status details string.
            debug_error_string: gRPC's internal diagnostic string, if available.
        """
        super().__init__(f"{code.name}: {message}")
        self.code = code
        self.message = message
        self.debug_error_string = debug_error_string


class MemcoAuthenticationError(MemcoAPIError):
    """The credential was missing, malformed, expired or revoked.

    The service deliberately returns one indistinguishable message for every
    such case, so this exception cannot tell you which of them applied. Check
    that the token is current and that it is being sent as
    ``authorization: Bearer <token>``.
    """


class MemcoPermissionError(MemcoAPIError):
    """The credential is valid but lacks the scope or role for this operation."""


class MemcoInvalidRequestError(MemcoAPIError):
    """The request was rejected as malformed.

    Also raised by this SDK *before* a request is sent, when a field exceeds a
    documented limit or a required combination of arguments is missing. In that
    case :attr:`~MemcoAPIError.code` is ``INVALID_ARGUMENT`` and the message
    names the offending field.
    """


class MemcoNotFoundError(MemcoAPIError):
    """A handle did not resolve to anything the caller may see.

    Note:
        Not every "not found" condition is an error. ``revert_memory`` reports a
        missing operation as a successful :class:`~memco.client.types.RevertResult`
        carrying :attr:`~memco.client.types.RevertOutcome.NOT_FOUND`, because that
        is caller-visible state rather than a service failure.
    """


class ResourceExhaustedKind(enum.Enum):
    """Which limit produced a ``RESOURCE_EXHAUSTED`` status.

    The service returns the same status code for a short-window rate limit and
    for an exhausted usage quota, and attaches no structured error detail to
    separate them. This SDK therefore infers the kind from the message text.

    Attributes:
        RATE_LIMIT: A short-window rate limit. Retrying after a brief pause is
            usually enough.
        QUOTA: A usage quota for the billing period. Retrying will not help
            until the quota resets or the plan changes.
        UNKNOWN: The message matched neither pattern. Read
            :attr:`~MemcoAPIError.message` to decide.
    """

    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    UNKNOWN = "unknown"


class MemcoResourceExhaustedError(MemcoAPIError):
    """A rate limit or a usage quota was exceeded.

    Attributes:
        kind: Which limit was hit, inferred from the message. See
            :class:`ResourceExhaustedKind` for why this is a heuristic and how
            reliable it is.

    Example:
        >>> try:
        ...     client.search("...", domain="coding")
        ... except MemcoResourceExhaustedError as exc:
        ...     if exc.kind is ResourceExhaustedKind.RATE_LIMIT:
        ...         time.sleep(60)
        ...     else:
        ...         raise
    """

    def __init__(
        self,
        code: grpc.StatusCode,
        message: str,
        debug_error_string: str | None = None,
    ) -> None:
        """Initialise the error and classify which limit was hit.

        Args:
            code: The gRPC status code the server returned.
            message: The status details string, which is inspected to infer
                :attr:`kind`.
            debug_error_string: gRPC's internal diagnostic string, if available.
        """
        super().__init__(code, message, debug_error_string)
        self.kind = _classify_exhaustion(message)


class MemcoUnavailableError(MemcoAPIError):
    """The service could not be reached, or reported itself as not ready.

    Covers transport failures such as a refused connection or a DNS failure, and
    is the base class of :class:`MemcoUnhealthyError`, so catching it also
    catches a server that answered but declared itself unhealthy.
    """


class MemcoUnhealthyError(MemcoUnavailableError):
    """The server answered a health check but reported that it is not serving.

    Distinct from a transport failure: the connection worked and the service
    replied, so the credential, host and TLS settings are all sound. The backend
    is simply not ready to take traffic.
    """


class MemcoTimeoutError(MemcoAPIError):
    """The call did not complete before its deadline.

    Pass a larger ``timeout`` to the individual method, or to the client to
    raise the default for every call.
    """


class MemcoInternalError(MemcoAPIError):
    """The service failed in a way it did not attribute to the request.

    Also used for any status code this SDK does not model separately, so that a
    new server-side status can never escape as a bare :class:`grpc.RpcError`.
    """


_RATE_LIMIT_MARKERS = ("rate limit",)
_QUOTA_MARKERS = ("daily", "quota", "reached your")

_STATUS_TO_ERROR: dict[grpc.StatusCode, type[MemcoAPIError]] = {
    grpc.StatusCode.UNAUTHENTICATED: MemcoAuthenticationError,
    grpc.StatusCode.PERMISSION_DENIED: MemcoPermissionError,
    grpc.StatusCode.INVALID_ARGUMENT: MemcoInvalidRequestError,
    grpc.StatusCode.NOT_FOUND: MemcoNotFoundError,
    grpc.StatusCode.RESOURCE_EXHAUSTED: MemcoResourceExhaustedError,
    grpc.StatusCode.UNAVAILABLE: MemcoUnavailableError,
    grpc.StatusCode.DEADLINE_EXCEEDED: MemcoTimeoutError,
}


def _classify_exhaustion(message: str) -> ResourceExhaustedKind:
    """Infer which limit produced a ``RESOURCE_EXHAUSTED`` status.

    Args:
        message: The status details string from the server.

    Returns:
        The inferred kind, or :attr:`ResourceExhaustedKind.UNKNOWN` when the
        message matches no known pattern.
    """
    lowered = message.lower()
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return ResourceExhaustedKind.RATE_LIMIT
    if any(marker in lowered for marker in _QUOTA_MARKERS):
        return ResourceExhaustedKind.QUOTA
    return ResourceExhaustedKind.UNKNOWN


def from_rpc_error(err: grpc.RpcError) -> MemcoAPIError:
    """Translate a raw :class:`grpc.RpcError` into the matching SDK exception.

    Every method on the client funnels failures through this function, so a
    caller never has to handle a bare gRPC error. An unrecognised status code
    becomes :class:`MemcoInternalError` rather than escaping untyped.

    Args:
        err: The error raised by the underlying gRPC call.

    Returns:
        The SDK exception corresponding to the error's status code. The result
        is returned rather than raised so callers can add context before
        raising it.

    Example:
        >>> try:
        ...     stub.Search(request)
        ... except grpc.RpcError as exc:
        ...     raise from_rpc_error(exc) from exc
    """
    code = err.code() if callable(getattr(err, "code", None)) else grpc.StatusCode.UNKNOWN
    details = err.details() if callable(getattr(err, "details", None)) else None
    debug = (
        err.debug_error_string() if callable(getattr(err, "debug_error_string", None)) else None
    )
    cls = _STATUS_TO_ERROR.get(code, MemcoInternalError)
    return cls(code, details or "", debug)
