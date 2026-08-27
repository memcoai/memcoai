"""Channel construction, including the paths a fake server cannot reach.

Every client fixture dials plaintext, so the TLS branches — which are the
production default — are never exercised end to end. These tests inspect what
the builders hand to gRPC instead, so losing an option on the secure path is
caught rather than shipped.
"""

from __future__ import annotations

import json
from typing import Any

import grpc
import pytest

from memco._channel import RETRYABLE_METHODS, USER_AGENT, build_async_channel, build_channel
from memco._config import resolve

_REAL_INSECURE = grpc.insecure_channel


@pytest.fixture
def captured_options(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record the options passed to whichever channel constructor is used."""
    seen: list[Any] = []

    def record(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("options"))
        return _REAL_INSECURE("localhost:1")

    for module, name in (
        (grpc, "secure_channel"),
        (grpc, "insecure_channel"),
        (grpc.aio, "secure_channel"),
        (grpc.aio, "insecure_channel"),
    ):
        monkeypatch.setattr(module, name, record)
    return seen


@pytest.mark.parametrize("tls", [True, False], ids=["tls", "plaintext"])
def test_the_sync_builder_sends_the_user_agent(captured_options: list[Any], tls: bool):
    build_channel(resolve(token="t", host="localhost:1", tls=tls, timeout=5.0, env={}))
    assert captured_options, "no channel was constructed"
    assert ("grpc.primary_user_agent", USER_AGENT) in (captured_options[-1] or [])


@pytest.mark.parametrize("tls", [True, False], ids=["tls", "plaintext"])
def test_the_async_builder_sends_the_user_agent(captured_options: list[Any], tls: bool):
    build_async_channel(resolve(token="t", host="localhost:1", tls=tls, timeout=5.0, env={}))
    assert captured_options, "no channel was constructed"
    assert ("grpc.primary_user_agent", USER_AGENT) in (captured_options[-1] or [])


def test_the_user_agent_is_a_single_stable_product_token():
    # The service matches deprecation rules against this token, so a second
    # term or a changed shape could break that match.
    assert USER_AGENT.startswith("memco-python/")
    assert " " not in USER_AGENT


@pytest.mark.parametrize("tls", [True, False], ids=["tls", "plaintext"])
@pytest.mark.parametrize("build", [build_channel, build_async_channel], ids=["sync", "async"])
def test_both_builders_carry_the_retry_policy(captured_options: list[Any], build: Any, tls: bool):
    # The policy is what keeps a blip on a read from reaching the caller. Losing
    # it on one builder or one transport would go unnoticed: nothing fails, the
    # SDK just stops retrying.
    build(resolve(token="t", host="localhost:1", tls=tls, timeout=5.0, env={}))
    options = dict(captured_options[-1] or [])
    assert options["grpc.enable_retries"] == 1
    # grpcio ignores `default_service_config` outright, so a policy set there
    # silently never retries. tests/test_retries.py counts the attempts.
    assert "grpc.default_service_config" not in options
    config = json.loads(options["grpc.service_config"])
    named = {
        entry["method"]
        for method_config in config["methodConfig"]
        for entry in method_config["name"]
        if entry["service"] == "memco.memory.v1.MemoryService"
    }
    assert named == set(RETRYABLE_METHODS)
