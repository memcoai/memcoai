"""Credential, host and port resolution."""

import warnings

import pytest

from memcoai import Integration, errors
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


@pytest.mark.parametrize("version", ["0.2.0-rc.0", "1.0.0+build.5", "0.2.1.dev3+g1abc"])
def test_an_integration_is_kept_as_given(version: str):
    integration = Integration(name="memco-eve", version=version)
    assert resolve(token="t", integration=integration, env={}).integration == integration


def test_an_integration_is_read_once_so_what_is_sent_is_what_was_checked():
    class Shifty(Integration):
        reads = 0

        @property
        def name(self) -> str:
            type(self).reads += 1
            return "memco-eve" if type(self).reads == 1 else "evil\r\nx"

    shifty = Shifty.__new__(Shifty)
    object.__setattr__(shifty, "version", "0.2.0")
    config = resolve(token="t", integration=shifty, env={})
    assert config.integration == Integration(name="memco-eve", version="0.2.0")


@pytest.mark.parametrize("part", ["name", "version"])
def test_each_integration_part_is_kept_as_the_plain_string_that_was_checked(part: str):
    class Sneaky(str):
        def __format__(self, spec: str) -> str:
            return "evil\r\nx"

    parts = {"name": "memco-eve", "version": "1"}
    checked = parts[part]
    parts[part] = Sneaky(checked)
    config = resolve(token="t", integration=Integration(**parts), env={})
    assert config.integration is not None
    value = getattr(config.integration, part)
    assert type(value) is str
    assert f"{value}" == checked


def test_no_integration_resolves_to_none():
    assert resolve(token="t", env={}).integration is None


def test_a_scoped_package_name_is_accepted_and_kept_as_given():
    integration = Integration(name="@memco/eve", version="0.2.0")
    assert resolve(token="t", integration=integration, env={}).integration == integration


@pytest.mark.parametrize("name", ["@memco", "memco/eve", "@/eve", "@memco/", "@a/b/c", "@@a/b"])
def test_a_name_that_is_not_a_word_or_a_scoped_name_is_refused(name: str):
    with pytest.raises(errors.MemcoConfigError, match="integration name must be"):
        resolve(token="t", integration=Integration(name=name, version="1"), env={})


@pytest.mark.parametrize(
    ("name", "version", "field"),
    [
        ("", "1.0.0", "name"),
        ("memco-eve", "", "version"),
        ("memco eve", "1.0.0", "name"),
        ("memco-eve", "1.0/0", "version"),
        ("a" * 65, "1.0.0", "name"),
        ("memco-eve", None, "version"),
        ("memco+eve", "1.0.0", "name"),
    ],
    ids=[
        "blank name",
        "blank version",
        "space",
        "slash",
        "65 characters",
        "not a string",
        "plus in the name",
    ],
)
def test_a_malformed_integration_is_refused(name: str, version: str | None, field: str):
    with pytest.raises(errors.MemcoConfigError, match=f"integration {field} must be"):
        resolve(token="t", integration=Integration(name=name, version=version), env={})  # type: ignore[arg-type]


def test_an_integration_that_is_not_an_integration_is_refused():
    with pytest.raises(errors.MemcoConfigError, match="integration must be an Integration"):
        resolve(token="t", integration=("memco-eve", "0.2.0"), env={})  # type: ignore[arg-type]


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


@pytest.mark.parametrize("lifetime", [7200.0, True], ids=["float", "bool"])
def test_token_lifetime_must_be_a_whole_number_of_seconds(lifetime):
    # Refused here, typed, rather than by protobuf once the request is built.
    with pytest.raises(errors.MemcoConfigError, match="token_lifetime"):
        resolve(client_id="client-a", client_secret="secret-a", token_lifetime=lifetime, env={})


@pytest.mark.parametrize(
    ("given", "env"),
    [
        ({"token": "STATIC-TOKEN\udce9"}, {}),
        ({}, {"MEMCO_API_TOKEN": "STATIC-TOKEN\udce9"}),
        ({}, {"MEMCO_API_TOKEN": "STATIC-TOKEN\u00e9"}),
    ],
    ids=["lone surrogate argument", "lone surrogate in the environment", "non-ascii"],
)
def test_a_token_a_request_header_cannot_carry_is_refused_without_its_value(given, env):
    # A byte that is not UTF-8, read from the environment, arrives as a lone
    # surrogate; grpc would raise an encoding error holding the whole token.
    with pytest.raises(errors.MemcoConfigError) as caught:
        resolve(**given, env=env)
    assert "STATIC-TOKEN" not in str(caught.value)
    assert "STATIC-TOKEN" not in repr(caught.value.args)


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
