"""Resolution of the client's credential, endpoint and call defaults."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from typing import Mapping

from .errors import MemcoConfigError

DEFAULT_HOST = "grpc.spark.memco.ai"
"""Endpoint used when neither an argument nor ``MEMCO_API_HOST`` supplies one."""

DEFAULT_PORT = 443
"""Port used when the host does not carry one of its own."""

DEFAULT_TIMEOUT = 30.0
"""Per-call deadline in seconds, used when a method is given no ``timeout``."""

TOKEN_ENV = "MEMCO_API_TOKEN"
"""Environment variable holding the credential."""

LEGACY_TOKEN_ENV = "MEMCO_API_KEY"
"""Deprecated predecessor of :data:`TOKEN_ENV`, still honoured with a warning."""

HOST_ENV = "MEMCO_API_HOST"
"""Environment variable overriding :data:`DEFAULT_HOST`."""


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Fully resolved settings for one client.

    Attributes:
        token: The credential sent as ``authorization: Bearer <token>``. This is
            either a static Memco API key or a WorkOS JWT; the service accepts
            both in the same header. Excluded from ``repr`` so it cannot reach a
            log or a crash report: error trackers such as Sentry capture local
            variables by default, and this object is live while the channel is
            being dialled.
        host: Hostname of the service, without a port.
        port: TCP port of the service.
        tls: Whether to dial over TLS using the system trust store.
        timeout: Default per-call deadline in seconds.
    """

    token: str = field(repr=False)
    host: str
    port: int
    tls: bool
    timeout: float

    @property
    def target(self) -> str:
        """The ``host:port`` string to dial.

        Returns:
            The gRPC target, for example ``grpc.spark.memco.ai:443``.
        """
        return f"{self.host}:{self.port}"


def _resolve_token(token: str | None, env: Mapping[str, str]) -> str:
    """Pick the credential from the argument or the environment.

    Args:
        token: Explicit credential, or ``None`` to read the environment.
        env: Environment mapping to read from.

    Returns:
        The resolved, whitespace-stripped credential.

    Raises:
        MemcoConfigError: If no non-blank credential is available.
    """
    if token is not None and token.strip():
        return token.strip()
    if token is not None and not token.strip():
        raise MemcoConfigError(
            f"the token passed to the client is blank: pass a real token or set {TOKEN_ENV}"
        )

    from_env = env.get(TOKEN_ENV, "").strip()
    if from_env:
        return from_env

    legacy = env.get(LEGACY_TOKEN_ENV, "").strip()
    if legacy:
        warnings.warn(
            f"{LEGACY_TOKEN_ENV} is deprecated and will be removed in a future release; "
            f"rename it to {TOKEN_ENV}.",
            DeprecationWarning,
            stacklevel=3,
        )
        return legacy

    raise MemcoConfigError(f"no API token: pass token=... or set {TOKEN_ENV}")


def _split_host_port(host: str) -> tuple[str, int]:
    """Split an optional ``host:port`` string into its parts.

    Args:
        host: A bare hostname, or one carrying an explicit port.

    Returns:
        The hostname and the port, defaulting to :data:`DEFAULT_PORT`.

    Raises:
        MemcoConfigError: If a port is present but is not an integer in 1-65535.
    """
    name, separator, port_text = host.rpartition(":")
    if not separator:
        return host, DEFAULT_PORT
    try:
        port = int(port_text)
    except ValueError:
        raise MemcoConfigError(
            f"host {host!r} has an unparseable port: {port_text!r} is not an integer"
        ) from None
    if not 1 <= port <= 65535:
        raise MemcoConfigError(f"host {host!r} has a port outside the range 1-65535: {port}")
    return name, port


def resolve(
    token: str | None = None,
    host: str | None = None,
    *,
    tls: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> ClientConfig:
    """Resolve client settings from arguments and the environment.

    Arguments always win over the environment, which always wins over the
    built-in defaults.

    Args:
        token: Credential to authenticate with. When omitted, ``MEMCO_API_TOKEN``
            is used, falling back to the deprecated ``MEMCO_API_KEY`` with a
            :class:`DeprecationWarning`.
        host: Service endpoint, optionally including a port such as
            ``localhost:50051``. When omitted, ``MEMCO_API_HOST`` is used,
            falling back to :data:`DEFAULT_HOST`. A host without a port gets
            :data:`DEFAULT_PORT`.
        tls: Whether to dial over TLS with the system trust store. Set to
            ``False`` only for a plaintext endpoint, such as a local server.
        timeout: Default per-call deadline in seconds.
        env: Environment mapping to read from. Defaults to :data:`os.environ`;
            supplying one is mainly useful in tests.

    Returns:
        The resolved configuration.

    Raises:
        MemcoConfigError: If no credential is available, if the host carries an
            invalid port, or if ``timeout`` is not positive.

    Example:
        >>> resolve(token="sk-...", host="localhost:50051", tls=False).target
        'localhost:50051'
    """
    environment = os.environ if env is None else env

    if timeout <= 0:
        raise MemcoConfigError(f"timeout must be positive, got {timeout!r}")

    resolved_token = _resolve_token(token, environment)
    resolved_host = host or environment.get(HOST_ENV) or DEFAULT_HOST
    name, port = _split_host_port(resolved_host)

    return ClientConfig(token=resolved_token, host=name, port=port, tls=tls, timeout=timeout)


def deadline(timeout: float | None, default: float) -> float:
    """Resolve the deadline for one call.

    Args:
        timeout: The caller's per-call deadline, or ``None`` to use the default.
        default: The client's default deadline.

    Returns:
        The deadline in seconds.

    Raises:
        MemcoConfigError: If a deadline was given but is not positive. A
            zero or negative deadline is silently useless — the call expires
            before it is sent — so it is rejected rather than substituted.
    """
    if timeout is None:
        return default
    if timeout <= 0:
        raise MemcoConfigError(f"timeout must be positive, got {timeout!r}")
    return timeout
