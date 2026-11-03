"""Tests for the evaluation runner (SE-18), on a small golden file so they run fast."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from settlement.evaluation.runner import (
    RunConfig,
    current_commit,
    format_markdown,
    main,
    run,
    to_json,
    write_reports,
)

RUN_AT = datetime(2026, 11, 3, 10, 15, tzinfo=UTC)

# 100 generated trades, plus two cases: one the engine gets right, one it doesn't (it can't
# read corporate-action notices yet, so a split comes out as a quantity break).
SMALL_GOLDEN = """\
version: "test"
generated:
  trades: "100"
  seed: "1"
  generator_version: "1"
cases:
  - id: "clean"
    kind: "MUST_BE_CLEAN"
    title: "A perfect match"
    rationale: "Nothing differs, so nothing should be raised."
    book:
      - trade_id: "T-1"
    statement:
      - reference: "T-1"
    expected:
      clean: ["T-1"]
  - id: "split"
    kind: "CORPORATE_ACTION"
    title: "A 2-for-1 split"
    rationale: "The notice explains the doubled quantity."
    book:
      - trade_id: "T-1"
    statement:
      - reference: "T-1"
        quantity: "2000"
    corporate_actions:
      - type: "SPLIT"
        isin: "DE0007164600"
        effective_date: "2026-10-06"
        new_shares: "2"
        old_shares: "1"
    expected:
      breaks:
        - type: "CORPORATE_ACTION"
          trade: "T-1"
"""


@pytest.fixture
def config(tmp_path: Path) -> RunConfig:
    golden = tmp_path / "golden.yaml"
    golden.write_text(SMALL_GOLDEN, encoding="utf-8")
    return RunConfig(name="test-run", golden=golden)


def test_each_case_passes_or_fails_with_what_happened(config: RunConfig) -> None:
    result = run(config, run_at=RUN_AT, commit="abc1234")

    clean, split = result.case_results
    assert clean.passed
    assert not split.passed
    assert split.rows == (("split/T-1", "broken: CORPORATE_ACTION", "broken: QUANTITY_BREAK"),)


def test_scores_are_kept_apart_and_together(config: RunConfig) -> None:
    result = run(config, run_at=RUN_AT, commit="abc1234")

    assert result.cases.match_rate.total == 1  # only the clean case should come out clean
    assert result.overall.match_rate.total == result.generated.match_rate.total + 1


def test_the_same_run_scores_the_same(config: RunConfig) -> None:
    """Seeded data and a fixed raised_at: only the run time and commit may differ."""
    first = run(config, run_at=RUN_AT, commit="abc1234")
    second = run(config, run_at=RUN_AT, commit="abc1234")

    assert to_json(first) == to_json(second)


def test_markdown_report_says_what_was_run(config: RunConfig) -> None:
    text = format_markdown(run(config, run_at=RUN_AT, commit="abc1234"))

    assert "# Evaluation run: test-run" in text
    assert "| Commit | `abc1234` |" in text
    assert "## Hand-written cases: 1 of 2 passed" in text
    assert "| `split` | CORPORATE_ACTION | T-1 broken: CORPORATE_ACTION |" in text


def test_reports_are_named_by_time_and_config(config: RunConfig, tmp_path: Path) -> None:
    result = run(config, run_at=RUN_AT, commit="abc1234")

    markdown, data = write_reports(result, tmp_path / "reports")

    assert markdown.name == "2026-11-03T1015Z-test-run.md"
    assert data.name == "2026-11-03T1015Z-test-run.json"
    loaded = json.loads(data.read_text(encoding="utf-8"))
    assert loaded["commit"] == "abc1234"
    assert loaded["cases"][1]["passed"] is False


def test_commit_is_recorded() -> None:
    """Either a short hash (maybe with uncommitted changes on top) or an honest unknown."""
    commit = current_commit()

    assert commit.split()[0].isalnum()


def test_command_line_writes_both_reports(config: RunConfig, tmp_path: Path) -> None:
    out = tmp_path / "out"

    main(["--name", "cli", "--golden", str(config.golden), "--out", str(out)])

    assert sorted(path.suffix for path in out.iterdir()) == [".json", ".md"]
