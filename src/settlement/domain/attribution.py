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

from settlement.domain.errors import InvalidMatchInput, InvalidModel
from settlement.domain.models import Break, BreakType, FieldDifference, Trade, require_text


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

    def __post_init__(self) -> None:
        require_text("instruction_reference", self.instruction_reference)
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
REASON_BREAK_TYPES: dict[str, BreakType] = {
    "CMIS": BreakType.UNMATCHED_INSTRUCTION,  # counterparty's instruction is missing
    "DQUA": BreakType.QUANTITY_BREAK,  # quantities disagree
    "DDAT": BreakType.DATE_MISMATCH,  # settlement dates disagree
    "DMON": BreakType.PRICE_BREAK,  # settlement amounts disagree
    "DEPT": BreakType.SSI_MISMATCH,  # place of settlement / account disagrees
}


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
    status_for: dict[str, CsdStatus] = {}
    for each in statuses:
        if each.instruction_reference in status_for:
            raise InvalidMatchInput(
                f"Two CSD statuses for instruction {each.instruction_reference!r}"
            )
        status_for[each.instruction_reference] = each

    legs: dict[str, Leg] = {}
    for brk in breaks:
        reference = _instruction_reference(brk)
        status = status_for.get(reference) if reference is not None else None
        legs[brk.break_id] = locate(book_custodian_break=True, status=status) or Leg.UNKNOWN

    counterparty_breaks: list[Break] = []
    awaiting: list[str] = []
    unrecognised: list[CsdStatus] = []
    for trade in clean_trades:
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

    known = set(book_trade_ids)
    stray = [each for each in statuses if each.instruction_reference not in known]

    return ThreeWayResult(
        legs=MappingProxyType(legs),
        counterparty_breaks=tuple(sorted(counterparty_breaks, key=lambda b: b.break_id)),
        awaiting_status=tuple(sorted(awaiting)),
        unrecognised=tuple(sorted(unrecognised, key=lambda s: s.instruction_reference)),
        stray_statuses=tuple(sorted(stray, key=lambda s: s.instruction_reference)),
    )


def _instruction_reference(brk: Break) -> str | None:
    """Which instruction a break's CSD status is filed under.

    The custodian's line says which of our instructions it settled, so it wins when there is
    one: both bookings of a duplicate share one line and therefore one status.
    """
    if brk.theirs is not None:
        return brk.theirs.reference
    if brk.ours is not None:
        return brk.ours.trade_id
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
