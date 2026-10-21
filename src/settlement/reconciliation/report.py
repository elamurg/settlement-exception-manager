"""Result of one reconciliation run: what matched cleanly, what broke, what's left."""

from dataclasses import dataclass
from datetime import datetime

from settlement.domain.matching import AmbiguousGroup, Match
from settlement.domain.models import Break, StatementLine, Trade


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    tolerance_set_version: str  # which tolerances judged this run
    raised_at: datetime  # when the run happened
    clean: tuple[Match, ...]  # matched, within tolerance, no break of any kind
    breaks: tuple[Break, ...]
    unresolved_trades: tuple[Trade, ...]
    unresolved_lines: tuple[StatementLine, ...]
    unresolved_groups: tuple[AmbiguousGroup, ...]
