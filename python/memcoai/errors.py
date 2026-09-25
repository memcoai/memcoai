"""Typed exceptions raised by the Memco SDK.

Every failure reaching a caller is one of these. Transport and protocol failures
arrive as :class:`MemcoAPIError` subclasses chosen by the gRPC status code;
problems with the client's own configuration arrive as :class:`MemcoConfigError`
before any request is sent.

The hierarchy is arranged so a caller can be as coarse or as precise as it likes::

    try:
        result = client.memory.search("how does X work", domain="coding")
    except MemcoResourceExhaustedError:   # just this one condition
        ...
    except MemcoAPIError:             # anything the server reported
        ...
    except MemcoError:                # anything this SDK raises
        ...
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import grpc
from google.rpc import error_details_pb2
from grpc_status import rpc_status

__all__ = [
    "MemcoAPIError",
    "MemcoAlreadyExistsError",
    "MemcoAuthenticationError",
    "MemcoConfigError",
    "MemcoError",
    "MemcoExternalUserNeedsCustomerNetworkError",
    "MemcoInternalError",
    "MemcoInvalidRequestError",
    "MemcoNotFoundError",
    "MemcoPermissionError",
    "MemcoPreconditionFailedError",
    "MemcoResourceExhaustedError",
    "MemcoSunsetError",
    "MemcoTimeoutError",
    "MemcoUnavailableError",
    "MemcoUnhealthyError",
    "MemcoUserAlreadyAssignedNetworkError",
    "ResourceExhaustedKind",
    "SunsetKind",
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

    One case is raised after requests were sent: a session key that this
    host's clock already reads as expired when it arrives. That takes a clock
    well ahead of the service's, and a service that does not report the
    seconds a key has left, so that its expiry time is read against this
    host's clock. The key's mint was sent, and so was the EndImpersonation
    that ends it at once; what needs correcting is the system clock.

    Example:
        >>> Memco(token=None)  # with no MEMCO_API_TOKEN set
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

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Support pickling and copying.

        The default reconstructs an exception from ``args``, which holds only
        the formatted message here. Without this, letting one of these
        propagate out of a worker process raises an opaque ``TypeError`` in
        place of the real error.

        Returns:
            The callable and arguments that rebuild this exception.
        """
        return (self.__class__, (self.code, self.message, self.debug_error_string))


class MemcoAuthenticationError(MemcoAPIError):
    """The credential was missing, malformed, expired or revoked.

    Never worth retrying: the same credential will be refused again. The
    service deliberately returns one indistinguishable message for every such
    case, so this exception cannot tell you which of them applied. Check that
    the token is current and that it is being sent as
    ``authorization: Bearer <token>``.
    """


class MemcoPermissionError(MemcoAPIError):
    """The credential is valid but lacks the scope or role for this operation.

    Never worth retrying: neither a retry nor a different argument changes the
    answer. The credential itself has to be granted what it was refused.
    """


class MemcoInvalidRequestError(MemcoAPIError):
    """The request was rejected as malformed.

    Never worth retrying: the same request will be refused again. Also raised
    by this SDK *before* a request is sent, when a field exceeds a documented
    limit or a required combination of arguments is missing. In that case
    :attr:`~MemcoAPIError.code` is ``INVALID_ARGUMENT`` and the message names
    the offending field.
    """


class MemcoNotFoundError(MemcoAPIError):
    """A handle did not resolve to anything the caller may see.

    Note:
        Not every "not found" condition is an error. ``revert_memory`` reports a
        missing operation as a successful :class:`~memcoai.types.RevertResult`
        carrying :attr:`~memcoai.types.RevertOutcome.NOT_FOUND`, because that
        is caller-visible state rather than a service failure.
    """


class MemcoAlreadyExistsError(MemcoAPIError):
    """What the call would create already exists.

    Raised for a network name or an external user id that is already taken.
    Never worth retrying as-is: the same request will be refused again. Either
    use what already exists, or create it under another name.

    Example:
        >>> try:
        ...     client.users.create("customer-42", roles=["reader"])
        ... except MemcoAlreadyExistsError:
        ...     user = client.users.get("customer-42")
    """


class MemcoPreconditionFailedError(MemcoAPIError):
    """The service refused the call because some precondition is unmet.

    Never worth retrying as-is: something outside the request has to change
    first, so neither a retry nor a different argument gets past it. A
    general-purpose condition: a version past its sunset, but equally a
    disabled billing account, an unaccepted set of terms, or a resource in the
    wrong state. :attr:`~MemcoAPIError.message` names which, because only the
    service knows.

    The cases this SDK models separately, because the service names them with
    a structured reason: a sunset, as :class:`MemcoSunsetError`, and the two
    refusals to place a user in a network,
    :class:`MemcoUserAlreadyAssignedNetworkError` and
    :class:`MemcoExternalUserNeedsCustomerNetworkError`.

    Attributes:
        metadata: The detail the service attached to the refusal, key to value,
            such as the network a user is already in. Empty when it attached
            none. Read-only, and the service's to extend: a key this SDK does
            not name is still here.
    """

    def __init__(
        self,
        code: grpc.StatusCode,
        message: str,
        debug_error_string: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            code: The gRPC status code the server returned.
            message: The status details string.
            debug_error_string: gRPC's internal diagnostic string, if available.
            metadata: The detail the service attached, if any.
        """
        super().__init__(code, message, debug_error_string)
        self.metadata: Mapping[str, str] = MappingProxyType(dict(metadata or {}))

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Support pickling and copying.

        The base implementation rebuilds with three arguments and would drop
        :attr:`metadata`.

        Returns:
            The callable and arguments that rebuild this exception.
        """
        return (
            self.__class__,
            (self.code, self.message, self.debug_error_string, dict(self.metadata)),
        )


class MemcoUserAlreadyAssignedNetworkError(MemcoPreconditionFailedError):
    """The user is already in another network of the same memory domain.

    A user is placed in one network per domain, so placing them in a second is
    refused rather than done as a silent move. :attr:`~MemcoAPIError.message`
    names the network they are in. Pass ``force=True`` to
    :meth:`~memcoai.administration.NetworkOperations.add_member` to move them.

    Attributes:
        reason: ``"USER_ALREADY_ASSIGNED_NETWORK"``, the reason the service
            names this refusal by.

    Example:
        >>> try:
        ...     client.networks.add_member(project.id, user.id)
        ... except MemcoUserAlreadyAssignedNetworkError as exc:
        ...     print(f"moving them out of {exc.current_network_name}")
        ...     client.networks.add_member(project.id, user.id, force=True)
    """

    reason = "USER_ALREADY_ASSIGNED_NETWORK"

    @property
    def current_network_id(self) -> str | None:
        """The id of the network the user is already in, or ``None`` if unsent."""
        return self.metadata.get("current_network_id")

    @property
    def current_network_name(self) -> str | None:
        """The name of the network the user is already in, or ``None`` if unsent."""
        return self.metadata.get("current_network_name")


class MemcoExternalUserNeedsCustomerNetworkError(MemcoPreconditionFailedError):
    """An external user can only be placed in a customer-scoped network.

    Refused for a network of any other scope, a domain's root network
    included. Place the user in a customer network, or create one for them
    with ``scope="customer"``.

    Attributes:
        reason: ``"EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK"``, the reason the
            service names this refusal by.

    Example:
        >>> try:
        ...     client.networks.add_member(root.id, user.id)
        ... except MemcoExternalUserNeedsCustomerNetworkError:
        ...     print("place them in a customer network instead")
    """

    reason = "EXTERNAL_USER_NEEDS_CUSTOMER_NETWORK"

    @property
    def required_network_scope(self) -> str | None:
        """The scope a network must have to take the user, or ``None`` if unsent."""
        return self.metadata.get("required_network_scope")


class SunsetKind(enum.Enum):
    """What a sunset blocked, and therefore what the remedy is.

    The service reports this as a structured detail rather than leaving it to
    be guessed from the message, because the two have opposite remedies and
    telling a caller the wrong one wastes their time.

    Attributes:
        CLIENT_VERSION: This build of the SDK is no longer served. Upgrade the
            package.
        API_VERSION: The API version this build speaks is no longer served.
            Upgrading the package is what moves a caller to the current one.
    """

    CLIENT_VERSION = "client_version"
    API_VERSION = "api_version"


class MemcoSunsetError(MemcoPreconditionFailedError):
    """What the caller is using is past its sunset date and is no longer served.

    The end state of a deprecation: the service announced it on every
    ``ListDomains`` while the version still worked, and now refuses it.
    Nothing was done, and retrying will not help until the caller upgrades.

    Both clients verify their connection before returning one, so a blocked
    version is refused there — in :class:`~memcoai.Memco`'s constructor, or in
    :meth:`~memcoai.AsyncMemco.connect` — rather than on the first real call.

    Attributes:
        kind: What was blocked — this SDK build, or the API version it speaks.
            See :class:`SunsetKind`; the two have different remedies.

    Example:
        >>> try:
        ...     client = Memco()
        ... except MemcoSunsetError as exc:
        ...     print(exc.kind, exc.message)
    """

    def __init__(
        self,
        code: grpc.StatusCode,
        message: str,
        debug_error_string: str | None,
        kind: SunsetKind,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            code: The gRPC status code the server returned.
            message: The status details string, which is the remedy to show.
            debug_error_string: gRPC's internal diagnostic string, if available.
            kind: What the service said was blocked. Read from a structured
                detail rather than inferred, so it is never a guess.
            metadata: The detail the service attached, if any.
        """
        super().__init__(code, message, debug_error_string, metadata)
        self.kind = kind

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Support pickling and copying.

        The base implementation rebuilds without :attr:`kind`, which this class
        requires.

        Returns:
            The callable and arguments that rebuild this exception.
        """
        return (
            self.__class__,
            (self.code, self.message, self.debug_error_string, self.kind, dict(self.metadata)),
        )


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

    Read :attr:`~MemcoResourceExhaustedError.kind` before retrying: a rate
    limit clears on a backoff, a quota will not refill on one. The transport
    does not retry this on the caller's behalf, precisely because which of the
    two it is decides whether retrying is worth anything, and only the caller
    can act on that.

    Attributes:
        kind: Which limit was hit, inferred from the message. See
            :class:`ResourceExhaustedKind` for why this is a heuristic and how
            reliable it is.

    Example:
        >>> try:
        ...     client.memory.search("...", domain="coding")
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

    The one failure here a backoff is the right answer to: nothing about the
    request is wrong. The transport already replays it — three attempts in all
    — on :meth:`~memcoai.operations.MemoryOperations.list_domains`,
    :meth:`~memcoai.operations.MemoryOperations.get_memory` and the health probe,
    so seeing it from one of those means all three failed. Every other method is
    left alone, because replaying a write can record it twice, so retrying one
    of those is the caller's decision and carries that risk.
    """


class MemcoUnhealthyError(MemcoUnavailableError):
    """The server answered a health check but reported that it is not serving.

    Distinct from a transport failure: the connection worked and the service
    replied, so the credential, host and TLS settings are all sound. The backend
    is simply not ready to take traffic. Nothing to reconfigure: wait, then
    construct the client again.
    """


class MemcoTimeoutError(MemcoAPIError):
    """The call did not complete before its deadline.

    The deadline is the caller's own — the ``timeout`` on the call, else the one
    the client was built with — which is why the transport does not retry this:
    a second attempt against the same deadline has no more time than the first.
    Pass a larger ``timeout`` to the individual method, or to the client to
    raise the default for every call.

    It says nothing about whether the service acted. A write that timed out may
    still have been recorded, so sending it again can write it twice.
    """


class MemcoInternalError(MemcoAPIError):
    """The service failed in a way it did not attribute to the request.

    Also used for any status code this SDK does not model separately, so that a
    new server-side status can never escape as a bare :class:`grpc.RpcError`.
    Nothing here says the call would have succeeded a moment later, so this is
    not the one to put a backoff loop around.
    """


# A quota is scoped to a billing period, so a period word is what separates it
# from a short-window rate limit. Checked first: "daily rate limit" is a quota,
# even though it also contains "rate limit".
_QUOTA_MARKERS = ("daily", "weekly", "monthly", "quota")
_RATE_LIMIT_MARKERS = ("rate limit", "per-minute", "per minute", "per-second", "too many requests")

_STATUS_TO_ERROR: dict[grpc.StatusCode, type[MemcoAPIError]] = {
    grpc.StatusCode.UNAUTHENTICATED: MemcoAuthenticationError,
    grpc.StatusCode.PERMISSION_DENIED: MemcoPermissionError,
    grpc.StatusCode.INVALID_ARGUMENT: MemcoInvalidRequestError,
    grpc.StatusCode.NOT_FOUND: MemcoNotFoundError,
    grpc.StatusCode.ALREADY_EXISTS: MemcoAlreadyExistsError,
    grpc.StatusCode.FAILED_PRECONDITION: MemcoPreconditionFailedError,
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
    if any(marker in lowered for marker in _QUOTA_MARKERS):
        return ResourceExhaustedKind.QUOTA
    if any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return ResourceExhaustedKind.RATE_LIMIT
    return ResourceExhaustedKind.UNKNOWN


# The service scopes its own reasons under this domain, so a reason another
# service happens to spell the same way is not read as Memco's. This is
# namespacing, not a trust boundary: anything terminating the connection to the
# configured host could write this domain into a trailer, exactly as it could
# already put anything it liked in an error message.
_ERROR_DOMAIN = "memco.ai"

_SUNSET_REASONS = {
    "CLIENT_VERSION_SUNSET": SunsetKind.CLIENT_VERSION,
    "API_VERSION_SUNSET": SunsetKind.API_VERSION,
}

_PRECONDITION_REASONS: dict[str, type[MemcoPreconditionFailedError]] = {
    cls.reason: cls
    for cls in (MemcoUserAlreadyAssignedNetworkError, MemcoExternalUserNeedsCustomerNetworkError)
}


def _memco_error_info(err: grpc.RpcError) -> tuple[str, dict[str, str]] | None:
    """Read the reason a ``FAILED_PRECONDITION`` names itself by, and its detail.

    The status carries a ``google.rpc.ErrorInfo`` in its trailer when the
    service names the cause. Reading it is what separates a sunset, or a refused
    placement, from every other precondition failure — a spent billing account,
    an account in the wrong state — which share the status code and must not be
    reported as "upgrade your client".

    Args:
        err: The error raised by the underlying gRPC call.

    Returns:
        The Memco-scoped reason and the metadata beside it, or ``None`` when the
        status carries none. ``None`` is the safe answer, as is a reason this
        build does not know: either yields the general
        :class:`MemcoPreconditionFailedError`, whose message still carries
        whatever the service said.
    """
    # Everything the trailer touches is inside the guard, the detail bytes
    # included: a trailer that is absent, malformed, or inconsistent with the
    # status is not worth failing over. It costs the caller a discriminator,
    # not their error, and raising here would replace a real failure with a
    # crash the caller cannot act on.
    try:
        status = rpc_status.from_call(err)
        if status is None:
            return None
        for detail in status.details:
            if not detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
                continue
            info = error_details_pb2.ErrorInfo()
            detail.Unpack(info)
            if info.domain == _ERROR_DOMAIN:
                return str(info.reason), {str(k): str(v) for k, v in info.metadata.items()}
    except Exception:
        return None
    return None


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
    code = err.code() if callable(getattr(err, "code", None)) else None
    if not isinstance(code, grpc.StatusCode):
        # A code of None would reach MemcoAPIError and fail on code.name, which
        # is worse than the raw error this function exists to replace.
        code = grpc.StatusCode.UNKNOWN
    details = err.details() if callable(getattr(err, "details", None)) else None
    debug = err.debug_error_string() if callable(getattr(err, "debug_error_string", None)) else None
    cls = _STATUS_TO_ERROR.get(code, MemcoInternalError)
    if code is grpc.StatusCode.FAILED_PRECONDITION:
        reason, metadata = _memco_error_info(err) or ("", {})
        if reason in _SUNSET_REASONS:
            return MemcoSunsetError(code, details or "", debug, _SUNSET_REASONS[reason], metadata)
        precondition = _PRECONDITION_REASONS.get(reason, MemcoPreconditionFailedError)
        return precondition(code, details or "", debug, metadata)
    return cls(code, details or "", debug)
