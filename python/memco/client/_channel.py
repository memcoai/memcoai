"""Channel construction shared by the synchronous and asynchronous clients."""

from __future__ import annotations

from typing import Any

import grpc

from ._auth import AsyncAuthInterceptor, AuthInterceptor
from ._config import ClientConfig

__all__ = ["build_async_channel", "build_channel"]


def build_channel(config: ClientConfig) -> grpc.Channel:
    """Open a synchronous channel with the credential interceptor attached.

    Args:
        config: Resolved client settings.

    Returns:
        A channel that adds the credential to every call.
    """
    if config.tls:
        channel: grpc.Channel = grpc.secure_channel(
            config.target, grpc.ssl_channel_credentials()
        )
    else:
        channel = grpc.insecure_channel(config.target)
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
            config.target, grpc.ssl_channel_credentials(), interceptors=interceptors
        )
    return grpc.aio.insecure_channel(config.target, interceptors=interceptors)
