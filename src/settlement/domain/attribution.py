"""Three-way attribution: which leg a break is on.

Leg 1 is our book against our custodian's statement. Leg 2 is our instruction against the
counterparty's, as matched (or not) at the CSD and reported back in an MT548. Knowing the
leg tells ops who to contact: fix our own booking, or chase the counterparty.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from settlement.domain.errors import InvalidModel
from settlement.domain.models import (
    Break,
    BreakType,
    FieldDifference,
    Trade,
    require_aware,
    require_text,
)


class Leg(StrEnum):
    INTERNAL = "INTERNAL"  # leg 1: our book vs our custodian. We fix it.
    COUNTERPARTY = "COUNTERPARTY"  # leg 2: our instruction vs theirs at the CSD. Chase them.
    BOTH = "BOTH"  # both legs are broken. A human looks at both.
    UNKNOWN = "UNKNOWN"  # no CSD status received yet, so we can't tell


# A reason code: 4 capital letters or digits. E.g. "DQUA"
_REASON = re.compile(r"[A-Z0-9]{4}")


@dataclass(frozen=True, slots=True)
class CsdStatus:
    """The CSD's view of one of our instructions: did it match the counterparty's?

    The domain's own version of an MT548, so domain/ doesn't depend on ingestion/.
    """

    instruction_reference: str  # our instruction's reference (the MT541/543's SEME)
    matched: bool
    reason: str | None  # why it didn't match, e.g. "DQUA"; None when matched
    at: datetime | None = None

    def __post_init__(self) -> None:
        require_text("instruction_reference", self.instruction_reference)
        if self.at is not None:
            require_aware("at", self.at)
        if not isinstance(self.matched, bool):
            raise InvalidModel("matched must be True or False.")
        if self.matched and self.reason is not None:
            raise InvalidModel("A matched instruction can't have a reason.")
        if not self.matched and self.reason is None:
            raise InvalidModel("An unmatched instruction needs a reason.")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not _REASON.fullmatch(self.reason)
        ):
            raise InvalidModel(f"reason must be a 4-letter code, got {self.reason!r}")


def locate(book_custodian_break: bool, status: CsdStatus | None) -> Leg | None:
    """Which leg a break is on, or None if both legs are clean.

    With no CSD status yet, leg 2 can't be confirmed clean, so the answer is UNKNOWN even
    when book and custodian agree: under T+1, a missing status is worth knowing about.
    """
    if status is None:
        return Leg.UNKNOWN
    csd_break = not status.matched
    if book_custodian_break and csd_break:
        return Leg.BOTH
    if book_custodian_break:
        return Leg.INTERNAL  # custodian and CSD agree with the counterparty: our book is off
    if csd_break:
        return Leg.COUNTERPARTY  # we and our custodian agree: the counterparty differs
    return None


# MT548 reason codes, as the break types we already use. Codes not listed here have no
# equivalent yet; the caller should leave those for a human rather than guess.
REASON_BREAK_TYPES: Mapping[str, BreakType] = MappingProxyType(
    {
        "CMIS": BreakType.UNMATCHED_INSTRUCTION,  # counterparty's instruction is missing
        "DQUA": BreakType.QUANTITY_BREAK,  # quantities disagree
        "DDAT": BreakType.DATE_MISMATCH,  # settlement dates disagree
        "DMON": BreakType.PRICE_BREAK,  # settlement amounts disagree
        "DEPT": BreakType.SSI_MISMATCH,  # place of settlement / account disagrees
    }
)


def break_type_for_reason(reason: str) -> BreakType | None:
    """The break type an MT548 reason code means, or None if we don't know it."""
    return REASON_BREAK_TYPES.get(reason)


@dataclass(frozen=True, slots=True)
class ThreeWayResult:
    """What the CSD statuses add to a book vs custodian reconciliation."""

    legs: Mapping[str, Leg]  # break id -> leg, for every break including counterparty ones
    counterparty_breaks: tuple[Break, ...]  # clean on leg 1, but not matched at the CSD
    awaiting_status: tuple[str, ...]  # clean trade ids with no CSD status yet
    unrecognised: tuple[CsdStatus, ...]  # not matched, with a reason code we can't type
    stray_statuses: tuple[CsdStatus, ...]  # statuses for trades that aren't in the book
    conflicting_statuses: tuple[CsdStatus, ...]


def latest_statuses(
    statuses: Sequence[CsdStatus],
) -> tuple[dict[str, CsdStatus], tuple[CsdStatus, ...]]:
    """The latest status per instruction and the statusese can't choose between.
    The CSD sends a new MT548 each time an instruction's status changes, so several per
    instruction is normal. We never use the order they arrived in: files get merged and
    re-sent, so that order isn't a fact. Statuses we can't order go to a human."""
    by_instruction: dict[str, list[CsdStatus]] = {}
    for each in statuses:
        group = by_instruction.setdefault(each.instruction_reference, [])
        if each not in group:
            group.append(each)

    chosen: dict[str, CsdStatus] = {}
    conflicts: list[CsdStatus] = []
    for reference, group in by_instruction.items():
        latest = _latest(group)
        if latest is None:
            conflicts.extend(group)
        else:
            chosen[reference] = latest
    return chosen, tuple(conflicts)


def _latest(group: list[CsdStatus]) -> CsdStatus | None:
    """The newest of one instruction's statuses, or None if we can't tell which it is."""
    if len({(each.matched, each.reason) for each in group}) == 1:
        return group[0]  # they all say the same thing, so it doesn't matter which
    times = [each.at for each in group if each.at is not None]
    if len(times) < len(group):
        return None  # they disagree and some have no time: we can't order them
    newest = max(times)
    at_newest = [each for each in group if each.at == newest]
    return at_newest[0] if len(at_newest) == 1 else None


def attribute(
    breaks: Sequence[Break],
    clean_trades: Sequence[Trade],
    statuses: Sequence[CsdStatus],
    book_trade_ids: Collection[str],
    *,
    raised_at: datetime,
) -> ThreeWayResult:
    """Put every book vs custodian break on a leg, and raise the breaks only the CSD sees.

    `breaks` and `clean_trades` come from the two-way reconciliation. Trades it left
    unresolved are neither, so they get no leg here: a human is already looking at them.
    """
    known = set(book_trade_ids)
    status_for, conflicts = latest_statuses(statuses)
    conflicting = {each.instruction_reference for each in conflicts}

    legs: dict[str, Leg] = {}
    for brk in breaks:
        status = _status_for_break(brk, status_for)
        legs[brk.break_id] = locate(book_custodian_break=True, status=status) or Leg.UNKNOWN

    counterparty_breaks: list[Break] = []
    awaiting: list[str] = []
    unrecognised: list[CsdStatus] = []
    for trade in clean_trades:
        if trade.trade_id in conflicting:
            continue
        status = status_for.get(trade.trade_id)
        leg = locate(book_custodian_break=False, status=status)
        if leg is None:
            continue  # matched at the CSD too: clean on both legs
        if status is None:
            awaiting.append(trade.trade_id)
            continue
        break_type = break_type_for_reason(status.reason or "")
        if break_type is None:
            unrecognised.append(status)  # never guess what an unknown code means
            continue
        brk = _counterparty_break(trade, break_type, status, raised_at)
        counterparty_breaks.append(brk)
        legs[brk.break_id] = leg

    stray = [each for each in dict.fromkeys(statuses) if each.instruction_reference not in known]

    return ThreeWayResult(
        legs=MappingProxyType(legs),
        counterparty_breaks=tuple(sorted(counterparty_breaks, key=lambda b: b.break_id)),
        awaiting_status=tuple(sorted(awaiting)),
        unrecognised=tuple(sorted(unrecognised, key=lambda s: s.instruction_reference)),
        stray_statuses=tuple(sorted(stray, key=lambda s: s.instruction_reference)),
        conflicting_statuses=tuple(
            each for each in conflicts if each.instruction_reference in known
        ),
    )


def _status_for_break(brk: Break, status_for: Mapping[str, CsdStatus]) -> CsdStatus | None:
    """The CSD status of a break's instruction, or None if there isn't one yet.

    Our instruction is filed under our trade id, the same as for a clean trade. The line's
    reference is the custodian's echo of it and can be wrong, so it must not borrow another
    trade's status. The one exception is a duplicate booking: both bookings share one line
    and therefore one status, filed under the reference only one of them has.
    """
    if brk.ours is not None and brk.ours.trade_id in status_for:
        return status_for[brk.ours.trade_id]
    if brk.break_type == BreakType.DUPLICATE_BOOKING and brk.theirs is not None:
        return status_for.get(brk.theirs.reference)
    return None


def _counterparty_break(
    trade: Trade, break_type: BreakType, status: CsdStatus, raised_at: datetime
) -> Break:
    """A break the CSD reported: our side expected a match, the CSD says why it didn't."""
    evidence = FieldDifference("csd_status", ours="MACH", theirs=f"NMAT/{status.reason}")
    return Break(
        break_id=f"B-{break_type.value}-{trade.trade_id}",
        break_type=break_type,
        ours=trade,
        theirs=None,
        evidence=(evidence,),
        intended_settlement_date=trade.intended_settlement_date,
        raised_at=raised_at,
    )
