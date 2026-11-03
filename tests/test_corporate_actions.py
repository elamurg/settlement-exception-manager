"""Tests for corporate action notices (SE-16)."""

from datetime import date
from typing import Any

import pytest

from settlement.domain.corporate_actions import CorporateAction, CorporateActionType
from settlement.domain.errors import InvalidModel
from settlement.domain.identifiers import ISIN

OLD_ISIN = ISIN("DE0007164600")
NEW_ISIN = ISIN("GB00B03MLX29")
EFFECTIVE = date(2026, 10, 6)

SPLIT = CorporateActionType.SPLIT
REVERSE = CorporateActionType.REVERSE_SPLIT
BONUS = CorporateActionType.BONUS_ISSUE
ISIN_CHANGE = CorporateActionType.ISIN_CHANGE


def action(action_type: CorporateActionType, **fields: Any) -> CorporateAction:
    return CorporateAction(
        action_type=action_type, isin=OLD_ISIN, effective_date=EFFECTIVE, **fields
    )


@pytest.mark.parametrize(
    ("action_type", "fields"),
    [
        (SPLIT, {"new_shares": 2, "old_shares": 1}),
        (REVERSE, {"new_shares": 1, "old_shares": 10}),
        (BONUS, {"new_shares": 3, "old_shares": 2}),
        (ISIN_CHANGE, {"new_isin": NEW_ISIN}),
    ],
)
def test_valid_corporate_actions(action_type: CorporateActionType, fields: Any) -> None:
    assert action(action_type, **fields).action_type == action_type


@pytest.mark.parametrize(
    ("action_type", "fields", "message"),
    [
        (SPLIT, {"new_shares": 1, "old_shares": 2}, "leaves more shares"),  # the wrong way round
        (BONUS, {"new_shares": 2, "old_shares": 2}, "leaves more shares"),  # nothing changes
        (REVERSE, {"new_shares": 10, "old_shares": 1}, "leaves fewer shares"),
        (SPLIT, {"old_shares": 1}, "new_shares must be a whole number"),  # ratio missing
        (SPLIT, {"new_shares": 0, "old_shares": 1}, "new_shares must be a whole number"),
        (SPLIT, {"new_shares": True, "old_shares": 1}, "new_shares must be a whole number"),
        (SPLIT, {"new_shares": 2, "old_shares": 1, "new_isin": NEW_ISIN}, "keeps the same ISIN"),
        (ISIN_CHANGE, {}, "new_isin must be a ISIN"),
        (ISIN_CHANGE, {"new_isin": OLD_ISIN}, "needs a different new ISIN"),
        (ISIN_CHANGE, {"new_isin": NEW_ISIN, "new_shares": 2}, "doesn't change the number"),
    ],
)
def test_invalid_corporate_actions(
    action_type: CorporateActionType, fields: Any, message: str
) -> None:
    with pytest.raises(InvalidModel, match=message):
        action(action_type, **fields)


def test_isin_must_be_an_isin_not_text() -> None:
    with pytest.raises(InvalidModel, match="isin must be a ISIN"):
        CorporateAction(
            action_type=SPLIT,
            isin="DE0007164600",  # type: ignore[arg-type]
            effective_date=EFFECTIVE,
            new_shares=2,
            old_shares=1,
        )
