"""Channel construction shared by the synchronous and asynchronous clients."""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

import grpc

from ._auth import AsyncAuthInterceptor, AuthInterceptor
from ._config import ClientConfig

__all__ = ["USER_AGENT", "build_async_channel", "build_channel"]


def _user_agent() -> str:
    """Build the identifier this client sends on every call.

    The product token is deliberately a single stable ``memco-python/<version>``:
    the service matches deprecation rules against it, and it feeds client
    tracking that expects that shape. A client sending no recognisable token can
    never be told its build is out of date. gRPC appends its own runtime token
    rather than replacing this, so the transport stays identified too.

    Returns:
        A user-agent fragment such as ``memco-python/0.1.0``.
    """
    # Read from the installed metadata rather than from memco.__version__:
    # memco/__init__.py imports this module, so reaching back into the package
    # here would be circular. Both read the same source, so they cannot drift.
    return f"memco-python/{version('memco')}"


USER_AGENT = _user_agent()
"""Sent as the primary user agent on every channel.

Computed once so the synchronous and asynchronous builders cannot disagree.
"""

_OPTIONS = [("grpc.primary_user_agent", USER_AGENT)]


def build_channel(config: ClientConfig) -> grpc.Channel:
    """Open a synchronous channel with the credential interceptor attached.

    Args:
        config: Resolved client settings.

    Returns:
        A channel that adds the credential to every call.
    """
    if config.tls:
        channel: grpc.Channel = grpc.secure_channel(
            config.target, grpc.ssl_channel_credentials(), options=_OPTIONS
        )
    else:
        channel = grpc.insecure_channel(config.target, options=_OPTIONS)
    return grpc.intercept_channel(channel, AuthInterceptor(config.token))


def build_async_channel(config: ClientConfig) -> Any:
    """Open an asyncio channel with the credential interceptor attached.

    Args:
        config: Resolved client settings.

    Returns:
        A :class:`grpc.aio.Channel` that adds the credential to every call.
    """
    interceptors = [AsyncAuthInterceptor(config.token)]
    if config.tls:
        return grpc.aio.secure_channel(
            config.target,
            grpc.ssl_channel_credentials(),
            interceptors=interceptors,
            options=_OPTIONS,
        )
    return grpc.aio.insecure_channel(config.target, interceptors=interceptors, options=_OPTIONS)
