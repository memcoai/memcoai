"""Surfacing the deprecation notice the service returns.

The service reports on every ``DescribeDomains`` whether what the caller is
using has been superseded — either the API version or this SDK build — and
supplies the remedy as text. Three rules govern how that is shown, and each
exists because breaking it makes the notice useless:

* **Once per process, not per call.** A busy client emitting this on every
  request becomes unusable and the notice gets filtered out wholesale, which is
  the opposite of the intent.
* **Verbatim.** The flag deliberately does not say which of the two causes
  applies; only the message does. Pattern-matching it or substituting text of
  our own would eventually tell someone to upgrade the SDK when their actual
  remedy is migrating to a new API version.
* **Never fatal.** This is advice. It must not refuse to build a client, raise,
  or block a call — the service keeps serving, and the caller decides when to
  act.
"""

from __future__ import annotations

import contextlib
import warnings
from datetime import date

__all__ = ["MemcoDeprecationWarning", "reset_warnings", "warn_once"]

# Messages already surfaced, so a repeat call stays quiet. Keyed on the text so
# that a message the service changes is shown again.
_seen: set[str] = set()


class MemcoDeprecationWarning(FutureWarning):
    """Something the caller uses has been superseded by the service.

    Subclasses :class:`FutureWarning` rather than :class:`DeprecationWarning`
    because Python hides the latter outside ``__main__`` — a notice raised from
    inside a framework or a service would reach nobody. Silence it with
    ``-W ignore::memco.MemcoDeprecationWarning``.
    """


def warn_once(message: str, sunset: date | None = None) -> None:
    """Surface a deprecation notice, at most once per message per process.

    Args:
        message: The service-authored remedy, relayed unchanged. Nothing is
            inferred from it and nothing is added beyond the sunset date.
        sunset: When what the caller uses stops working, if the service named a
            date.
    """
    if not message or message in _seen:
        return
    _seen.add(message)
    text = f"{message} (stops working on {sunset.isoformat()})" if sunset else message
    # A caller running with warnings escalated to errors would otherwise have a
    # working call broken by a notice about a future change. Losing the notice
    # is the lesser harm: deprecation is advice, and must never fail a call.
    with contextlib.suppress(Exception):
        warnings.warn(text, MemcoDeprecationWarning, stacklevel=4)


def reset_warnings() -> None:
    """Forget which notices have been surfaced.

    Only for tests: the once-per-process rule would otherwise let one test
    suppress another's notice.
    """
    _seen.clear()
