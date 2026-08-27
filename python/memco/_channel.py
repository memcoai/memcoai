"""Channel construction shared by the synchronous and asynchronous clients."""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import Any

import grpc
from grpc_health.v1 import health_pb2

from ._auth import AsyncAuthInterceptor, AuthInterceptor
from ._config import ClientConfig
from .memory.v1 import memory_pb2

__all__ = ["RETRYABLE_METHODS", "USER_AGENT", "build_async_channel", "build_channel"]


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

_MEMORY_SERVICE = memory_pb2.DESCRIPTOR.services_by_name["MemoryService"].full_name
_HEALTH_SERVICE = health_pb2.DESCRIPTOR.services_by_name["Health"].full_name

RETRYABLE_METHODS = ("DescribeDomains", "GetMemory")
"""The memory methods a lost connection may safely be replayed on.

gRPC's configurable retries are at-least-once: a retry sent after the server
committed produces a duplicate, not a second chance. So a method belongs here
only if replaying it cannot mint anything or change what a later call returns.

Deliberately absent, and why each one is worse than it looks:

* ``CreateMemory``, ``EnrichMemory``, ``ShareFeedback``, ``RevertMemory`` mint
  an operation id on the terminal side. A replay writes twice.
* ``StartSession`` mints a session id.
* ``Search`` mints one too. The contract is explicit that omitting
  ``session_id`` opens a session and returns it on the response, so replaying
  the domain-only form — the form the quickstart uses — can orphan a session
  the caller never learns the id of. Worse, replaying the *scoped* form can
  come back degraded: the first attempt already recorded delivery, so the
  memories return as bare ``reference`` handles carrying no insights. A caller
  iterating ``insights`` would see nothing and read it as "no results", which
  is a wrong answer rather than an error. Search is the hot path and leaving it
  out costs the most; a service-side idempotency key is what would let it back
  in.

``GetMemory`` is in despite counting the delivery: replaying it advances
``times_served`` by one. The contract says that field "records deliveries and
nothing else — a frequently served result is not thereby a correct one", so a
duplicate moves a counter that carries no judgement, and the content it returns
is the same either way. That is the whole of the cost, unlike the cases above.

The health probe is retried too, and is named separately below because it
belongs to a service this SDK does not own.
"""

_RETRY_POLICY = {
    "maxAttempts": 3,
    "initialBackoff": "0.1s",
    "maxBackoff": "1s",
    "backoffMultiplier": 2,
    # UNAVAILABLE only. DEADLINE_EXCEEDED is the caller's own deadline, so
    # retrying past it cannot help, and RESOURCE_EXHAUSTED is a rate limit or a
    # spent quota, which the caller handles by kind.
    "retryableStatusCodes": ["UNAVAILABLE"],
}

_SERVICE_CONFIG = {
    "methodConfig": [
        {
            "name": [{"service": _MEMORY_SERVICE, "method": method} for method in RETRYABLE_METHODS]
            # The probe the client construction gates on. It creates nothing,
            # and leaving it out would make a blip during connect the one
            # transient failure the SDK cannot absorb.
            + [{"service": _HEALTH_SERVICE, "method": "Check"}],
            "retryPolicy": _RETRY_POLICY,
        }
    ]
}

_OPTIONS = [
    ("grpc.primary_user_agent", USER_AGENT),
    ("grpc.enable_retries", 1),
    # `service_config`, not `default_service_config`. The default is the option
    # that reads as correct here — it yields to a policy the service publishes
    # — but it does not take effect: grpcio 1.83 ignores it on every target
    # form (host:port, dns:///, ipv4:), so a policy set that way silently never
    # retries. tests/test_retries.py counts the attempts the server saw, so
    # swapping this back fails rather than quietly disabling the policy.
    #
    # The cost is real and worth naming: this one overrides whatever the
    # resolver publishes, so the service cannot hand Python clients a policy of
    # its own while it is set. That is the wrong way round for a value the
    # service owns, and it is the option that works.
    ("grpc.service_config", json.dumps(_SERVICE_CONFIG)),
    # gRPC caps reconnect backoff at two minutes by default, so a client idle
    # through a short outage can sit unusable long after the service returns.
    ("grpc.initial_reconnect_backoff_ms", 200),
    ("grpc.max_reconnect_backoff_ms", 5000),
]


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
