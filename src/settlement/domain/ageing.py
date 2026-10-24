"""Prioritisation of older breaks to be resolved sooned,
or more expensive breaks if two have the same deadline."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

from settlement.domain.models import Break, require_aware
from settlement.domain.penalties import business_days, round_money

DEFAULT_CUTOFF = time(16, 0)
DEFAULT_CRITICAL_WINDOW = timedelta(hours=4)


class Band(StrEnum):
    OVERDUE = "OVERDUE"
    CRITICAL = "CRITICAL"
    TODAY = "TODAY"
    NEXT_DAY = "NEXT_DAY"
    LATER = "LATER"


@dataclass(frozen=True, slots=True)
class PrioritisedBreak:
    """A break, with everything that decided its place in the queue."""

    brk: Break
    band: Band
    deadline: datetime
    time_left: timedelta
    business_days_left: int
    daily_exposure: Decimal


def deadline_for(intended_settlement_date: date, cuttoff: time = DEFAULT_CUTOFF) -> datetime:
    """The last moment the. trade can still settle: the cut-off on its settlement date, in UTC."""
    return datetime.combine(intended_settlement_date, cuttoff, tzinfo=UTC)


def band_for(
    deadline: datetime,
    now: datetime,
    holidays: frozenset[date],
    critical_window: timedelta = DEFAULT_CRITICAL_WINDOW,
) -> Band:
    """How urgent a break is, from how close its deadline is."""
    if deadline <= now:
        return Band.OVERDUE
    time_left = deadline - now
    if time_left <= critical_window:
        return Band.CRITICAL
    if deadline.date() == now.date():
        return Band.TODAY
    days_away = len(business_days(now.date(), deadline.date(), holidays))
    if days_away <= 1:
        return Band.NEXT_DAY
    return Band.LATER


def daily_exposure(brk: Break, daily_rate: Decimal) -> Decimal:
    """Estimated penalty per day if the break makes the trade fail: failed amount x rate."""
    if brk.ours is not None:
        amount = brk.ours.consideration
    elif brk.theirs is not None:
        amount = brk.theirs.consideration
    else:
        amount = Decimal("0")
    return round_money(amount * daily_rate)


_BAND_ORDER: Mapping[Band, int] = {
    Band.OVERDUE: 0,
    Band.CRITICAL: 1,
    Band.TODAY: 2,
    Band.NEXT_DAY: 3,
    Band.LATER: 4,
}


def prioritise(
    breaks: Sequence[Break],
    *,
    now: datetime,
    holidays: frozenset[date],
    daily_rate: Decimal,
    cutoff: time = DEFAULT_CUTOFF,
    critical_window: timedelta = DEFAULT_CRITICAL_WINDOW,
) -> list[PrioritisedBreak]:
    """Every break in the order to work on it (urgent first)"""
    require_aware("now", now)
    items: list[PrioritisedBreak] = []
    for brk in breaks:
        deadline = deadline_for(brk.intended_settlement_date, cutoff)
        band = band_for(deadline, now, holidays, critical_window)
        time_left = deadline - now
        days_left = len(business_days(now.date(), deadline.date(), holidays))
        exposure = daily_exposure(brk, daily_rate)
        items.append(
            PrioritisedBreak(
                brk=brk,
                band=band,
                deadline=deadline,
                time_left=time_left,
                business_days_left=days_left,
                daily_exposure=exposure,
            )
        )
    return sorted(
        items,
        key=lambda item: (
            _BAND_ORDER[item.band],
            item.time_left,
            -item.daily_exposure,
            item.brk.break_id,
        ),
    )
