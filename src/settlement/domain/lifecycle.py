"""Documents legal transitions in stages, including partial settlement and failed path.
Every transition returns an AuditEvent.

The lifecycle:
booked -> allocated -> confirmed -> affirmed -> instructed ->

-> matched -> settled/partially_settled/failed
-> partially_settled -> settled/failed
-> failed -> resolved/cancelled

END PHASES: Settled, Resolved, Cancelled"""

from dataclasses import replace
from datetime import datetime

from settlement.domain.errors import IllegalTransition
from settlement.domain.models import AuditEvent, Trade, TradeStatus

_ALLOWED: dict[TradeStatus, frozenset[TradeStatus]] = {
    TradeStatus.BOOKED: frozenset({TradeStatus.ALLOCATED}),
    TradeStatus.ALLOCATED: frozenset({TradeStatus.CONFIRMED}),
    TradeStatus.CONFIRMED: frozenset({TradeStatus.AFFIRMED}),
    TradeStatus.AFFIRMED: frozenset({TradeStatus.INSTRUCTED}),
    TradeStatus.INSTRUCTED: frozenset({TradeStatus.MATCHED, TradeStatus.FAILED}),
    TradeStatus.MATCHED: frozenset(
        {TradeStatus.PARTIALLY_SETTLED, TradeStatus.SETTLED, TradeStatus.FAILED}
    ),
    TradeStatus.PARTIALLY_SETTLED: frozenset({TradeStatus.SETTLED, TradeStatus.FAILED}),
    TradeStatus.FAILED: frozenset({TradeStatus.RESOLVED, TradeStatus.CANCELLED}),
    TradeStatus.SETTLED: frozenset(),
    TradeStatus.CANCELLED: frozenset(),
    TradeStatus.RESOLVED: frozenset(),
}


def can_transition(current: TradeStatus, target: TradeStatus) -> bool:
    return target in _ALLOWED[current]


# returns tuple for 2 things at once, the * forces the caller to write the following
# parameters by name so two strings can't be swapped by accident
def transition(
    trade: Trade,
    target: TradeStatus,
    *,
    actor: str,
    at: datetime,
    event_id: str,
) -> tuple[Trade, AuditEvent]:
    if not can_transition(trade.status, target):
        raise IllegalTransition(
            f"Cannot move the trade {trade.trade_id} from {trade.status} to {target}"
        )

    new_trade = replace(trade, status=target)
    event = AuditEvent(
        event_id=event_id,
        entity_type="Trade",
        entity_id=trade.trade_id,
        event_type="STATUS_CHANGED",
        actor=actor,
        occurred_at=at,
        before=trade.status.value,
        after=target.value,
    )
    return new_trade, event
