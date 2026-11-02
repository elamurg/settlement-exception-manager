"""Tests for the evaluation metrics (SE-17). Every expected number is worked out by hand."""

from decimal import Decimal

import pytest

from settlement.domain.controls import ReasonCode, amend, approve, reject
from settlement.domain.models import BreakType, Resolution, ResolutionAction
from settlement.evaluation.metrics import (
    Ratio,
    TypeAccuracy,
    acceptance_rate,
    classification_by_type,
    cost_per_break,
    false_break_rate,
    left_for_human_rate,
    match_rate,
    prioritisation_quality,
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


# Left for a human. Worked example:
#   should be left for a person:  T8 T9
#   we left unresolved:           T8      T3   (T3 should have been decided: counted elsewhere)
#   -> 1 / 2


def test_left_for_human_rate() -> None:
    assert left_for_human_rate({"T8", "T9"}, {"T8", "T3"}) == Ratio(1, 2)


def test_nothing_to_leave_for_a_human_has_no_rate() -> None:
    assert left_for_human_rate(set(), {"T3"}).value is None


# Classification. Worked example, (actual -> predicted) per trade:
#   T1  {SSI}           -> {SSI}        right
#   T2  {SSI}           -> {STATIC}     wrong type
#   T3  {QUANTITY}      -> {QUANTITY}   right
#   T4  {QUANTITY}      -> {}           missed
#   T5  {}              -> {PRICE}      false break
#   T6  {}              -> {}           clean, says nothing about any type
#   T7  {SSI, PRICE}    -> {SSI}        two real breaks, only one caught
#
#                    called  real  correct   precision  recall
#   SSI_MISMATCH        2      3      2        2/2        2/3     (T1, T7 called; T1, T2, T7 real)
#   STATIC_DATA         1      0      0        0/1        0/0
#   QUANTITY_BREAK      1      2      1        1/1        1/2
#   PRICE_BREAK         1      1      0        0/1        0/1     (T5 called, T7 real)

SSI = BreakType.SSI_MISMATCH
STATIC = BreakType.STATIC_DATA
QUANTITY = BreakType.QUANTITY_BREAK
PRICE = BreakType.PRICE_BREAK

OUTCOMES: list[tuple[set[BreakType], set[BreakType]]] = [
    ({SSI}, {SSI}),
    ({SSI}, {STATIC}),
    ({QUANTITY}, {QUANTITY}),
    ({QUANTITY}, set()),
    (set(), {PRICE}),
    (set(), set()),
    ({SSI, PRICE}, {SSI}),
]


def test_classification_by_type() -> None:
    assert classification_by_type(OUTCOMES) == {
        SSI: TypeAccuracy(precision=Ratio(2, 2), recall=Ratio(2, 3)),
        STATIC: TypeAccuracy(precision=Ratio(0, 1), recall=Ratio(0, 0)),
        QUANTITY: TypeAccuracy(precision=Ratio(1, 1), recall=Ratio(1, 2)),
        PRICE: TypeAccuracy(precision=Ratio(0, 1), recall=Ratio(0, 1)),
    }


def test_types_that_never_appear_are_left_out() -> None:
    assert BreakType.DATE_MISMATCH not in classification_by_type(OUTCOMES)


# Prioritisation. Worked example: the ideal queue is A, B, C (A most urgent).
#   ours A, C, B:  A<B yes, A<C yes, B<C no  -> 2 / 3


@pytest.mark.parametrize(
    ("ranked", "expected"),
    [
        (["A", "C", "B"], Ratio(2, 3)),
        (["A", "B", "C"], Ratio(3, 3)),  # the ideal order
        (["C", "B", "A"], Ratio(0, 3)),  # reversed: every pair wrong
    ],
)
def test_prioritisation_quality(ranked: list[str], expected: Ratio) -> None:
    assert prioritisation_quality(ranked, ["A", "B", "C"]) == expected


def test_one_break_has_no_pairs_to_order() -> None:
    assert prioritisation_quality(["A"], ["A"]).value is None


@pytest.mark.parametrize(
    "ranked",
    [
        ["A", "B"],  # C is missing
        ["A", "B", "C", "D"],  # D isn't in the ideal
        ["A", "B", "B"],  # B twice
    ],
)
def test_queues_must_hold_the_same_breaks(ranked: list[str]) -> None:
    with pytest.raises(ValueError, match="same break ids"):
        prioritisation_quality(ranked, ["A", "B", "C"])


# Acceptance. Worked example, each built with the real SE-14 controls:
#   R1  approved as proposed           accepted
#   R2  rejected                       not accepted
#   R3  amended, waiting for approval  not accepted
#   R4  amended, then approved         not accepted: ends APPROVED, but someone changed it
#   R5  still proposed                 left out: nobody has acted on it
#   -> 1 / 4


def proposal(resolution_id: str) -> Resolution:
    return Resolution(
        resolution_id=resolution_id,
        break_id=f"B-{resolution_id}",
        action=ResolutionAction.AMEND_SSI,
        rationale="Custodian has the old safekeeping account.",
        proposed_by="agent",
    )


def amended(resolution_id: str) -> Resolution:
    return amend(
        proposal(resolution_id),
        amended_by="tina",
        action=ResolutionAction.REINSTRUCT,
        rationale="Re-instruct with the corrected SSI.",
        reason_code=ReasonCode.WRONG_ACTION,
    )


def test_acceptance_rate() -> None:
    resolutions = [
        approve(proposal("R1"), approver="tina"),
        reject(proposal("R2"), checker="tina", reason_code=ReasonCode.WRONG_ACTION),
        amended("R3"),
        approve(amended("R4"), approver="ela"),
        proposal("R5"),
    ]

    assert acceptance_rate(resolutions) == Ratio(1, 4)


def test_nothing_acted_on_yet_has_no_acceptance_rate() -> None:
    assert acceptance_rate([proposal("R1")]).value is None


# Cost. Worked examples:
#   0.30 / 4 = 0.075    -> 0.0750
#   1.00 / 3 = 0.33333… -> 0.3333
#   0.00025 / 1         -> 0.0003   (exactly half: rounds up, not to the even 0.0002)


@pytest.mark.parametrize(
    ("total", "breaks", "expected"),
    [
        (Decimal("0.30"), 4, Decimal("0.0750")),
        (Decimal("1.00"), 3, Decimal("0.3333")),
        (Decimal("0.00025"), 1, Decimal("0.0003")),
    ],
)
def test_cost_per_break(total: Decimal, breaks: int, expected: Decimal) -> None:
    assert cost_per_break(total, breaks) == expected


def test_no_breaks_has_no_cost_per_break() -> None:
    assert cost_per_break(Decimal("0.30"), 0) is None


@pytest.mark.parametrize(
    "total",
    [0.30, Decimal("-0.01"), Decimal("NaN")],  # a float, negative, not a number
)
def test_cost_must_be_a_finite_non_negative_decimal(total: object) -> None:
    with pytest.raises(ValueError, match="finite, non-negative Decimal"):
        cost_per_break(total, 4)  # type: ignore[arg-type]
