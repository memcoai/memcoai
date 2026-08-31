"""The SDK's logger tree and the level it runs at.

Everything this SDK logs goes to a logger under ``memco``: each module writes to
``logging.getLogger(__name__)``, which makes ``memco._sync``, ``memco._channel``
and the rest children of one tree. Configuring ``memco`` therefore governs all
of them, while a caller who wants to quieten a single noisy area still can.

The SDK configures itself at ``INFO`` when it is imported. It sets the level,
attaches its own stderr handler, and stops the tree propagating so a record is
not also printed by whatever the application put on the root logger. At that
level it says one line when a client connects and one when it closes, and
reports a rejected credential. The detail — every RPC with its outcome and
duration, where the credential and endpoint came from, and any list the service
trimmed — is a level below, at ``DEBUG``.

``MEMCO_LOG`` changes that level, and ``none`` turns it off entirely. The
``log_level`` argument on either client does the same from code, and wins over
the variable because an argument always does.

The cost of configuring itself is that cutting propagation takes the SDK's
records out of the application's handlers, redaction filters and log shipping
included. An application with its own logging should therefore silence the SDK's
handler — ``MEMCO_LOG=none``, or ``log_level="none"`` — and set the level on the
``memco`` logger itself, which leaves the records propagating as usual. Both
settings are process-wide besides, because a logger is: two clients asking for
different levels means the last one constructed decides.

The :class:`logging.NullHandler` attached alongside is what makes ``none`` mean
silence rather than noise: without a handler Python falls back to
``logging.lastResort``, which prints WARNING and above straight to stderr.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
import warnings
from collections.abc import Mapping
from typing import TextIO

from .errors import MemcoConfigError

__all__ = [
    "DEFAULT_LEVEL",
    "LOG_ENV",
    "ROOT",
    "configure",
    "elapsed_ms",
    "rpc_name",
    "set_level",
]

LOG_ENV = "MEMCO_LOG"
"""Environment variable that turns SDK logging on and sets its level."""

ROOT = "memco"
"""Logger every SDK record is written under, as a child of this name."""

DEFAULT_LEVEL = "info"
"""Level the SDK configures itself at when ``MEMCO_LOG`` does not name one."""

_LEVELS = {
    "critical": logging.CRITICAL,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

_SILENCE = "none"

# Above CRITICAL, so every standard level is dropped. Preferred to `disabled`,
# which logging.config resets and which a caller cannot lift by setting a level.
_SILENT = logging.CRITICAL + 1

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


class _MemcoStreamHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever ``sys.stderr`` is bound at the time of the record.

    Marks the handler this module installed, so reconfiguring replaces the SDK's
    own and never touches one the application attached.

    A plain :class:`logging.StreamHandler` captures ``sys.stderr`` when it is
    constructed, which here is during ``import memco``. A process that
    daemonizes, redirects, or reopens its error stream afterwards would have
    every record still going to the original one — or to a closed descriptor.
    Resolving the stream per record, the way :class:`logging._StderrHandler`
    does, is what avoids that.
    """

    def __init__(self) -> None:
        # Handler.__init__ rather than StreamHandler's: leaving ``stream``
        # unassigned is what lets the property below answer for it.
        logging.Handler.__init__(self)

    @property
    def stream(self) -> TextIO:
        """The error stream as it is now, not as it was at import."""
        return sys.stderr


def configure(env: Mapping[str, str] | None = None) -> None:
    """Configure the SDK's logging, from ``MEMCO_LOG`` or the default level.

    Called once when :mod:`memco` is imported. Safe to call again: the SDK's own
    handler is replaced rather than stacked, so repeated calls cannot duplicate
    output.

    Args:
        env: Environment mapping to read from. Defaults to :data:`os.environ`;
            supplying one is mainly useful in tests.
    """
    logger = logging.getLogger(ROOT)
    if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())

    setting = (os.environ if env is None else env).get(LOG_ENV, "").strip().lower()
    if not setting:
        set_level(DEFAULT_LEVEL)
        return

    try:
        set_level(setting)
    except MemcoConfigError as exc:
        # An environment variable is ambient: a typo in one must never break a
        # program, and must never leave the SDK louder or quieter than asked.
        # An argument is the caller's own code, so set_level raises there.
        # Suppressed for the same reason _deprecation.warn_once suppresses:
        # this runs during `import memco`, so under `-W error` the warning
        # itself would become the ImportError it exists to prevent.
        with contextlib.suppress(Exception):
            warnings.warn(f"{LOG_ENV} ignored: {exc}", UserWarning, stacklevel=2)
        set_level(DEFAULT_LEVEL)


def set_level(level: str | int) -> None:
    """Turn SDK logging on at a level, and attach the SDK's own stderr handler.

    Process-wide, since a logger is. This is what ``MEMCO_LOG`` and the clients'
    ``log_level`` argument both go through.

    Args:
        level: A level name — ``"debug"``, ``"info"``, ``"warning"``,
            ``"error"``, ``"critical"``, or ``"none"`` to silence the SDK — or a
            :mod:`logging` level constant.

    Raises:
        MemcoConfigError: If the level is not one of those names.
    """
    logger = logging.getLogger(ROOT)
    resolved = _resolve(level)
    if _detach(logger):
        # Only undo what this module did. An application that turned propagation
        # off itself keeps its setting, since there is nothing here to restore.
        logger.propagate = True
    logger.setLevel(resolved)
    if resolved > logging.CRITICAL:
        return
    handler = _MemcoStreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    # The handler is ours, so the record must not also reach whatever the
    # application attached to the root logger: that would print it twice.
    logger.propagate = False


def _resolve(level: str | int) -> int:
    """Turn a level name or constant into a numeric level.

    Args:
        level: A level name, or a :mod:`logging` constant. Any positive
            integer is passed through, so a custom level keeps working; zero and
            below are refused, since they read as "off" and are not.

    Returns:
        The numeric level.

    Raises:
        MemcoConfigError: If the name is not one this SDK accepts.
    """
    if isinstance(level, bool):
        # bool is an int, so True would mean level 1 and turn everything on. A
        # caller passing one meant a switch, and there isn't one.
        raise MemcoConfigError(f"{level!r} is not a log level; pass a name or a logging constant")
    if isinstance(level, int):
        if level <= 0:
            # NOTSET is 0, which attaches the handler and then defers to the
            # root logger's level: `log_level=0` reads as off and is not.
            raise MemcoConfigError(f'{level!r} is not a log level; use "none" to silence the SDK')
        return level
    name = level.strip().lower()
    if name == _SILENCE:
        return _SILENT
    resolved = _LEVELS.get(name)
    if resolved is None:
        raise MemcoConfigError(
            f"{level!r} is not a log level; use one of "
            f"{', '.join(sorted(_LEVELS))}, or none to silence"
        )
    return resolved


def _detach(logger: logging.Logger) -> bool:
    """Remove the handler an earlier call to :func:`set_level` installed.

    Args:
        logger: The SDK's root logger.

    Returns:
        Whether a handler was removed — and so whether this module, rather than
        the application, is the one that turned propagation off.
    """
    # A list rather than one: two threads constructing clients with log_level
    # can interleave and attach two, and a handler left behind would double
    # every record for the life of the process.
    installed = [h for h in logger.handlers if isinstance(h, _MemcoStreamHandler)]
    for handler in installed:
        logger.removeHandler(handler)
        handler.close()
    return bool(installed)


def rpc_name(request: object) -> str:
    """Name the RPC a request belongs to, for a log record.

    Args:
        request: The protobuf request message being sent.

    Returns:
        The message type without its ``Request`` suffix, so a record reads
        ``ListDomains ok in 8ms`` rather than ``ListDomainsRequest ok in 8ms``.
    """
    return type(request).__name__.removesuffix("Request")


def elapsed_ms(started: float) -> float:
    """Return the milliseconds since a :func:`time.perf_counter` reading.

    Args:
        started: The reading taken when the work began.

    Returns:
        Elapsed time in milliseconds, for a ``%.0fms`` log record.
    """
    return (time.perf_counter() - started) * 1000.0
