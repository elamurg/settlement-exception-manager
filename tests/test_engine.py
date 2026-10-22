"""Tests for settlement.reconciliation.engine: the full exact -> tolerance -> fuzzy run."""

from collections import Counter
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
from settlement.generation.files import write_all
from settlement.generation.generator import GenerationConfig, generate
from settlement.ingestion.book import BookEntry
from settlement.reconciliation.engine import reconcile, reconcile_files
from settlement.reconciliation.errors import ToleranceError
from settlement.reconciliation.report import ReconciliationReport

V1_TOLERANCES = Path(__file__).parent.parent / "config" / "tolerances" / "v1.yaml"
NOW = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)
LEI_BANK = LEI("HWUPKR0MPOU8FGXBT394")
ISIN_SAP = ISIN("DE0007164600")

# One simple rule for the hand-built tests: differences up to 5.00 are accepted.
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
    counterparty=Party(party_id="CP-001", name="Revolut", lei=LEI_BANK),
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
    """A book entry: BASE_TRADE (with changes) plus the SSI we hold on file for it."""
    return BookEntry(
        trade=replace(BASE_TRADE, trade_id=trade_id, **trade_changes),
        counterparty_bic="DEUTDEFF",
        counterparty_account="SAFE-123",
    )


def make_line(line_id: str = "L1", **changes: Any) -> StatementLine:
    return replace(BASE_LINE, line_id=line_id, **changes)


def trade_ids_everywhere(report: ReconciliationReport) -> list[str]:
    """Every trade id the report mentions, once per place it appears.

    A trade can appear in: a clean match, a break (as `ours`), the unresolved trades, or an
    unresolved group. Duplicates in this list mean a trade was counted in two places.
    """
    return [
        *(result.match.trade.trade_id for result in report.clean),
        *(brk.ours.trade_id for brk in report.breaks if brk.ours is not None),
        *(trade.trade_id for trade in report.unresolved_trades),
        *(trade.trade_id for group in report.unresolved_groups for trade in group.trades),
    ]


def test_matching_pair_is_clean() -> None:
    report = reconcile([make_entry()], [make_line()], TOLERANCES, raised_at=NOW)

    # The pair matches and is within tolerance, and the line reports no SSI details,
    # so it should be the only thing in the report:
    assert [(r.match.trade.trade_id, r.match.line.line_id) for r in report.clean] == [("T1", "L1")]
    assert report.breaks == ()
    assert report.unresolved_trades == ()
    assert report.unresolved_lines == ()
    assert report.unresolved_groups == ()


def test_clean_pair_keeps_the_difference_it_was_accepted_with() -> None:
    # 3.00 off, within the 5.00 tolerance: clean, but the difference must not be lost.
    line = make_line(consideration=Decimal("150253.00"))

    report = reconcile([make_entry()], [line], TOLERANCES, raised_at=NOW)

    (result,) = report.clean
    assert result.difference == Decimal("3.00")
    assert result.allowed == Decimal("5.00")
    assert result.within is True
    assert result.tolerance_set_version == "test-v1"


def test_outside_tolerance_is_a_break_and_not_clean() -> None:
    # The line's consideration is 100.00 off (tolerance is 5.00):
    line = make_line(consideration=Decimal("150350.00"))
    report = reconcile([make_entry()], [line], TOLERANCES, raised_at=NOW)

    (brk,) = report.breaks
    assert brk.break_type == BreakType.PRICE_BREAK
    assert brk.evidence == (FieldDifference("consideration", ours="150250.00", theirs="150350.00"),)
    assert report.clean == ()


def test_ssi_on_file_comes_from_the_book_entry() -> None:
    # make_entry() holds BIC "DEUTDEFF" on file. Make the custodian report a different one:
    line = make_line(counterparty_bic="BARCGB22")
    report = reconcile([make_entry()], [line], TOLERANCES, raised_at=NOW)

    (brk,) = report.breaks
    assert brk.break_type == BreakType.SSI_MISMATCH
    assert brk.evidence == (
        FieldDifference("counterparty_bic", ours="DEUTDEFF", theirs="BARCGB22"),
    )
    assert report.clean == ()


def test_report_is_stamped_with_version_and_time() -> None:
    report = reconcile([make_entry()], [make_line()], TOLERANCES, raised_at=NOW)
    assert report.tolerance_set_version == "test-v1"
    assert report.raised_at == NOW


def test_line_with_no_trade_is_unresolved() -> None:
    report = reconcile([], [make_line()], TOLERANCES, raised_at=NOW)
    assert [line.line_id for line in report.unresolved_lines] == ["L1"]
    assert report.breaks == ()
    assert report.clean == ()


def test_every_trade_appears_exactly_once() -> None:
    # Build a mix where every pile gets something (check each trade against EVERY line!):
    #   T1               + L1                                    → clean
    #   T2 quantity 2000 + L2 quantity 2100                      → QUANTITY_BREAK (near match)
    #   T3 with isin=ISIN("US0378331005"), no line               → UNMATCHED_INSTRUCTION
    #   T4 and T5 both quantity 4000 + L4 quantity 4000          → DUPLICATE_BOOKING on both
    # Then:
    book = [
        make_entry("T1"),
        make_entry("T2", quantity=Decimal("2000")),
        make_entry("T3", isin=ISIN("US0378331005")),
        make_entry("T4", quantity=Decimal("4000")),
        make_entry("T5", quantity=Decimal("4000")),
    ]
    statement = [
        make_line("L1"),
        make_line("L2", quantity=Decimal("2100")),
        make_line("L4", quantity=Decimal("4000")),
    ]
    report = reconcile(book, statement, TOLERANCES, raised_at=NOW)
    assert sorted(trade_ids_everywhere(report)) == ["T1", "T2", "T3", "T4", "T5"]


def test_reconcile_files_runs_on_generated_files(tmp_path: Path) -> None:
    # tmp_path is an empty temporary folder that pytest creates for this test.
    data = generate(GenerationConfig(trades=500, seed=7))
    write_all(data, tmp_path)
    report = reconcile_files(
        tmp_path / "book.csv", tmp_path / "statement.csv", V1_TOLERANCES, raised_at=NOW
    )
    # Every generated trade must be in exactly one place:
    # (Counter counts how often each id appears, so it catches missing AND doubled ids.)
    assert Counter(trade_ids_everywhere(report)) == Counter(t.trade_id for t in data.book)
    assert report.tolerance_set_version == "v1"  # makes sure a real config file was used


def test_generated_breaks_are_found(tmp_path: Path) -> None:
    # Same setup as above (500 trades, seed 7, write_all, reconcile_files).
    # Every injected PRICE_BREAK and DUPLICATE_BOOKING should be found: they were 47/47
    # and 29/29 on the 5,000-trade run, so expect all of them here too.
    data = generate(GenerationConfig(trades=500, seed=7))
    write_all(data, tmp_path)
    report = reconcile_files(
        tmp_path / "book.csv", tmp_path / "statement.csv", V1_TOLERANCES, raised_at=NOW
    )
    injected = {b.trade_id for b in data.breaks if b.break_type == BreakType.PRICE_BREAK}
    found = {
        brk.ours.trade_id
        for brk in report.breaks
        if brk.ours and brk.break_type == BreakType.PRICE_BREAK
    }
    assert injected <= found


def test_missing_tolerance_file_raises(tmp_path: Path) -> None:
    # Write generated files as above, but pass tmp_path / "missing.yaml" as the tolerances.
    data = generate(GenerationConfig(trades=500, seed=7))
    write_all(data, tmp_path)
    with pytest.raises(ToleranceError):
        reconcile_files(
            tmp_path / "book.csv",
            tmp_path / "statement.csv",
            tmp_path / "missing.yaml",
            raised_at=NOW,
        )
