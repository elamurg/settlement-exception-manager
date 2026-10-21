"""Tests for settlement.evaluation.baseline: scoring a reconciliation against the manifest."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import (
    BreakType,
    FieldDifference,
    Party,
    SettlementMethod,
    Side,
    StatementLine,
    Trade,
)
from settlement.domain.tolerance import ToleranceRule, ToleranceSet
from settlement.evaluation.baseline import (
    Scores,
    TypeScore,
    main,
    run_baseline,
    score,
)
from settlement.generation.generator import GenerationConfig, InjectedBreak
from settlement.ingestion.book import BookEntry
from settlement.reconciliation.engine import reconcile
from settlement.reconciliation.report import ReconciliationReport

NOW = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)
LEI_BANK = LEI("HWUPKR0MPOU8FGXBT394")
ISIN_SAP = ISIN("DE0007164600")
ISIN_OTHER = ISIN("US0378331005")
V1_TOLERANCES = Path(__file__).parent.parent / "config" / "tolerances" / "v1.yaml"
SMALL_RUN = GenerationConfig(trades=500, seed=7)

TOLERANCES = ToleranceSet(
    version="test-v1",
    effective_from=date(2026, 10, 1),
    rules=(ToleranceRule("*", "*", Decimal("5.00"), Decimal("0")),),
)

BASE_TRADE = Trade(
    trade_id="T1",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    price=Decimal("150.25"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty=Party(party_id="CP-001", name="Example Bank plc", lei=LEI_BANK),
    trade_date=date(2026, 10, 8),
    intended_settlement_date=date(2026, 10, 9),
    settlement_method=SettlementMethod.DVP,
)

BASE_LINE = StatementLine(
    line_id="L1",
    source="custodian",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty_lei=LEI_BANK,
    intended_settlement_date=date(2026, 10, 9),
    reference="T1",
)


def make_entry(trade_id: str = "T1", **trade_changes: Any) -> BookEntry:
    return BookEntry(
        trade=replace(BASE_TRADE, trade_id=trade_id, **trade_changes),
        counterparty_bic="DEUTDEFF",
        counterparty_account="SAFE-123",
    )


def make_line(line_id: str = "L1", **changes: Any) -> StatementLine:
    return replace(BASE_LINE, line_id=line_id, **changes)


def run(book: list[BookEntry], statement: list[StatementLine]) -> ReconciliationReport:
    return reconcile(book, statement, TOLERANCES, raised_at=NOW)


def injected(break_type: BreakType, trade_id: str, *related_ids: str) -> InjectedBreak:
    """A manifest entry. The evidence isn't used by score(), so any valid difference will do."""
    return InjectedBreak(
        break_type=break_type,
        trade_id=trade_id,
        statement_line_id=None,
        evidence=(FieldDifference("example", ours="a", theirs="b"),),
        related_trade_ids=related_ids,
    )


# A report where T1's line is 100 short: the engine raises a QUANTITY_BREAK on T1.
QUANTITY_REPORT_BOOK = [make_entry("T1")]
QUANTITY_REPORT_STATEMENT = [make_line("L1", quantity=Decimal("900"))]


def test_rates_are_worked_out_from_the_counts() -> None:
    scores = Scores(clean_trades=4, matched_clean=3, false_breaks=1, unresolved_clean=0, by_type={})

    assert scores.match_rate == 0.75
    assert scores.false_break_rate == 0.25


def test_rates_are_zero_when_there_are_no_clean_trades() -> None:
    scores = Scores(clean_trades=0, matched_clean=0, false_breaks=0, unresolved_clean=0, by_type={})

    assert scores.match_rate == 0.0  # no ZeroDivisionError
    assert scores.false_break_rate == 0.0


def test_clean_trade_that_matched_counts_as_matched() -> None:
    report = run([make_entry("T1")], [make_line("L1")])

    scores = score(report, injected=[], book_trade_ids=["T1"])

    assert scores.clean_trades == 1
    assert scores.matched_clean == 1
    assert scores.false_breaks == 0
    assert scores.match_rate == 1.0
    assert scores.by_type == {}


def test_clean_trade_with_a_break_is_a_false_break() -> None:
    # Nothing was injected on T1, but the engine raised a QUANTITY_BREAK on it.
    report = run(QUANTITY_REPORT_BOOK, QUANTITY_REPORT_STATEMENT)

    scores = score(report, injected=[], book_trade_ids=["T1"])

    assert scores.false_breaks == 1
    assert scores.matched_clean == 0
    assert scores.false_break_rate == 1.0


def test_trade_missing_from_report_lowers_the_match_rate() -> None:
    # T9 is in the book but was never reconciled: it must still count as a clean trade.
    report = run([make_entry("T1")], [make_line("L1")])

    scores = score(report, injected=[], book_trade_ids=["T1", "T9"])

    assert scores.clean_trades == 2
    assert scores.matched_clean == 1
    assert scores.match_rate == 0.5


def test_every_clean_trade_is_matched_false_or_unresolved() -> None:
    #   T1 + L1                                  → clean, matched
    #   T2 qty 2000 + L2 qty 2100                → QUANTITY_BREAK on a clean trade: false break
    #   T3 (other ISIN) + L3a, L3b (other ISIN)  → two candidate lines: unresolved
    book = [
        make_entry("T1"),
        make_entry("T2", quantity=Decimal("2000")),
        make_entry("T3", isin=ISIN_OTHER, quantity=Decimal("3000")),
    ]
    statement = [
        make_line("L1"),
        make_line("L2", quantity=Decimal("2100")),
        make_line("L3a", isin=ISIN_OTHER, quantity=Decimal("3100")),
        make_line("L3b", isin=ISIN_OTHER, quantity=Decimal("3200")),
    ]
    report = run(book, statement)

    scores = score(report, injected=[], book_trade_ids=["T1", "T2", "T3"])

    assert (scores.matched_clean, scores.false_breaks, scores.unresolved_clean) == (1, 1, 1)
    total = scores.matched_clean + scores.false_breaks + scores.unresolved_clean
    assert total == scores.clean_trades


def test_injected_break_found_with_right_type() -> None:
    report = run(QUANTITY_REPORT_BOOK, QUANTITY_REPORT_STATEMENT)

    scores = score(
        report, injected=[injected(BreakType.QUANTITY_BREAK, "T1")], book_trade_ids=["T1"]
    )

    assert scores.by_type == {
        BreakType.QUANTITY_BREAK: TypeScore(injected=1, found=1, wrong_type=0, missed=0)
    }
    assert scores.clean_trades == 0  # T1 was injected, so it isn't clean
    assert scores.false_breaks == 0


def test_injected_break_found_with_wrong_type() -> None:
    # The manifest says STATIC_DATA, but the engine raised a QUANTITY_BREAK on T1.
    report = run(QUANTITY_REPORT_BOOK, QUANTITY_REPORT_STATEMENT)

    scores = score(report, injected=[injected(BreakType.STATIC_DATA, "T1")], book_trade_ids=["T1"])

    assert scores.by_type == {
        BreakType.STATIC_DATA: TypeScore(injected=1, found=0, wrong_type=1, missed=0)
    }


def test_injected_break_with_nothing_raised_is_missed() -> None:
    # The manifest says T1 had a PRICE_BREAK, but T1 came out clean.
    report = run([make_entry("T1")], [make_line("L1")])

    scores = score(report, injected=[injected(BreakType.PRICE_BREAK, "T1")], book_trade_ids=["T1"])

    assert scores.by_type == {
        BreakType.PRICE_BREAK: TypeScore(injected=1, found=0, wrong_type=0, missed=1)
    }


def test_duplicate_booking_counts_once_and_is_not_a_false_break() -> None:
    # T1 and T2 are identical bookings with one line: the engine flags both.
    # The manifest records ONE break on T1, with T2 as the related booking.
    report = run([make_entry("T1"), make_entry("T2")], [make_line("L1")])

    scores = score(
        report,
        injected=[injected(BreakType.DUPLICATE_BOOKING, "T1", "T2")],
        book_trade_ids=["T1", "T2"],
    )

    assert scores.by_type == {
        BreakType.DUPLICATE_BOOKING: TypeScore(injected=1, found=1, wrong_type=0, missed=0)
    }
    assert scores.false_breaks == 0  # T2's break belongs to the injected duplicate
    assert scores.clean_trades == 0


def test_injected_break_is_found_through_a_related_trade() -> None:
    # The break was raised on T2 only. The manifest's main trade is T1, with T2 related,
    # so it still counts as found: score() checks ALL of an injected break's trade ids.
    report = run(
        [make_entry("T1"), make_entry("T2", quantity=Decimal("2000"))],
        [make_line("L1"), make_line("L2", quantity=Decimal("2100"))],
    )

    scores = score(
        report,
        injected=[injected(BreakType.QUANTITY_BREAK, "T1", "T2")],
        book_trade_ids=["T1", "T2"],
    )

    assert scores.by_type[BreakType.QUANTITY_BREAK].found == 1


@pytest.mark.parametrize(
    ("break_type", "expected"),
    [
        pytest.param(
            BreakType.QUANTITY_BREAK, TypeScore(1, found=1, wrong_type=0, missed=0), id="found"
        ),
        pytest.param(
            BreakType.DATE_MISMATCH, TypeScore(1, found=0, wrong_type=1, missed=0), id="wrong"
        ),
    ],
)
def test_outcomes_are_counted_under_the_injected_type(
    break_type: BreakType, expected: TypeScore
) -> None:
    report = run(QUANTITY_REPORT_BOOK, QUANTITY_REPORT_STATEMENT)

    scores = score(report, injected=[injected(break_type, "T1")], book_trade_ids=["T1"])

    assert scores.by_type[break_type] == expected


def test_baseline_finds_every_price_break_and_duplicate() -> None:
    scores = run_baseline(SMALL_RUN, V1_TOLERANCES)

    for break_type in (BreakType.PRICE_BREAK, BreakType.DUPLICATE_BOOKING):
        type_score = scores.by_type[break_type]
        assert type_score.found == type_score.injected
    assert scores.false_break_rate < 0.01


def test_baseline_counts_add_up() -> None:
    scores = run_baseline(SMALL_RUN, V1_TOLERANCES)

    clean_outcomes = scores.matched_clean + scores.false_breaks + scores.unresolved_clean
    assert clean_outcomes == scores.clean_trades
    assert scores.found + scores.wrong_type + scores.missed == scores.injected


def test_same_seed_gives_the_same_scores() -> None:
    # This is what makes it a baseline: anyone can re-run it and get identical numbers.
    assert run_baseline(SMALL_RUN, V1_TOLERANCES) == run_baseline(SMALL_RUN, V1_TOLERANCES)


def test_main_prints_the_table(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--trades", "200", "--seed", "7", "--tolerances", str(V1_TOLERANCES)])

    printed = capsys.readouterr().out
    assert "Trades: 200 · seed: 7" in printed
    assert "tolerance set v1" in printed
    assert "| Match rate |" in printed
