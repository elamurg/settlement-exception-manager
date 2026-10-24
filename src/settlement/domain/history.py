"""Rebuild a record's state at any past moment from its audit events alone (SE-15c)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from settlement.domain.errors import InvalidModel
from settlement.domain.models import AuditEvent, require_aware


@dataclass(frozen=True, slots=True)
class StateAsOf:
    """What a record looked like at a moment, and the event that made it so."""

    state: str  # e.g. "CONFIRMED"
    since: datetime  # when it got that state
    changed_by: str  # who changed it
    event_id: str  # the event that changed it


def state_as_of(events: Sequence[AuditEvent], at: datetime) -> StateAsOf | None:
    """Replay events up to `at`. None if nothing had happened yet."""
    require_aware("at", at)
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.event_id))

    current: AuditEvent | None = None
    for event in ordered:
        if event.occurred_at > at:
            break  # this event is after the moment we're asking about: stop
        if current is not None and event.before != current.after:
            raise InvalidModel(
                f"history doesn't join up: {event.event_id} starts from {event.before!r}, "
                f"but {current.event_id} left it at {current.after!r}"
            )
        current = event  # this event is the latest one so far

    if current is None:
        return None
    if current.after is None:
        raise InvalidModel(f"history ends with {current.event_id} leaving state None")
    return StateAsOf(
        state=current.after,  # the status this event changed it TO
        since=current.occurred_at,
        changed_by=current.actor,
        event_id=current.event_id,
    )
