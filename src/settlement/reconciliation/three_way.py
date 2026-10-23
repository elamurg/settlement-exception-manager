"""Three-way reconciliation (SE-12): book vs custodian, then the CSD's matching status.

Runs the existing two-way reconciliation unchanged, then uses the MT548 statuses to put
each break on a leg and to raise the breaks only the CSD can see.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from settlement.domain.attribution import CsdStatus, Leg, attribute
from settlement.domain.models import Break, StatementLine
from settlement.domain.tolerance import ToleranceResult, ToleranceSet
from settlement.ingestion.book import BookEntry, load_book
from settlement.ingestion.custodian import load_statement
from settlement.ingestion.messages import StatusMessage
from settlement.ingestion.statuses import load_statuses
from settlement.reconciliation.engine import reconcile
from settlement.reconciliation.report import ReconciliationReport
from settlement.reconciliation.tolerances import load_tolerance_set


@dataclass(frozen=True, slots=True)
class ThreeWayReport:
    two_way: ReconciliationReport  # book vs custodian, exactly as SE-11 produces it
    legs: Mapping[str, Leg]  # break id -> leg, for every break in `breaks`
    counterparty_breaks: tuple[Break, ...]  # clean on leg 1, not matched at the CSD
    awaiting_status: tuple[str, ...]  # clean trade ids with no CSD status yet
    unrecognised: tuple[CsdStatus, ...]  # not matched, with a reason code we can't type
    stray_statuses: tuple[CsdStatus, ...]  # statuses for trades that aren't in the book

    @property
    def breaks(self) -> tuple[Break, ...]:
        """Every break from both legs: the one queue ops works through."""
        return self.two_way.breaks + self.counterparty_breaks

    @property
    def clean(self) -> tuple[ToleranceResult, ...]:
        """Clean on both legs: matched with our custodian *and* at the CSD."""
        not_clean = {brk.ours.trade_id for brk in self.counterparty_breaks if brk.ours}
        not_clean |= set(self.awaiting_status)
        not_clean |= {status.instruction_reference for status in self.unrecognised}
        return tuple(
            result for result in self.two_way.clean if result.match.trade.trade_id not in not_clean
        )


def reconcile_three_way(
    book: Sequence[BookEntry],
    statement: Sequence[StatementLine],
    statuses: Sequence[StatusMessage],
    tolerance_set: ToleranceSet,
    *,
    raised_at: datetime,
) -> ThreeWayReport:
    two_way = reconcile(book, statement, tolerance_set, raised_at=raised_at)
    # The domain's own version of each MT548, so domain/ doesn't depend on ingestion/.
    csd_statuses = [
        CsdStatus(message.related_reference, message.matched, message.reason)
        for message in statuses
    ]
    result = attribute(
        breaks=two_way.breaks,
        clean_trades=[result.match.trade for result in two_way.clean],
        statuses=csd_statuses,
        book_trade_ids=[entry.trade.trade_id for entry in book],
        raised_at=raised_at,
    )
    return ThreeWayReport(
        two_way=two_way,
        legs=result.legs,
        counterparty_breaks=result.counterparty_breaks,
        awaiting_status=result.awaiting_status,
        unrecognised=result.unrecognised,
        stray_statuses=result.stray_statuses,
    )


def reconcile_three_way_files(
    book_path: Path,
    statement_path: Path,
    statuses_path: Path,
    tolerance_path: Path,
    *,
    raised_at: datetime,
) -> ThreeWayReport:
    """Load the four files, then reconcile them three ways."""
    return reconcile_three_way(
        book=load_book(book_path),
        statement=load_statement(statement_path),
        statuses=load_statuses(statuses_path),
        tolerance_set=load_tolerance_set(tolerance_path),
        raised_at=raised_at,
    )
