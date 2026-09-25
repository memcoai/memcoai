"""Credential, host and port resolution."""

import warnings

import pytest

from memcoai import errors
from memcoai._config import DEFAULT_HOST, DEFAULT_PORT, resolve


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [("false", False), ("FALSE", False), (" False ", False), ("true", True), ("True", True)],
)
def test_tls_from_memco_api_tls(value: str, expected: bool):
    assert resolve(token="t", env={"MEMCO_API_TLS": value}).tls is expected


def test_a_blank_memco_api_tls_keeps_the_default():
    assert resolve(token="t", env={"MEMCO_API_TLS": "  "}).tls is True


@pytest.mark.parametrize("explicit", [True, False])
def test_explicit_tls_wins_over_memco_api_tls(explicit: bool):
    env = {"MEMCO_API_TLS": "true" if not explicit else "false"}
    assert resolve(token="t", tls=explicit, env=env).tls is explicit


@pytest.mark.parametrize("value", ["0", "no", "off", "flase", "yes"])
def test_an_unreadable_memco_api_tls_is_refused(value: str):
    # Whether traffic is encrypted is not something to guess from a typo.
    with pytest.raises(errors.MemcoConfigError, match="MEMCO_API_TLS"):
        resolve(token="t", env={"MEMCO_API_TLS": value})


def test_non_positive_timeout_raises_config_error():
    with pytest.raises(errors.MemcoConfigError, match="timeout"):
        resolve(token="t", timeout=0, env={})


# --- client credentials --------------------------------------------------

CLIENT_ENV = {"MEMCO_CLIENT_ID": "env-id", "MEMCO_CLIENT_SECRET": "env-secret"}


def test_client_credentials_from_arguments():
    cfg = resolve(client_id="client-a", client_secret="secret-a", env={})
    assert (cfg.client_id, cfg.client_secret) == ("client-a", "secret-a")
    assert not cfg.token


def test_client_credential_arguments_win_over_the_environment():
    cfg = resolve(
        client_id="client-a",
        client_secret="secret-a",
        env={**CLIENT_ENV, "MEMCO_API_TOKEN": "from-env"},
    )
    assert (cfg.client_id, cfg.client_secret) == ("client-a", "secret-a")
    assert not cfg.token


def test_client_credentials_in_the_environment_win_over_a_token_there():
    # CI exports both: the token for the memory suite, the pair for the admin
    # one. A client given no credential of its own runs as the API client.
    cfg = resolve(env={**CLIENT_ENV, "MEMCO_API_TOKEN": "from-env"})
    assert (cfg.client_id, cfg.client_secret) == ("env-id", "env-secret")
    assert not cfg.token


def test_a_token_argument_ignores_client_credentials_in_the_environment():
    cfg = resolve(token="explicit", env=CLIENT_ENV)
    assert cfg.token == "explicit"
    assert cfg.client_id is None
    assert cfg.client_secret is None


def test_client_credentials_skip_the_deprecated_token_variable_and_its_warning():
    # The token is never read, so warning that its variable is deprecated
    # would be noise about a value this client does not use.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = resolve(env={**CLIENT_ENV, "MEMCO_API_KEY": "legacy"})
    assert cfg.client_id == "env-id"


def test_a_blank_pair_in_the_environment_reads_as_unset():
    # What an unset CI secret expands to. A blank MEMCO_API_TOKEN already reads
    # as unset, and the pair follows it.
    cfg = resolve(
        env={"MEMCO_CLIENT_ID": "", "MEMCO_CLIENT_SECRET": "  ", "MEMCO_API_TOKEN": "from-env"}
    )
    assert cfg.token == "from-env"
    assert cfg.client_id is None


@pytest.mark.parametrize(
    "given",
    [
        {"client_id": "client-a", "client_secret": "secret-a"},
        {"client_id": "client-a"},
        {"client_secret": "secret-a"},
    ],
    ids=["the pair", "client_id", "client_secret"],
)
def test_a_token_argument_beside_client_arguments_is_refused(given):
    # Two credentials of different kinds: sending either one would be a guess
    # at which the caller meant.
    with pytest.raises(errors.MemcoConfigError):
        resolve(token="explicit", **given, env={})


@pytest.mark.parametrize(
    ("given", "missing"),
    [({"client_id": "client-a"}, "client_secret"), ({"client_secret": "secret-a"}, "client_id")],
)
def test_half_a_pair_of_arguments_is_refused(given, missing):
    # Even with a token in the environment to fall back on: the caller asked
    # for client credentials, and running as something else would hide that.
    with pytest.raises(errors.MemcoConfigError, match=missing):
        resolve(**given, env={"MEMCO_API_TOKEN": "from-env"})


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"MEMCO_CLIENT_ID": "env-id"}, "MEMCO_CLIENT_SECRET"),
        ({"MEMCO_CLIENT_SECRET": "env-secret"}, "MEMCO_CLIENT_ID"),
        ({"MEMCO_CLIENT_ID": "env-id", "MEMCO_CLIENT_SECRET": "   "}, "MEMCO_CLIENT_SECRET"),
    ],
    ids=["id only", "secret only", "blank secret"],
)
def test_half_a_pair_in_the_environment_is_refused(env, missing):
    # A deployment missing one of its two secrets must fail loudly rather
    # than quietly run as the token beside it.
    with pytest.raises(errors.MemcoConfigError, match=missing):
        resolve(env={**env, "MEMCO_API_TOKEN": "from-env"})


@pytest.mark.parametrize(
    "given",
    [
        {"client_id": "   ", "client_secret": "secret-a"},
        {"client_id": "client-a", "client_secret": ""},
    ],
    ids=["blank client_id", "empty client_secret"],
)
def test_a_blank_client_credential_argument_is_refused(given):
    with pytest.raises(errors.MemcoConfigError):
        resolve(**given, env={})


@pytest.mark.parametrize(
    ("given", "env"),
    [({"token": "t"}, {}), ({}, {"MEMCO_API_TOKEN": "from-env"})],
    ids=["token argument", "token variable"],
)
def test_token_lifetime_needs_client_credentials(given, env):
    # Only an issued token has a lifetime to ask for; accepting one beside a
    # static token would silently ignore it.
    with pytest.raises(errors.MemcoConfigError, match="token_lifetime"):
        resolve(**given, token_lifetime=600, env=env)


@pytest.mark.parametrize("lifetime", [0, -60])
def test_token_lifetime_must_be_positive(lifetime):
    with pytest.raises(errors.MemcoConfigError, match="token_lifetime"):
        resolve(client_id="client-a", client_secret="secret-a", token_lifetime=lifetime, env={})


def test_token_lifetime_has_no_local_maximum():
    # The service owns its cap. A copy of it here would go stale and start
    # refusing lifetimes the service accepts.
    resolve(client_id="client-a", client_secret="secret-a", token_lifetime=100_000, env={})


def test_token_lifetime_applies_to_client_credentials_from_the_environment():
    resolve(token_lifetime=600, env=CLIENT_ENV)


def test_the_client_secret_never_reaches_a_repr():
    # Error trackers capture local variables, and this object is live while
    # the token is being issued.
    cfg = resolve(client_id="client-a", client_secret="s3cr3t-value", env={})
    assert "s3cr3t-value" not in repr(cfg)
