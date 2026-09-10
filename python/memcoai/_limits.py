"""Applying the input limits the service reports.

The service owns these numbers and delivers them on ``ListDomains``. An SDK
carrying its own copy would go stale the moment the service changed one: an
older client would keep rejecting requests the service had started accepting,
locally, with no way for the caller to tell why. So nothing here has a default —
a client that has not been told a limit does not check it, and the service
rules.

Two of the caps **trim** rather than refuse. The service keeps the first N and
drops the rest, so raising would reject a call it would have accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import DomainEntry, Limits

__all__ = ["Known"]


@dataclass
class Known:
    """What the service has told this client about its own limits.

    Empty until a ``ListDomains`` response supplies it, and refreshed by
    every subsequent one.

    Attributes:
        limits: The service-wide caps, or ``None`` if none have been reported.
        tags_per_domain: The per-domain tag cap, by domain slug. Absent or zero
            means that domain sets no cap.
    """

    limits: Limits | None = None
    tags_per_domain: dict[str, int] = field(default_factory=dict)

    def update(self, limits: Limits | None, domains: tuple[DomainEntry, ...]) -> None:
        """Record what a ``ListDomains`` response reported.

        Args:
            limits: The service-wide caps the response carried, if any.
            domains: The domains it described, each carrying its own tag cap.
        """
        if limits is not None:
            self.limits = limits
        for domain in domains:
            self.tags_per_domain[domain.slug] = domain.max_tags_per_query

    def max_tags(self, domain: str | None) -> int:
        """Return the tag cap for a domain.

        Args:
            domain: The domain being named, or ``None`` when the call is scoped
                by a session instead.

        Returns:
            The cap, or ``0`` for no cap — which is also the answer when the
            domain is unknown or the call names a session, since the session's
            domain is not visible to the client.
        """
        return self.tags_per_domain.get(domain or "", 0)
