"""Channel construction shared by the synchronous and asynchronous clients."""

from __future__ import annotations

import platform
from importlib.metadata import version
from typing import Any

import grpc

from ._auth import AsyncAuthInterceptor, AuthInterceptor
from ._config import ClientConfig

__all__ = ["USER_AGENT", "build_async_channel", "build_channel"]


def _user_agent() -> str:
    """Build the identifier this client sends on every call.

    Names the SDK, its version and the interpreter it is running on, so the
    service can see which versions are in use and retire support on evidence
    rather than on a guess. gRPC prepends this to its own token rather than
    replacing it, so the transport is still identified.

    Returns:
        A user-agent fragment such as ``memco-python/0.1.0 python/3.10.20``.
    """
    # Read from the installed metadata rather than from memco.__version__:
    # memco/__init__.py imports this module, so reaching back into the package
    # here would be circular. Both read the same source, so they cannot drift.
    return f"memco-python/{version('memco')} python/{platform.python_version()}"


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
