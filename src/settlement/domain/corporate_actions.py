"""Corporate actions: a company changing its shares, announced in advance (SE-16).

Ops learn about these from a notice, not by spotting odd numbers: a split makes a correct
position look exactly like a quantity break. The notice is the evidence.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from settlement.domain.errors import InvalidModel
from settlement.domain.identifiers import ISIN
from settlement.domain.models import require_type


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"  # more shares, each worth less: e.g. 2 for 1
    REVERSE_SPLIT = "REVERSE_SPLIT"  # fewer shares, each worth more: e.g. 1 for 10
    BONUS_ISSUE = "BONUS_ISSUE"  # free extra shares: e.g. 3 for 2
    ISIN_CHANGE = "ISIN_CHANGE"  # the same holding, re-issued under a new ISIN


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One announced corporate action on one security.

    A ratio is two whole numbers, not a decimal, so 3 for 2 stays exact.
    """

    action_type: CorporateActionType
    isin: ISIN
    effective_date: date
    new_shares: int | None = None  # for every `old_shares` held, a holder now has this many
    old_shares: int | None = None
    new_isin: ISIN | None = None  # ISIN_CHANGE only

    def __post_init__(self) -> None:
        require_type("action_type", self.action_type, CorporateActionType)
        require_type("isin", self.isin, ISIN)
        require_type("effective_date", self.effective_date, date)

        if self.action_type == CorporateActionType.ISIN_CHANGE:
            require_type("new_isin", self.new_isin, ISIN)
            if self.new_isin == self.isin:
                raise InvalidModel("An ISIN change needs a different new ISIN.")
            if self.new_shares is not None or self.old_shares is not None:
                raise InvalidModel("An ISIN change doesn't change the number of shares.")
            return

        new = _require_count("new_shares", self.new_shares)
        old = _require_count("old_shares", self.old_shares)
        if self.new_isin is not None:
            raise InvalidModel(f"A {self.action_type.value} keeps the same ISIN.")
        if self.action_type == CorporateActionType.REVERSE_SPLIT:
            if new >= old:
                raise InvalidModel("A REVERSE_SPLIT leaves fewer shares: new_shares < old_shares.")
        elif new <= old:
            raise InvalidModel(
                f"A {self.action_type.value} leaves more shares: new_shares > old_shares."
            )


def _require_count(field_name: str, value: object) -> int:
    # bool is a subclass of int, so True would otherwise pass as 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidModel(f"{field_name} must be a whole number, 1 or more.")
    return value
