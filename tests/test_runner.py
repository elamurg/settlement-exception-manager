"""Tests for the evaluation runner (SE-18), on a small golden file so they run fast."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from settlement.agents.classifier import load_classifier_prompt
from settlement.agents.client import ModelReply, ModelRequest
from settlement.agents.fake import reply
from settlement.agents.ledger import InMemoryLedger
from settlement.agents.pricing import PRICE_LIST_V1, load_price_list
from settlement.agents.runner import AgentContext
from settlement.domain.models import BreakType
from settlement.evaluation.classification import ClassifierSetup
from settlement.evaluation.metrics import Ratio
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


class EvidenceEcho:
    """A model stand-in that always cites the break's first evidence field and answers
    CORPORATE_ACTION: right for the split, so the report shows the classifier beating the
    engine there."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        field = json.loads(request.messages[0].text)["evidence"][0]["field"]
        answer = {
            "root_cause": "CORPORATE_ACTION",
            "evidence_fields": [field],
            "confidence": "0.6",
            "explanation": "test",
        }
        return reply(json.dumps(answer))


def classifier_setup(client: EvidenceEcho, limit: int | None) -> ClassifierSetup:
    context = AgentContext(
        client=client,
        ledger=InMemoryLedger(),
        prices=load_price_list(PRICE_LIST_V1),
        clock=lambda: RUN_AT,
    )
    return ClassifierSetup(prompt=load_classifier_prompt("v1"), context=context, limit=limit)


def test_the_classifier_starts_with_the_hand_written_cases(config: RunConfig) -> None:
    client = EvidenceEcho()

    result = run(config, RUN_AT, "abc1234", classifier=classifier_setup(client, limit=1))

    scores = result.classification
    assert scores is not None
    assert (scores.records, scores.breaks, len(client.requests)) == (1, 1, 1)
    assert scores.engine[BreakType.CORPORATE_ACTION].recall == Ratio(0, 1)
    assert scores.classifier[BreakType.CORPORATE_ACTION].recall == Ratio(1, 1)
    assert scores.cost == Decimal("0.014")


def test_the_report_shows_the_classifier_next_to_the_engine(config: RunConfig) -> None:
    result = run(config, RUN_AT, "abc1234", classifier=classifier_setup(EvidenceEcho(), limit=1))

    text = format_markdown(result)
    data = to_json(result)

    assert "| Pipeline | exact match → tolerance → fuzzy pass → classifier v1 |" in text
    assert "## Classifier: prompt v1" in text
    assert "1 breaks on 1 records, the first 1 only. Cost $0.0140" in text
    assert data["classifier"]["cost"] == "0.014"


def test_a_run_without_the_classifier_says_so(config: RunConfig) -> None:
    result = run(config, RUN_AT, "abc1234")

    assert result.classification is None
    assert to_json(result)["classifier"] is None
    assert "(deterministic, no agents)" in format_markdown(result)


def test_limit_without_classifier_is_refused(config: RunConfig) -> None:
    with pytest.raises(SystemExit):
        main(["--name", "cli", "--golden", str(config.golden), "--limit", "5"])
