"""Credential, host and port resolution."""

import pytest

from memco.client import errors
from memco.client._config import DEFAULT_HOST, DEFAULT_PORT, resolve


def test_explicit_token_wins_over_env():
    cfg = resolve(token="explicit", env={"MEMCO_API_TOKEN": "from-env"})
    assert cfg.token == "explicit"


def test_token_falls_back_to_memco_api_token():
    assert resolve(env={"MEMCO_API_TOKEN": "t"}).token == "t"


def test_token_falls_back_to_deprecated_memco_api_key_with_warning():
    with pytest.warns(DeprecationWarning, match="MEMCO_API_KEY"):
        cfg = resolve(env={"MEMCO_API_KEY": "legacy"})
    assert cfg.token == "legacy"


def test_memco_api_token_takes_precedence_over_the_deprecated_name():
    cfg = resolve(env={"MEMCO_API_TOKEN": "new", "MEMCO_API_KEY": "old"})
    assert cfg.token == "new"


def test_missing_token_raises_config_error():
    with pytest.raises(errors.MemcoConfigError, match="MEMCO_API_TOKEN"):
        resolve(env={})


def test_blank_token_is_treated_as_missing():
    with pytest.raises(errors.MemcoConfigError):
        resolve(token="   ", env={})


def test_host_defaults():
    cfg = resolve(token="t", env={})
    assert (cfg.host, cfg.port) == (DEFAULT_HOST, DEFAULT_PORT)
    assert cfg.target == f"{DEFAULT_HOST}:{DEFAULT_PORT}"


def test_host_from_env():
    assert resolve(token="t", env={"MEMCO_API_HOST": "example.test"}).host == "example.test"


def test_explicit_host_wins_over_env():
    cfg = resolve(token="t", host="arg.test", env={"MEMCO_API_HOST": "env.test"})
    assert cfg.host == "arg.test"


def test_host_may_carry_a_port():
    cfg = resolve(token="t", host="localhost:50051", env={})
    assert (cfg.host, cfg.port) == ("localhost", 50051)


def test_unparseable_port_raises_config_error():
    with pytest.raises(errors.MemcoConfigError, match="port"):
        resolve(token="t", host="localhost:not-a-port", env={})


def test_out_of_range_port_raises_config_error():
    with pytest.raises(errors.MemcoConfigError, match="port"):
        resolve(token="t", host="localhost:99999", env={})


def test_tls_and_timeout_defaults():
    cfg = resolve(token="t", env={})
    assert cfg.tls is True
    assert cfg.timeout == 30.0


def test_non_positive_timeout_raises_config_error():
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        resolve(token="t", timeout=0, env={})
