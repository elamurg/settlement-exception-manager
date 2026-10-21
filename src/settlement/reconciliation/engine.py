"""Run a reconciliation: exact match, then tolerance check, then the fuzzy pass."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from settlement.domain.fuzzy import SettlementDetails, find_breaks
from settlement.domain.matching import match_exact
from settlement.domain.models import StatementLine
from settlement.domain.tolerance import ToleranceSet, evaluate
from settlement.ingestion.book import BookEntry, load_book
from settlement.ingestion.custodian import load_statement
from settlement.reconciliation.report import ReconciliationReport
from settlement.reconciliation.tolerances import load_tolerance_set


def reconcile(
    book: Sequence[BookEntry],
    statement: Sequence[StatementLine],
    tolerance_set: ToleranceSet,
    *,
    raised_at: datetime,
) -> ReconciliationReport:
    """Reconcile our book against a custodian statement."""
    trades = [entry.trade for entry in book]
    match_result = match_exact(trades, statement)
    tolerance_results = [evaluate(match, tolerance_set) for match in match_result.matched]

    settlement_details = {
        entry.trade.trade_id: SettlementDetails(
            bic=entry.counterparty_bic,
            account=entry.counterparty_account,
        )
        for entry in book
    }

    fuzzy = find_breaks(match_result, tolerance_results, settlement_details, raised_at=raised_at)

    broken_trade_ids = {brk.ours.trade_id for brk in fuzzy.breaks if brk.ours}
    clean = tuple(
        match for match in match_result.matched if match.trade.trade_id not in broken_trade_ids
    )

    return ReconciliationReport(
        tolerance_set_version=tolerance_set.version,
        raised_at=raised_at,
        clean=clean,
        breaks=fuzzy.breaks,
        unresolved_trades=fuzzy.unresolved_trades,
        unresolved_lines=fuzzy.unresolved_lines,
        unresolved_groups=fuzzy.unresolved_groups,
    )


def reconcile_files(
    book_path: Path,
    statement_path: Path,
    tolerance_path: Path,
    *,
    raised_at: datetime,
) -> ReconciliationReport:
    """Load the three files, then reconcile them."""
    return reconcile(
        book=load_book(book_path),
        statement=load_statement(statement_path),
        tolerance_set=load_tolerance_set(tolerance_path),
        raised_at=raised_at,
    )
