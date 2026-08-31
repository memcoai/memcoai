"""The SDK's logger tree, its default level, and the ways to change it."""

from __future__ import annotations

import contextlib
import io
import logging

import pytest

from memco import AsyncMemco, Memco, _logging, errors


def stream_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if isinstance(h, _logging._MemcoStreamHandler)]


# --- the default ---------------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"MEMCO_LOG": "   "}])
def test_the_default_is_info_on_stderr(env):
    logger = logging.getLogger(_logging.ROOT)
    _logging.configure(env)
    assert logger.level == logging.INFO
    assert len(stream_handlers(logger)) == 1
    # The handler is ours, so a record must not also reach the application's.
    assert logger.propagate is False


def test_a_client_reports_connecting_and_closing_at_the_default_level(harness, caplog):
    with (
        caplog.at_level(logging.INFO, logger="memco"),
        Memco(token="test-token", host=harness.address, tls=False),
    ):
        pass
    assert "connected to" in caplog.text
    assert "closed connection to" in caplog.text
    # A credential must never reach a log.
    assert "test-token" not in caplog.text


# --- MEMCO_LOG -----------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "expected"), [("debug", logging.DEBUG), ("error", logging.ERROR)]
)
def test_the_variable_overrides_the_default(setting, expected):
    _logging.configure({"MEMCO_LOG": setting})
    assert logging.getLogger(_logging.ROOT).level == expected


def test_the_value_tolerates_case_and_whitespace():
    _logging.configure({"MEMCO_LOG": "  DeBuG \n"})
    assert logging.getLogger(_logging.ROOT).level == logging.DEBUG


def test_none_stops_every_record(capsys):
    logger = logging.getLogger(_logging.ROOT)
    _logging.configure({"MEMCO_LOG": "none"})
    logging.getLogger("memco._sync").critical("this must not appear")
    assert logger.level > logging.CRITICAL
    assert stream_handlers(logger) == []
    assert capsys.readouterr().err == ""


def test_an_unrecognised_value_warns_and_falls_back_to_the_default():
    logger = logging.getLogger(_logging.ROOT)
    with pytest.warns(UserWarning, match="MEMCO_LOG"):
        _logging.configure({"MEMCO_LOG": "verbose"})
    # A typo in a debug knob must never break a program, and must leave the SDK
    # exactly where it would have been without it.
    assert logger.level == logging.INFO


def test_the_variable_is_read_from_the_real_environment(monkeypatch):
    monkeypatch.setenv("MEMCO_LOG", "error")
    _logging.configure()
    assert logging.getLogger(_logging.ROOT).level == logging.ERROR


def test_records_carry_the_module_and_reach_stderr_once(capsys):
    _logging.configure({"MEMCO_LOG": "debug"})
    logging.getLogger("memco._sync").debug("dialling %s", "example:443")
    err = capsys.readouterr().err
    assert err.count("dialling example:443") == 1
    # Which module spoke is the point of the tree, and of the format string.
    assert "memco._sync" in err


# --- set_level, and the clients' log_level argument ----------------------


def test_set_level_accepts_a_name_and_a_constant():
    logger = logging.getLogger(_logging.ROOT)
    _logging.set_level("warning")
    assert logger.level == logging.WARNING
    _logging.set_level(logging.INFO)
    assert logger.level == logging.INFO
    assert len(stream_handlers(logger)) == 1


def test_set_level_refuses_a_level_it_does_not_know():
    # An argument is the caller's own code, so unlike a stray MEMCO_LOG this is
    # a bug worth raising for rather than warning about.
    with pytest.raises(errors.MemcoConfigError, match="not a log level"):
        _logging.set_level("verbose")


@pytest.mark.parametrize("level", [True, 0, -5])
def test_a_level_that_reads_as_off_but_is_not_is_refused(level):
    # bool is an int, so True would mean level 1 and turn everything on; NOTSET
    # would attach the handler and then defer to the root logger's level. Both
    # look like "off" to a caller and are the opposite.
    with pytest.raises(errors.MemcoConfigError, match="not a log level"):
        _logging.set_level(level)


def test_the_client_argument_turns_logging_on(harness, capsys):
    with Memco(token="test-token", host=harness.address, tls=False, log_level="debug"):
        pass
    err = capsys.readouterr().err
    # Applied before the endpoint is resolved, so the records about where the
    # credential and host came from are inside the window, not before it.
    assert "credential taken from the token argument" in err
    assert "test-token" not in err


async def test_the_async_client_takes_the_same_argument(harness, capsys):
    async with AsyncMemco(token="test-token", host=harness.address, tls=False, log_level="debug"):
        pass
    err = capsys.readouterr().err
    assert "memco._aio" in err
    assert "test-token" not in err


def test_the_argument_wins_over_the_environment(harness):
    _logging.configure({"MEMCO_LOG": "error"})
    with Memco(token="test-token", host=harness.address, tls=False, log_level="debug"):
        pass
    assert logging.getLogger(_logging.ROOT).level == logging.DEBUG


def test_the_client_argument_can_silence_the_sdk(harness, capsys):
    with Memco(token="test-token", host=harness.address, tls=False, log_level="none"):
        pass
    assert capsys.readouterr().err == ""


def test_a_bad_client_argument_is_a_config_error(harness):
    with pytest.raises(errors.MemcoConfigError, match="not a log level"):
        Memco(token="test-token", host=harness.address, tls=False, log_level="loud")


# --- state the SDK must not trample --------------------------------------


def test_an_application_keeps_its_own_propagate_setting():
    # The SDK only undoes what the SDK did. Silencing removes no handler here,
    # so an application that isolated the memco tree keeps that isolation.
    logger = logging.getLogger(_logging.ROOT)
    logger.propagate = False
    _logging.set_level("none")
    assert logger.propagate is False


def test_configuring_twice_does_not_stack_handlers():
    logger = logging.getLogger(_logging.ROOT)
    _logging.configure({"MEMCO_LOG": "debug"})
    before = len(logger.handlers)
    _logging.configure({"MEMCO_LOG": "info"})
    assert len(logger.handlers) == before
    assert len(stream_handlers(logger)) == 1
    assert logger.level == logging.INFO


def test_every_handler_this_module_installed_is_cleared():
    # Two threads constructing clients with log_level can interleave inside
    # set_level and leave a second handler attached; one left behind would
    # double every record for the life of the process.
    logger = logging.getLogger(_logging.ROOT)
    _logging.set_level("debug")
    logger.addHandler(_logging._MemcoStreamHandler())
    assert len(stream_handlers(logger)) == 2
    _logging.set_level("info")
    assert len(stream_handlers(logger)) == 1


def test_the_handler_follows_a_later_stderr_redirect():
    # The handler is built during `import memco`, long before a process that
    # daemonizes or redirects has done so. Binding the stream then would send
    # every record to the original one, or to a closed descriptor.
    _logging.set_level("debug")
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        logging.getLogger("memco._sync").debug("after the redirect")
    assert "after the redirect" in captured.getvalue()
