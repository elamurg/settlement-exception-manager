"""Score a three-way reconciliation against the generator's manifest (SE-12).

Two questions: did each book vs custodian break land on the right leg, and were the breaks
only the CSD can see found? Run it with:

    python -m settlement.evaluation.three_way --trades 5000 --seed 42
"""

import argparse
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from settlement.domain.attribution import Leg
from settlement.domain.models import BreakType
from settlement.evaluation.baseline import DEFAULT_TOLERANCES
from settlement.generation.files import BOOK_FILE, STATEMENT_FILE, STATUSES_FILE, write_all
from settlement.generation.generator import GeneratedData, GenerationConfig, generate
from settlement.reconciliation.three_way import ThreeWayReport, reconcile_three_way_files


@dataclass(frozen=True, slots=True)
class ThreeWayScores:
    """Counts from one scored run. Rates are worked out from the counts."""

    # Leg 1: injected book vs custodian breaks that the engine raised a break for.
    internal_found: int
    internal_right_leg: int  # ...tagged INTERNAL, as they should be
    internal_leg_unknown: int  # ...tagged UNKNOWN: no CSD status to tell from
    internal_wrong_leg: int  # ...tagged anything else
    # Leg 2: injected counterparty breaks.
    counterparty_injected: int
    counterparty_found: int  # a COUNTERPARTY break of the right type on that trade
    counterparty_wrong_type: int  # a COUNTERPARTY break, but of another type
    counterparty_missed: int  # no counterparty break raised
    false_counterparty_breaks: int  # raised on a trade with no injected leg-2 break

    @property
    def leg_accuracy(self) -> float:
        return self.internal_right_leg / self.internal_found if self.internal_found else 0.0

    @property
    def counterparty_detection_rate(self) -> float:
        if not self.counterparty_injected:
            return 0.0
        return self.counterparty_found / self.counterparty_injected


def score_three_way(report: ThreeWayReport, data: GeneratedData) -> ThreeWayScores:
    """Compare a three-way report with the breaks that were injected on each leg."""
    legs_by_trade: defaultdict[str, set[Leg]] = defaultdict(set)
    for brk in report.two_way.breaks:
        if brk.ours is not None:
            legs_by_trade[brk.ours.trade_id].add(report.legs[brk.break_id])

    internal_found = right = unknown = wrong = 0
    for injected in data.breaks:
        ids = (injected.trade_id, *injected.related_trade_ids)
        legs = set().union(*(legs_by_trade.get(trade_id, set()) for trade_id in ids))
        if not legs:
            continue  # not raised at all: SE-11's baseline already counts it as missed
        internal_found += 1
        if Leg.INTERNAL in legs:
            right += 1
        elif legs == {Leg.UNKNOWN}:
            unknown += 1
        else:
            wrong += 1

    raised: dict[str, BreakType] = {}
    for brk in report.counterparty_breaks:
        if brk.ours is not None:
            raised[brk.ours.trade_id] = brk.break_type
    injected_types = {item.trade_id: item.break_type for item in data.counterparty_breaks}

    found = sum(1 for trade_id, kind in injected_types.items() if raised.get(trade_id) == kind)
    wrong_type = sum(
        1 for trade_id, kind in injected_types.items() if raised.get(trade_id) not in (None, kind)
    )

    return ThreeWayScores(
        internal_found=internal_found,
        internal_right_leg=right,
        internal_leg_unknown=unknown,
        internal_wrong_leg=wrong,
        counterparty_injected=len(injected_types),
        counterparty_found=found,
        counterparty_wrong_type=wrong_type,
        counterparty_missed=len(injected_types) - found - wrong_type,
        false_counterparty_breaks=len(raised.keys() - injected_types.keys()),
    )


def run_three_way(config: GenerationConfig, tolerance_path: Path) -> ThreeWayScores:
    """Generate data, reconcile it three ways through real files, and score it."""
    data = generate(config)
    raised_at = datetime.combine(config.trade_date, time(18, 0), tzinfo=UTC)
    with tempfile.TemporaryDirectory() as folder:
        out_dir = Path(folder)
        write_all(data, out_dir)
        report = reconcile_three_way_files(
            out_dir / BOOK_FILE,
            out_dir / STATEMENT_FILE,
            out_dir / STATUSES_FILE,
            tolerance_path,
            raised_at=raised_at,
        )
    return score_three_way(report, data)


def format_table(scores: ThreeWayScores, config: GenerationConfig) -> str:
    """The scores as a markdown table, with what's needed to reproduce them."""
    return "\n".join(
        [
            f"Trades: {config.trades} · seed: {config.seed} · "
            f"counterparty break rate: {config.counterparty_break_rate}",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Leg 1 breaks put on the right leg | {scores.leg_accuracy:.2%} "
            f"({scores.internal_right_leg} / {scores.internal_found}) |",
            f"| Leg 1 breaks with no CSD status (unknown leg) | {scores.internal_leg_unknown} |",
            f"| Leg 1 breaks on the wrong leg | {scores.internal_wrong_leg} |",
            f"| Counterparty breaks found | {scores.counterparty_detection_rate:.2%} "
            f"({scores.counterparty_found} / {scores.counterparty_injected}) |",
            f"| Counterparty breaks with the wrong type | {scores.counterparty_wrong_type} |",
            f"| Counterparty breaks missed | {scores.counterparty_missed} |",
            f"| False counterparty breaks | {scores.false_counterparty_breaks} |",
        ]
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m settlement.evaluation.three_way",
        description="Reconcile generated data three ways and score the legs.",
    )
    parser.add_argument("--trades", type=int, default=5000, help="number of trades to generate")
    parser.add_argument("--seed", type=int, default=42, help="same seed, same scores")
    parser.add_argument(
        "--tolerances", type=Path, default=DEFAULT_TOLERANCES, help="tolerance set YAML file"
    )
    args = parser.parse_args(argv)

    config = GenerationConfig(trades=args.trades, seed=args.seed)
    print(format_table(run_three_way(config, args.tolerances), config))


if __name__ == "__main__":
    main()
