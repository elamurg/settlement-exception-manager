"""What happened to every record in a run, in a form the metrics can compare (SE-18).

The same shape holds the right answer (from the golden set) and what the engine did (from a
reconciliation report), so scoring is comparing two Outcomes. Ids are prefixed with where
they came from ("stock-split/T-1", "generated/T-000123"), because every hand-written case
has its own T-1. Custodian lines get "line" in theirs ("stock-split/line L-2"), so a line
can't be mistaken for a trade with the same id.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from settlement.domain.models import Break, BreakType
from settlement.evaluation.golden import GoldenCase
from settlement.generation.generator import GeneratedData
from settlement.reconciliation.report import ReconciliationReport


@dataclass(frozen=True, slots=True)
class Outcomes:
    """Every record's outcome: clean, broken (with its break types), or left for a human."""

    clean: frozenset[str]
    broken: Mapping[str, frozenset[BreakType]]  # record id -> the break types on it
    unresolved: frozenset[str]

    @property
    def records(self) -> frozenset[str]:
        """Every record these outcomes say anything about."""
        return self.clean | frozenset(self.broken) | self.unresolved


def combine(parts: Iterable[Outcomes]) -> Outcomes:
    """Many outcomes as one. Ids are prefixed by where they came from, so none clash."""
    clean: set[str] = set()
    unresolved: set[str] = set()
    broken: dict[str, frozenset[BreakType]] = {}
    for part in parts:
        clean |= part.clean
        unresolved |= part.unresolved
        broken.update(part.broken)
    return Outcomes(clean=frozenset(clean), broken=frozen(broken), unresolved=frozenset(unresolved))


def trade_key(prefix: str, trade_id: str) -> str:
    return f"{prefix}/{trade_id}"


def line_key(prefix: str, line_id: str) -> str:
    return f"{prefix}/line {line_id}"


def expected_for_case(case: GoldenCase) -> Outcomes:
    """The right answer for one hand-written case, from its `expected` block."""
    prefix = case.case_id
    broken: dict[str, set[BreakType]] = {}
    for expected_break in case.expected.breaks:
        key = trade_key(prefix, expected_break.trade_id)
        broken.setdefault(key, set()).add(expected_break.break_type)
    return Outcomes(
        clean=frozenset(trade_key(prefix, trade_id) for trade_id in case.expected.clean),
        broken=frozen(broken),
        unresolved=frozenset(trade_key(prefix, trade_id) for trade_id in case.expected.unresolved)
        | frozenset(line_key(prefix, line_id) for line_id in case.expected.unresolved_lines),
    )


def expected_for_generated(data: GeneratedData, prefix: str = "generated") -> Outcomes:
    """The right answer for the generated part, from the generator's manifest.

    Every injected break is on its trade, and on the second booking too for a duplicate.
    Every other trade on the book should come out clean. Nothing is meant for a human.
    """
    broken: dict[str, set[BreakType]] = {}
    for injected in data.breaks:
        for trade_id in (injected.trade_id, *injected.related_trade_ids):
            broken.setdefault(trade_key(prefix, trade_id), set()).add(injected.break_type)
    on_book = {trade_key(prefix, trade.trade_id) for trade in data.book}
    return Outcomes(
        clean=frozenset(on_book - broken.keys()), broken=frozen(broken), unresolved=frozenset()
    )


def actual_from_report(report: ReconciliationReport, prefix: str) -> Outcomes:
    """What the engine did, from its reconciliation report."""
    broken: dict[str, set[BreakType]] = {}
    for brk in report.breaks:
        broken.setdefault(_break_key(brk, prefix), set()).add(brk.break_type)
    unresolved = (
        {trade_key(prefix, trade.trade_id) for trade in report.unresolved_trades}
        | {line_key(prefix, line.line_id) for line in report.unresolved_lines}
        | {
            trade_key(prefix, t.trade_id)
            for group in report.unresolved_groups
            for t in group.trades
        }
        | {line_key(prefix, ln.line_id) for group in report.unresolved_groups for ln in group.lines}
    )
    return Outcomes(
        clean=frozenset(trade_key(prefix, result.match.trade.trade_id) for result in report.clean),
        broken=frozen(broken),
        unresolved=frozenset(unresolved),
    )


def _break_key(brk: Break, prefix: str) -> str:
    """A break is on our trade when there is one, otherwise on the custodian's line."""
    if brk.ours is not None:
        return trade_key(prefix, brk.ours.trade_id)
    if brk.theirs is not None:
        return line_key(prefix, brk.theirs.line_id)
    raise ValueError(f"break {brk.break_id} has neither side")  # Break itself refuses this


def frozen(broken: Mapping[str, Iterable[BreakType]]) -> Mapping[str, frozenset[BreakType]]:
    """A read-only copy, so Outcomes can't be changed after it's made."""
    return MappingProxyType({record: frozenset(types) for record, types in broken.items()})
