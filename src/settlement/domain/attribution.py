"""Three-way attribution: which leg a break is on.

Leg 1 is our book against our custodian's statement. Leg 2 is our instruction against the
counterparty's, as matched (or not) at the CSD and reported back in an MT548. Knowing the
leg tells ops who to contact: fix our own booking, or chase the counterparty.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from settlement.domain.errors import InvalidModel
from settlement.domain.models import BreakType, require_text


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
