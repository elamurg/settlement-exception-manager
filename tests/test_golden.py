"""Tests for loading the golden break set (SE-16)."""

from decimal import Decimal
from pathlib import Path

import pytest

from settlement.domain.models import BreakType
from settlement.evaluation.golden import (
    GOLDEN_V1,
    STANDARD_TRADE,
    CaseKind,
    ExpectedBreak,
    GoldenSetError,
    load_golden_set,
    parse_golden_set,
)

HEADER = """\
version: "v1"
generated:
  trades: "100"
  seed: "1"
cases:
"""


def one_case(body: str, expected: str = '    expected:\n      clean: ["T-1"]\n') -> str:
    """A golden file with one case: a standard trade T-1, plus `body` lines."""
    return (
        HEADER
        + '  - id: "c1"\n    kind: "MUST_BE_CLEAN"\n    title: "t"\n    rationale: "r"\n'
        + '    book:\n      - trade_id: "T-1"\n'
        + body
        + expected
    )


# The committed file


def test_committed_v1_loads() -> None:
    golden = load_golden_set(GOLDEN_V1)

    assert golden.version == "v1"
    assert (golden.generated.trades, golden.generated.seed) == (5000, 42)
    assert golden.cases


def test_every_committed_case_has_a_rationale() -> None:
    for case in load_golden_set(GOLDEN_V1).cases:
        assert len(case.rationale) > 20, f"{case.case_id} needs a real rationale"


# Building trades and lines from a case


def test_a_trade_starts_from_the_standard_trade() -> None:
    (case,) = parse_golden_set(one_case("")).cases
    (entry,) = case.book

    assert entry.trade.trade_id == "T-1"
    assert entry.trade.quantity == Decimal(STANDARD_TRADE["quantity"])
    assert str(entry.trade.isin) == STANDARD_TRADE["isin"]
    assert entry.counterparty_bic == STANDARD_TRADE["counterparty_bic"]


def test_a_trade_only_changes_what_the_case_says() -> None:
    text = one_case("").replace('trade_id: "T-1"', 'trade_id: "T-1"\n        quantity: "500"')
    (case,) = parse_golden_set(text).cases

    assert case.book[0].trade.quantity == Decimal("500")
    assert case.book[0].trade.price == Decimal(STANDARD_TRADE["price"])


def test_a_line_starts_as_a_perfect_match_for_its_trade() -> None:
    text = one_case("").replace('trade_id: "T-1"', 'trade_id: "T-1"\n        quantity: "500"')
    text = text.replace("    expected:", '    statement:\n      - reference: "T-1"\n    expected:')
    (case,) = parse_golden_set(text).cases
    (line,) = case.statement

    assert line.quantity == Decimal("500")  # copied from the trade, not the standard trade
    assert line.reference == "T-1"
    assert line.counterparty_account == STANDARD_TRADE["counterparty_account"]
    assert line.line_id == "L-1"


def test_a_line_only_changes_what_the_case_says() -> None:
    text = one_case('    statement:\n      - reference: "T-1"\n        quantity: "900"\n')
    (case,) = parse_golden_set(text).cases

    assert case.statement[0].quantity == Decimal("900")
    assert case.statement[0].isin == case.book[0].trade.isin


def test_a_line_for_a_trade_we_dont_have_uses_the_standard_trade() -> None:
    text = one_case('    statement:\n      - reference: "T-STRAY"\n')
    (case,) = parse_golden_set(text).cases

    assert case.statement[0].reference == "T-STRAY"
    assert str(case.statement[0].isin) == STANDARD_TRADE["isin"]


def test_statement_is_optional() -> None:
    (case,) = parse_golden_set(one_case("")).cases

    assert case.statement == ()


# The expected answer


def test_expected_breaks_and_outcomes() -> None:
    text = one_case(
        "",
        '    expected:\n      breaks:\n        - type: "QUANTITY_BREAK"\n          trade: "T-1"\n'
        '        - type: "SSI_MISMATCH"\n          trade: "T-1"\n',
    )
    (case,) = parse_golden_set(text).cases

    assert case.expected.breaks == (
        ExpectedBreak(BreakType.QUANTITY_BREAK, "T-1"),
        ExpectedBreak(BreakType.SSI_MISMATCH, "T-1"),
    )
    assert case.expected.clean == frozenset()


@pytest.mark.parametrize(
    ("expected", "message"),
    [
        pytest.param(
            "    expected: {}\n", "'T-1' must be in exactly one outcome, found none", id="forgotten"
        ),
        pytest.param(
            '    expected:\n      clean: ["T-1"]\n      unresolved: ["T-1"]\n',
            "found clean and unresolved",
            id="answered_twice",
        ),
        pytest.param(
            '    expected:\n      clean: ["T-1", "T-9"]\n',
            "mentions 'T-9', which isn't in the book",
            id="unknown_trade",
        ),
        pytest.param(
            '    expected:\n      clean: ["T-1", "T-1"]\n', "lists a trade twice", id="listed_twice"
        ),
        pytest.param(
            '    expected:\n      breaks:\n        - type: "WRONG"\n          trade: "T-1"\n',
            "'WRONG' isn't a break type",
            id="bad_break_type",
        ),
        pytest.param(
            '    expected:\n      clena: ["T-1"]\n', "unknown clena", id="misspelt_outcome"
        ),
    ],
)
def test_bad_expected_answers_are_refused(expected: str, message: str) -> None:
    with pytest.raises(GoldenSetError, match=message):
        parse_golden_set(one_case("", expected))


# Never a silent skip


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(
            ('kind: "MUST_BE_CLEAN"', 'kind: "EASY"'), "kind must be one of", id="bad_kind"
        ),
        pytest.param(
            ('rationale: "r"', 'rationale: " "'), "rationale is empty", id="blank_rationale"
        ),
        pytest.param(('    rationale: "r"\n', ""), "missing rationale", id="no_rationale"),
        pytest.param(
            ('trade_id: "T-1"', 'trade_id: "T-1"\n        quantty: "5"'),
            "unknown quantty",
            id="misspelt_field",
        ),
        pytest.param(
            ('trade_id: "T-1"', 'trade_id: "T-1"\n        quantity: 500'),
            "must be a quoted string",
            id="unquoted",
        ),
        pytest.param(
            ('trade_id: "T-1"', 'trade_id: "T-1"\n        isin: "DE0007164601"'),
            r"cases\[1\] \(c1\)\.book\[1\]\.isin: Invalid ISIN check digit",
            id="bad_isin_names_where",
        ),
        pytest.param(
            ('seed: "1"', 'seed: "one"'), "generated.seed must be a whole number", id="bad_seed"
        ),
    ],
)
def test_malformed_cases_are_refused(change: tuple[str, str], message: str) -> None:
    old, new = change

    with pytest.raises(GoldenSetError, match=message):
        parse_golden_set(one_case("").replace(old, new))


def test_two_cases_with_the_same_id() -> None:
    case = one_case("")[len(HEADER) :]

    with pytest.raises(GoldenSetError, match="'c1' is used twice"):
        parse_golden_set(HEADER + case + case)


def test_missing_file_names_it(tmp_path: Path) -> None:
    with pytest.raises(GoldenSetError, match="nope.yaml: cannot read file"):
        load_golden_set(tmp_path / "nope.yaml")


def test_case_kinds_match_the_ticket() -> None:
    assert {kind.value for kind in CaseKind} == {
        "TWO_CAUSES",
        "REALLY_TWO",
        "CORPORATE_ACTION",
        "MUST_BE_CLEAN",
    }
