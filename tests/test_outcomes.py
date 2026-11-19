"""Tests for turning golden cases and reports into comparable outcomes (SE-18)."""

from datetime import UTC, datetime

from settlement.domain.models import BreakType
from settlement.evaluation.baseline import DEFAULT_TOLERANCES
from settlement.evaluation.golden import GoldenCase, load_golden_set, parse_golden_set
from settlement.evaluation.outcomes import (
    Outcomes,
    actual_from_report,
    expected_for_case,
    expected_for_generated,
)
from settlement.generation.generator import GenerationConfig, generate
from settlement.reconciliation.engine import reconcile
from settlement.reconciliation.tolerances import load_tolerance_set

GOLDEN = {case.case_id: case for case in load_golden_set().cases}


def test_two_breaks_on_one_trade_are_kept_together() -> None:
    expected = expected_for_case(GOLDEN["ssi-and-price"])

    assert expected.broken == {
        "ssi-and-price/T-1": frozenset({BreakType.SSI_MISMATCH, BreakType.PRICE_BREAK})
    }
    assert expected.clean == frozenset()


def test_trades_left_for_a_human() -> None:
    expected = expected_for_case(GOLDEN["one-line-two-candidates"])

    assert expected.unresolved == {"one-line-two-candidates/T-1", "one-line-two-candidates/T-2"}


def test_a_line_gets_its_own_kind_of_id() -> None:
    """Trade T-1 and line L-1 can't be confused, even with the same case prefix."""
    case = case_from(
        '    statement:\n      - reference: "T-1"\n      - reference: "NEVER-BOOKED"\n',
        '    expected:\n      clean: ["T-1"]\n      unresolved_lines: ["L-2"]\n',
    )

    expected = expected_for_case(case)

    assert expected.clean == {"c1/T-1"}
    assert expected.unresolved == {"c1/line L-2"}
    assert expected.records == {"c1/T-1", "c1/line L-2"}


def case_from(statement: str, expected: str) -> GoldenCase:
    """A one-case golden file: trade T-1 plus the given statement and expected blocks."""
    (case,) = parse_golden_set(
        'version: "v1"\n'
        'generated:\n  trades: "1"\n  seed: "1"\n  generator_version: "1"\n'
        "cases:\n"
        '  - id: "c1"\n    kind: "MUST_BE_CLEAN"\n    title: "t"\n    rationale: "r"\n'
        '    book:\n      - trade_id: "T-1"\n' + statement + expected
    ).cases
    return case


# What the engine did. Only basics the engine will always do, so these don't break when
# it gets better at the hard cases.

RAISED_AT = datetime(2026, 10, 5, 18, tzinfo=UTC)
TOLERANCES = load_tolerance_set(DEFAULT_TOLERANCES)
CLEAN = '    expected:\n      clean: ["T-1"]\n'  # not used by these tests, but required


def run(statement: str) -> GoldenCase:
    return case_from(statement, CLEAN)


def actual(case: GoldenCase) -> Outcomes:
    report = reconcile(case.book, case.statement, TOLERANCES, raised_at=RAISED_AT)
    return actual_from_report(report, "c1")


def test_a_perfect_match_is_clean() -> None:
    outcomes = actual(run('    statement:\n      - reference: "T-1"\n'))

    assert outcomes.clean == {"c1/T-1"}


def test_a_line_nobody_booked_is_left_for_a_human() -> None:
    stray = '      - reference: "NEVER-BOOKED"\n'
    stray += '        isin: "GB00B03MLX29"\n        quantity: "777"\n'
    outcomes = actual(run('    statement:\n      - reference: "T-1"\n' + stray))

    assert outcomes.unresolved == {"c1/line L-2"}


def test_a_break_is_recorded_with_its_type() -> None:
    outcomes = actual(run('    statement:\n      - reference: "T-1"\n        quantity: "900"\n'))

    assert outcomes.broken == {"c1/T-1": {BreakType.QUANTITY_BREAK}}


# The generated part's right answer


def test_generated_expects_every_injected_break_and_nothing_else() -> None:
    data = generate(GenerationConfig(trades=300, seed=3))  # 304 rows, 36 breaks, 4 duplicates

    expected = expected_for_generated(data)

    assert expected.records == {f"generated/{trade.trade_id}" for trade in data.book}
    for injected in data.breaks:
        for trade_id in (injected.trade_id, *injected.related_trade_ids):
            assert injected.break_type in expected.broken[f"generated/{trade_id}"]
    assert expected.unresolved == frozenset()
