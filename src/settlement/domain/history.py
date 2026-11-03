"""Rebuild a record's state at any past moment from its audit events alone (SE-15c)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

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
    """Replay events up to `at`. None if nothing had happened yet.

    Events are replayed in the order the audit store wrote them (their sequence), not by
    timestamp: two events can share a timestamp, and their ids don't sort reliably.

    The whole history is checked first, not just the part up to `at`: a backdated event
    joins up with what came before it, and only the events after it expose it.
    """
    require_aware("at", at)
    for event in events:
        if event.sequence is None:
            raise InvalidModel(f"{event.event_id} has no sequence: only stored events replay")
    ordered = sorted(events, key=lambda event: event.sequence or 0)

    for position, event in enumerate(ordered, start=1):
        if event.sequence != position:
            raise InvalidModel(
                f"history has a gap: expected event #{position}, found {event.event_id} "
                f"as #{event.sequence}"
            )

    for earlier, later in pairwise(ordered):
        if later.occurred_at < earlier.occurred_at:
            raise InvalidModel(
                f"history goes back in time: {later.event_id} (#{later.sequence}) was written "
                f"after {earlier.event_id} (#{earlier.sequence}) but is dated before it"
            )
        if later.before != earlier.after:
            raise InvalidModel(
                f"history doesn't join up: {later.event_id} starts from {later.before!r}, "
                f"but {earlier.event_id} left it at {earlier.after!r}"
            )

    current: AuditEvent | None = None
    for event in ordered:
        if event.occurred_at > at:
            break  # this event is after the moment we're asking about: stop
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
