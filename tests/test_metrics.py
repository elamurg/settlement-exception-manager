"""Tests for the evaluation metrics (SE-17). Every expected number is worked out by hand."""

import pytest

from settlement.domain.models import BreakType
from settlement.evaluation.metrics import (
    Ratio,
    TypeAccuracy,
    classification_by_type,
    false_break_rate,
    match_rate,
)


def test_ratio_value_is_count_over_total() -> None:
    assert Ratio(3, 4).value == 0.75


def test_ratio_with_nothing_to_count_has_no_value() -> None:
    """Zero clean trades means no match rate at all, not a match rate of 0%."""
    assert Ratio(0, 0).value is None


@pytest.mark.parametrize(("count", "total"), [(5, 4), (-1, 4)])
def test_ratio_count_must_fit_in_total(count: int, total: int) -> None:
    with pytest.raises(ValueError, match="between 0 and total"):
        Ratio(count, total)


# Reconciliation. Worked example:
#   should be clean:  T1 T2 T3 T4
#   we said clean:    T1 T2 T3       T9   (T9 really had a break: a miss, not counted here)
#   we broke:                  T4 T5      (T4 is a false break; T5 really had one)
#   match rate = 3 / 4, false break rate = 1 / 4

EXPECTED_CLEAN = {"T1", "T2", "T3", "T4"}


def test_match_rate() -> None:
    assert match_rate(EXPECTED_CLEAN, {"T1", "T2", "T3", "T9"}) == Ratio(3, 4)


def test_false_break_rate() -> None:
    assert false_break_rate(EXPECTED_CLEAN, {"T4", "T5"}) == Ratio(1, 4)


def test_rates_with_no_clean_trades_have_no_value() -> None:
    assert match_rate(set(), {"T1"}).value is None
    assert false_break_rate(set(), {"T1"}).value is None


# Classification. Worked example, (actual -> predicted) per trade:
#   T1  SSI_MISMATCH   -> SSI_MISMATCH     right
#   T2  SSI_MISMATCH   -> STATIC_DATA      wrong type
#   T3  QUANTITY_BREAK -> QUANTITY_BREAK   right
#   T4  QUANTITY_BREAK -> (none)           missed
#   T5  (none)         -> PRICE_BREAK      false break
#   T6  (none)         -> (none)           clean, says nothing about any type
#
#                    called  real  correct   precision  recall
#   SSI_MISMATCH        1      2      1        1/1        1/2
#   STATIC_DATA         1      0      0        0/1        0/0
#   QUANTITY_BREAK      1      2      1        1/1        1/2
#   PRICE_BREAK         1      0      0        0/1        0/0

SSI = BreakType.SSI_MISMATCH
STATIC = BreakType.STATIC_DATA
QUANTITY = BreakType.QUANTITY_BREAK
PRICE = BreakType.PRICE_BREAK

OUTCOMES = [
    (SSI, SSI),
    (SSI, STATIC),
    (QUANTITY, QUANTITY),
    (QUANTITY, None),
    (None, PRICE),
    (None, None),
]


def test_classification_by_type() -> None:
    assert classification_by_type(OUTCOMES) == {
        SSI: TypeAccuracy(precision=Ratio(1, 1), recall=Ratio(1, 2)),
        STATIC: TypeAccuracy(precision=Ratio(0, 1), recall=Ratio(0, 0)),
        QUANTITY: TypeAccuracy(precision=Ratio(1, 1), recall=Ratio(1, 2)),
        PRICE: TypeAccuracy(precision=Ratio(0, 1), recall=Ratio(0, 0)),
    }


def test_types_that_never_appear_are_left_out() -> None:
    assert BreakType.DATE_MISMATCH not in classification_by_type(OUTCOMES)
