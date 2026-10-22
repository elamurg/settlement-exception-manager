"""Result of one reconciliation run: what matched cleanly, what broke, what's left."""

from dataclasses import dataclass
from datetime import datetime

from settlement.domain.matching import AmbiguousGroup
from settlement.domain.models import Break, StatementLine, Trade
from settlement.domain.tolerance import ToleranceResult


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    tolerance_set_version: str  # which tolerances judged this run
    raised_at: datetime  # when the run happened
    # Matched, within tolerance, no break of any kind. Each keeps its tolerance result, so
    # the accepted difference (and the rule that allowed it) is recorded, not thrown away.
    clean: tuple[ToleranceResult, ...]
    breaks: tuple[Break, ...]
    unresolved_trades: tuple[Trade, ...]
    unresolved_lines: tuple[StatementLine, ...]
    unresolved_groups: tuple[AmbiguousGroup, ...]
