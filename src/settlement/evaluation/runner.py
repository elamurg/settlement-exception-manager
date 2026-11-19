"""Run the pipeline over the golden set and write a timestamped report (SE-18).

Run it with: python -m settlement.evaluation.runner --name deterministic

Each run writes <time>-<name>.md for people and <time>-<name>.json for comparing runs, into
reports/ (ignored by git). The recorded baseline is a run written to docs/evaluation/ on
purpose, so it's committed: every later change is judged against it.
"""

import argparse
import json
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from settlement.domain.models import BreakType
from settlement.evaluation.baseline import DEFAULT_TOLERANCES
from settlement.evaluation.golden import GOLDEN_V1, CaseKind, load_golden_set
from settlement.evaluation.metrics import Ratio
from settlement.evaluation.outcomes import (
    Outcomes,
    actual_from_report,
    combine,
    expected_for_case,
    expected_for_generated,
)
from settlement.evaluation.scoring import Scores, describe, passed, score
from settlement.generation.files import BOOK_FILE, STATEMENT_FILE, write_all
from settlement.generation.generator import GENERATOR_VERSION, generate
from settlement.reconciliation.engine import reconcile, reconcile_files
from settlement.reconciliation.tolerances import load_tolerance_set

DEFAULT_OUT = Path("reports")


@dataclass(frozen=True, slots=True)
class RunConfig:
    """What is being evaluated. Grows when the agents arrive (prompt version, model)."""

    name: str
    tolerances: Path = DEFAULT_TOLERANCES
    golden: Path = GOLDEN_V1


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    kind: CaseKind
    passed: bool
    rows: tuple[tuple[str, str, str], ...]  # (record, expected, got) for every record it names


@dataclass(frozen=True, slots=True)
class RunResult:
    config: RunConfig
    run_at: datetime
    commit: str
    golden_version: str
    tolerance_set_version: str
    trades: int
    seed: int
    generated: Scores
    cases: Scores
    overall: Scores
    case_results: tuple[CaseResult, ...]


def run(config: RunConfig, run_at: datetime, commit: str) -> RunResult:
    """Reconcile the generated part and every hand-written case, and score them."""
    golden = load_golden_set(config.golden)
    tolerance_set = load_tolerance_set(config.tolerances)
    # Fixed, not the wall clock, so the same code always gives the same scores.
    raised_at = datetime.combine(golden.generated.trade_date, time(18, 0), tzinfo=UTC)

    data = generate(golden.generated)
    with tempfile.TemporaryDirectory() as folder:
        # Through real files, so the SE-08 parsers are part of what's measured.
        out_dir = Path(folder)
        write_all(data, out_dir)
        report = reconcile_files(
            out_dir / BOOK_FILE, out_dir / STATEMENT_FILE, config.tolerances, raised_at=raised_at
        )
    generated_expected = expected_for_generated(data)
    generated_actual = actual_from_report(report, "generated")

    case_expected: list[Outcomes] = []
    case_actual: list[Outcomes] = []
    case_results = []
    for case in golden.cases:
        # The engine can't read corporate-action notices yet, so they aren't passed in.
        case_report = reconcile(case.book, case.statement, tolerance_set, raised_at=raised_at)
        expected = expected_for_case(case)
        actual = actual_from_report(case_report, case.case_id)
        case_expected.append(expected)
        case_actual.append(actual)
        rows = tuple(
            (record, describe(expected, record), describe(actual, record))
            for record in sorted(expected.records)
        )
        case_results.append(CaseResult(case.case_id, case.kind, passed(expected, actual), rows))

    all_expected = combine([generated_expected, *case_expected])
    all_actual = combine([generated_actual, *case_actual])
    return RunResult(
        config=config,
        run_at=run_at,
        commit=commit,
        golden_version=golden.version,
        tolerance_set_version=tolerance_set.version,
        trades=golden.generated.trades,
        seed=golden.generated.seed,
        generated=score(generated_expected, generated_actual),
        cases=score(combine(case_expected), combine(case_actual)),
        overall=score(all_expected, all_actual),
        case_results=tuple(case_results),
    )


def current_commit() -> str:
    """The commit being measured, and whether there were uncommitted changes on top."""
    try:
        commit = _git("rev-parse", "--short", "HEAD")
        changed = _git("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        return "unknown (not a git checkout)"
    return f"{commit} + uncommitted changes" if changed else commit


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


# Reports


def format_markdown(result: RunResult) -> str:
    config = result.config
    passed_count = sum(case.passed for case in result.case_results)
    lines = [
        f"# Evaluation run: {config.name}",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Run at | {result.run_at:%Y-%m-%d %H:%M} UTC |",
        f"| Commit | `{result.commit}` |",
        f"| Golden set | {result.golden_version} ({config.golden}) |",
        f"| Generated part | {result.trades} trades, seed {result.seed}, "
        f"generator v{GENERATOR_VERSION} |",
        f"| Tolerance set | {result.tolerance_set_version} ({config.tolerances}) |",
        "| Pipeline | exact match → tolerance → fuzzy pass (deterministic, no agents) |",
        "| Corporate-action notices | not given to the engine: it can't read them yet |",
        "",
        "## Scores",
        "",
        "| Metric | Generated | Hand-written cases | All |",
        "|---|---|---|---|",
    ]
    for label, attribute in [
        ("Match rate", "match_rate"),
        ("False break rate", "false_break_rate"),
        ("Left for a human", "left_for_human_rate"),
    ]:
        cells = [
            _ratio(getattr(scores, attribute))
            for scores in (result.generated, result.cases, result.overall)
        ]
        lines.append(f"| {label} | {' | '.join(cells)} |")
    lines += [
        "",
        "Prioritisation, resolution acceptance and cost per break: not measured yet. They need",
        "an ideal queue, resolutions and model costs, which come with the agents.",
        "",
        "## By break type (all)",
        "",
        "| Break type | Precision | Recall |",
        "|---|---|---|",
    ]
    for break_type in BreakType:
        accuracy = result.overall.by_type.get(break_type)
        if accuracy is not None:
            lines.append(
                f"| {break_type.value} | {_ratio(accuracy.precision)} | {_ratio(accuracy.recall)} |"
            )
    lines += [
        "",
        f"## Hand-written cases: {passed_count} of {len(result.case_results)} passed",
        "",
        "| Case | Kind | Right answer | Engine | |",
        "|---|---|---|---|---|",
    ]
    for case in result.case_results:
        right = "; ".join(f"{_short(record)} {want}" for record, want, _ in case.rows)
        got = "; ".join(f"{_short(record)} {have}" for record, _, have in case.rows)
        mark = "✅" if case.passed else "❌"
        lines.append(f"| `{case.case_id}` | {case.kind.value} | {right} | {got} | {mark} |")
    return "\n".join(lines) + "\n"


def to_json(result: RunResult) -> dict[str, Any]:
    config = result.config
    return {
        "name": config.name,
        "run_at": result.run_at.isoformat(),
        "commit": result.commit,
        "golden": {"version": result.golden_version, "path": str(config.golden)},
        "generated_part": {
            "trades": result.trades,
            "seed": result.seed,
            "generator_version": GENERATOR_VERSION,
        },
        "tolerance_set": {"version": result.tolerance_set_version, "path": str(config.tolerances)},
        "scores": {
            "generated": _scores_json(result.generated),
            "cases": _scores_json(result.cases),
            "all": _scores_json(result.overall),
        },
        "cases": [
            {
                "id": case.case_id,
                "kind": case.kind.value,
                "passed": case.passed,
                "records": [
                    {"record": record, "expected": want, "got": have}
                    for record, want, have in case.rows
                ],
            }
            for case in result.case_results
        ],
    }


def write_reports(result: RunResult, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result.run_at:%Y-%m-%dT%H%MZ}-{result.config.name}"
    markdown, data = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
    markdown.write_text(format_markdown(result), encoding="utf-8")
    data.write_text(json.dumps(to_json(result), indent=2) + "\n", encoding="utf-8")
    return markdown, data


def _scores_json(scores: Scores) -> dict[str, Any]:
    return {
        "match_rate": _ratio_json(scores.match_rate),
        "false_break_rate": _ratio_json(scores.false_break_rate),
        "left_for_human_rate": _ratio_json(scores.left_for_human_rate),
        "by_type": {
            break_type.value: {
                "precision": _ratio_json(accuracy.precision),
                "recall": _ratio_json(accuracy.recall),
            }
            for break_type, accuracy in scores.by_type.items()
        },
    }


def _ratio_json(ratio: Ratio) -> dict[str, Any]:
    return {"count": ratio.count, "total": ratio.total, "value": ratio.value}


def _ratio(ratio: Ratio) -> str:
    if ratio.value is None:
        return "n/a (0 / 0)"
    return f"{ratio.value:.2%} ({ratio.count} / {ratio.total})"


def _short(record: str) -> str:
    """ "stock-split/T-1" -> "T-1": inside a case's own row, the prefix is noise."""
    return record.split("/", 1)[1]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m settlement.evaluation.runner",
        description="Run the pipeline over the golden set and write a timestamped report.",
    )
    parser.add_argument("--name", required=True, help="what is being evaluated, e.g. deterministic")
    parser.add_argument("--tolerances", type=Path, default=DEFAULT_TOLERANCES)
    parser.add_argument("--golden", type=Path, default=GOLDEN_V1)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write reports")
    args = parser.parse_args(argv)

    config = RunConfig(name=args.name, tolerances=args.tolerances, golden=args.golden)
    result = run(config, run_at=datetime.now(UTC), commit=current_commit())
    markdown, data = write_reports(result, args.out)
    passed_count = sum(case.passed for case in result.case_results)
    print(
        f"{config.name}: match rate {_ratio(result.overall.match_rate)}, "
        f"{passed_count} of {len(result.case_results)} cases passed"
    )
    print(f"Wrote {markdown} and {data}")


if __name__ == "__main__":
    main()
