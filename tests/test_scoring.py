"""Tests for scoring a run (SE-18). Every expected number is worked out by hand."""

from settlement.domain.models import BreakType
from settlement.evaluation.metrics import Ratio, TypeAccuracy
from settlement.evaluation.outcomes import Outcomes, combine, frozen
from settlement.evaluation.scoring import describe, passed, score

SSI = BreakType.SSI_MISMATCH
QUANTITY = BreakType.QUANTITY_BREAK
PRICE = BreakType.PRICE_BREAK


def outcomes(
    clean: set[str] | None = None,
    broken: dict[str, set[BreakType]] | None = None,
    unresolved: set[str] | None = None,
) -> Outcomes:
    return Outcomes(
        clean=frozenset(clean or set()),
        broken=frozen(broken or {}),
        unresolved=frozenset(unresolved or set()),
    )


# Worked example, expected -> actual:
#   a  clean        -> clean
#   b  clean        -> clean
#   c  clean        -> PRICE        a false break
#   d  SSI          -> SSI          right
#   e  QUANTITY     -> unresolved   missed
#   f  unresolved   -> unresolved   rightly left for a human
#
#   match 2/3, false break 1/3, left for a human 1/1
#   SSI precision 1/1 recall 1/1, QUANTITY 0/0 and 0/1, PRICE 0/1 and 0/0

EXPECTED = outcomes(clean={"a", "b", "c"}, broken={"d": {SSI}, "e": {QUANTITY}}, unresolved={"f"})
ACTUAL = outcomes(clean={"a", "b"}, broken={"c": {PRICE}, "d": {SSI}}, unresolved={"e", "f"})


def test_score() -> None:
    scores = score(EXPECTED, ACTUAL)

    assert scores.match_rate == Ratio(2, 3)
    assert scores.false_break_rate == Ratio(1, 3)
    assert scores.left_for_human_rate == Ratio(1, 1)
    assert scores.by_type == {
        SSI: TypeAccuracy(precision=Ratio(1, 1), recall=Ratio(1, 1)),
        QUANTITY: TypeAccuracy(precision=Ratio(0, 0), recall=Ratio(0, 1)),
        PRICE: TypeAccuracy(precision=Ratio(0, 1), recall=Ratio(0, 0)),
    }


def test_combine_keeps_every_part() -> None:
    combined = combine([outcomes(clean={"x/T-1"}), outcomes(broken={"y/T-1": {SSI}})])

    assert combined.records == {"x/T-1", "y/T-1"}


def test_a_case_passes_when_every_answer_it_gives_comes_out() -> None:
    expected = outcomes(clean={"c1/T-1"})
    actual = outcomes(clean={"c1/T-1"}, unresolved={"c1/line L-2"})  # a line it didn't list

    assert passed(expected, actual)


def test_one_of_two_breaks_found_is_a_fail() -> None:
    expected = outcomes(broken={"c1/T-1": {SSI, PRICE}})
    actual = outcomes(broken={"c1/T-1": {SSI}})

    assert not passed(expected, actual)
    assert describe(expected, "c1/T-1") == "broken: PRICE_BREAK, SSI_MISMATCH"
    assert describe(actual, "c1/T-1") == "broken: SSI_MISMATCH"
