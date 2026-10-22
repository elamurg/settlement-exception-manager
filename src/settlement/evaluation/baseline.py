"""Score a reconciliation run against the generator's manifest, to record the baseline.

Run it with: python -m settlement.evaluation.baseline --trades 5000 --seed 42
"""

import argparse
import tempfile
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from settlement.domain.models import BreakType
from settlement.generation.files import BOOK_FILE, STATEMENT_FILE, write_all
from settlement.generation.generator import (
    GENERATOR_VERSION,
    GenerationConfig,
    InjectedBreak,
    generate,
)
from settlement.reconciliation.engine import reconcile_files
from settlement.reconciliation.report import ReconciliationReport
from settlement.reconciliation.tolerances import load_tolerance_set

DEFAULT_TOLERANCES = Path("config/tolerances/v1.yaml")


@dataclass(frozen=True, slots=True)
class TypeScore:
    """How one break type did."""

    injected: int
    found: int  # a break of the right type on the injected trade
    wrong_type: int  # a break, but of a different type
    missed: int  # no break at all (left unresolved)


@dataclass(frozen=True, slots=True)
class Scores:
    """Counts from one scored run. Rates are worked out from the counts.

    Rates are floats: they're proportions for people to read, not money or quantities.
    """

    clean_trades: int  # trades with nothing injected
    matched_clean: int  # ...that ended up clean
    false_breaks: int  # ...that got a break anyway: the expensive error
    unresolved_clean: int  # ...left unresolved: no break, but not clean either
    by_type: Mapping[BreakType, TypeScore]

    @property
    def match_rate(self) -> float:
        return self.matched_clean / self.clean_trades if self.clean_trades else 0.0

    @property
    def false_break_rate(self) -> float:
        return self.false_breaks / self.clean_trades if self.clean_trades else 0.0

    @property
    def injected(self) -> int:
        return sum(type_score.injected for type_score in self.by_type.values())

    @property
    def found(self) -> int:
        return sum(type_score.found for type_score in self.by_type.values())

    @property
    def wrong_type(self) -> int:
        return sum(type_score.wrong_type for type_score in self.by_type.values())

    @property
    def missed(self) -> int:
        return sum(type_score.missed for type_score in self.by_type.values())

    @property
    def detection_rate(self) -> float:
        return self.found / self.injected if self.injected else 0.0


def score(
    report: ReconciliationReport,
    injected: Sequence[InjectedBreak],
    book_trade_ids: Collection[str],
) -> Scores:
    """Compare a reconciliation report with the breaks that were injected."""
    injected_ids: set[str] = set()
    for injected_break in injected:
        injected_ids.add(injected_break.trade_id)
        for related_id in injected_break.related_trade_ids:
            injected_ids.add(related_id)

    clean_ids = set(book_trade_ids) - injected_ids

    break_types_by_trade: defaultdict[str, set[BreakType]] = defaultdict(set)
    for brk in report.breaks:
        if brk.ours is not None:
            break_types_by_trade[brk.ours.trade_id].add(brk.break_type)
    clean_in_report = {result.match.trade.trade_id for result in report.clean}
    unresolved_ids = {trade.trade_id for trade in report.unresolved_trades} | {
        trade.trade_id for group in report.unresolved_groups for trade in group.trades
    }

    counts: defaultdict[BreakType, Counter[str]] = defaultdict(Counter)
    for injected_break in injected:
        ids = (injected_break.trade_id, *injected_break.related_trade_ids)
        raised = set().union(*(break_types_by_trade.get(trade_id, set()) for trade_id in ids))
        if injected_break.break_type in raised:
            outcome = "found"
        elif raised:
            outcome = "wrong_type"
        else:
            outcome = "missed"
        counts[injected_break.break_type][outcome] += 1

    by_type = {
        break_type: TypeScore(
            injected=sum(counter.values()),
            found=counter["found"],
            wrong_type=counter["wrong_type"],
            missed=counter["missed"],
        )
        for break_type, counter in counts.items()
    }

    return Scores(
        clean_trades=len(clean_ids),
        matched_clean=len(clean_ids & clean_in_report),
        false_breaks=len(clean_ids & break_types_by_trade.keys()),
        unresolved_clean=len(clean_ids & unresolved_ids),
        by_type=by_type,
    )


def run_baseline(config: GenerationConfig, tolerance_path: Path) -> Scores:
    """Generate data, reconcile it through real files, and score it against the manifest."""
    data = generate(config)
    raised_at = datetime.combine(config.trade_date, time(18, 0), tzinfo=UTC)
    with tempfile.TemporaryDirectory() as folder:
        out_dir = Path(folder)
        write_all(data, out_dir)
        report = reconcile_files(
            out_dir / BOOK_FILE, out_dir / STATEMENT_FILE, tolerance_path, raised_at=raised_at
        )
    return score(report, data.breaks, [trade.trade_id for trade in data.book])


def format_table(scores: Scores, config: GenerationConfig, tolerance_set_version: str) -> str:
    """The scores as a markdown table, with everything needed to reproduce them."""
    lines = [
        f"Trades: {config.trades} · seed: {config.seed} · generator v{GENERATOR_VERSION} · "
        f"tolerance set {tolerance_set_version}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Match rate | {scores.match_rate:.2%} "
        f"({scores.matched_clean} / {scores.clean_trades}) |",
        f"| False break rate | {scores.false_break_rate:.2%} "
        f"({scores.false_breaks} / {scores.clean_trades}) |",
        f"| Clean trades left unresolved | {scores.unresolved_clean} |",
        f"| Breaks found with the right type | {scores.detection_rate:.2%} "
        f"({scores.found} / {scores.injected}) |",
        f"| Found with the wrong type | {scores.wrong_type} |",
        f"| Missed | {scores.missed} |",
        "",
        "| Break type | Injected | Found | Wrong type | Missed |",
        "|---|---|---|---|---|",
    ]
    for break_type in BreakType:  # enum order, so the table is the same every run
        type_score = scores.by_type.get(break_type)
        if type_score is not None:
            lines.append(
                f"| {break_type.value} | {type_score.injected} | {type_score.found} "
                f"| {type_score.wrong_type} | {type_score.missed} |"
            )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m settlement.evaluation.baseline",
        description="Reconcile generated data and score it against the injected breaks.",
    )
    parser.add_argument("--trades", type=int, default=5000, help="number of trades to generate")
    parser.add_argument("--seed", type=int, default=42, help="same seed, same scores")
    parser.add_argument(
        "--tolerances", type=Path, default=DEFAULT_TOLERANCES, help="tolerance set YAML file"
    )
    args = parser.parse_args(argv)

    config = GenerationConfig(trades=args.trades, seed=args.seed)
    scores = run_baseline(config, args.tolerances)
    print(format_table(scores, config, load_tolerance_set(args.tolerances).version))


if __name__ == "__main__":
    main()
