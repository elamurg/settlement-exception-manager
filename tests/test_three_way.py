"""Tests for three-way reconciliation (SE-12c): legs, counterparty breaks, and scoring."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from settlement.domain.attribution import CsdStatus, Leg, attribute
from settlement.domain.errors import InvalidMatchInput
from settlement.domain.models import Break, BreakType, FieldDifference, StatementLine, Trade
from settlement.domain.tolerance import ToleranceSet
from settlement.evaluation.baseline import DEFAULT_TOLERANCES
from settlement.evaluation.three_way import (
    ThreeWayScores,
    format_table,
    main,
    run_three_way,
    score_three_way,
)
from settlement.generation.generator import GeneratedData, GenerationConfig, generate
from settlement.ingestion.book import BookEntry
from settlement.ingestion.messages import StatusMessage
from settlement.reconciliation.engine import reconcile
from settlement.reconciliation.three_way import (
    ThreeWayReport,
    reconcile_three_way,
    reconcile_three_way_files,
)
from settlement.reconciliation.tolerances import load_tolerance_set

RAISED_AT = datetime(2026, 10, 5, 18, tzinfo=UTC)


@pytest.fixture(scope="module")
def data() -> GeneratedData:
    return generate(GenerationConfig(trades=300, seed=3, counterparty_break_rate=0.0))


@pytest.fixture(scope="module")
def trade(data: GeneratedData) -> Trade:
    return data.book[0]


def line_for(data: GeneratedData, trade: Trade) -> StatementLine:
    (line,) = [line for line in data.statement if line.reference == trade.trade_id]
    return line


def a_break(trade: Trade, line: StatementLine | None) -> Break:
    return Break(
        break_id=f"B-QUANTITY_BREAK-{trade.trade_id}",
        break_type=BreakType.QUANTITY_BREAK,
        ours=trade,
        theirs=line,
        evidence=(FieldDifference("quantity", ours="1000", theirs="900"),),
        intended_settlement_date=trade.intended_settlement_date,
        raised_at=RAISED_AT,
    )


def matched(reference: str) -> CsdStatus:
    return CsdStatus(reference, matched=True, reason=None)


def not_matched(reference: str, reason: str = "DQUA") -> CsdStatus:
    return CsdStatus(reference, matched=False, reason=reason)


# attribute(): one situation per test


def test_ticket_done_when(data: GeneratedData, trade: Trade) -> None:
    """A break between book and custodian, absent between custodian and CSD, is ours."""
    brk = a_break(trade, line_for(data, trade))

    result = attribute([brk], [], [matched(trade.trade_id)], [trade.trade_id], raised_at=RAISED_AT)

    assert result.legs[brk.break_id] == Leg.INTERNAL


def test_break_on_both_legs(data: GeneratedData, trade: Trade) -> None:
    brk = a_break(trade, line_for(data, trade))

    result = attribute(
        [brk], [], [not_matched(trade.trade_id)], [trade.trade_id], raised_at=RAISED_AT
    )

    assert result.legs[brk.break_id] == Leg.BOTH


def test_break_with_no_line_and_no_status_has_unknown_leg(trade: Trade) -> None:
    brk = a_break(trade, None)

    result = attribute([brk], [], [], [trade.trade_id], raised_at=RAISED_AT)

    assert result.legs[brk.break_id] == Leg.UNKNOWN


def test_both_bookings_of_a_duplicate_use_the_shared_lines_status(
    data: GeneratedData, trade: Trade
) -> None:
    """The status is filed under the line's reference, which only one booking has."""
    copy = replace(trade, trade_id="T-COPY")
    line = line_for(data, trade)
    breaks = [replace(a_break(t, line), break_id=f"B-{t.trade_id}") for t in (trade, copy)]

    result = attribute(
        breaks, [], [matched(trade.trade_id)], [trade.trade_id, "T-COPY"], raised_at=RAISED_AT
    )

    assert set(result.legs.values()) == {Leg.INTERNAL}


def test_clean_trade_not_matched_at_the_csd_becomes_a_counterparty_break(trade: Trade) -> None:
    result = attribute(
        [], [trade], [not_matched(trade.trade_id, "DQUA")], [trade.trade_id], raised_at=RAISED_AT
    )

    (brk,) = result.counterparty_breaks
    assert brk.break_type == BreakType.QUANTITY_BREAK
    assert (brk.ours, brk.theirs) == (trade, None)
    assert brk.evidence == (FieldDifference("csd_status", ours="MACH", theirs="NMAT/DQUA"),)
    assert result.legs[brk.break_id] == Leg.COUNTERPARTY


def test_clean_trade_matched_at_the_csd_stays_clean(trade: Trade) -> None:
    result = attribute(
        [], [trade], [matched(trade.trade_id)], [trade.trade_id], raised_at=RAISED_AT
    )

    assert (result.counterparty_breaks, result.awaiting_status, dict(result.legs)) == ((), (), {})


def test_clean_trade_with_no_status_is_awaiting(trade: Trade) -> None:
    result = attribute([], [trade], [], [trade.trade_id], raised_at=RAISED_AT)

    assert result.awaiting_status == (trade.trade_id,)
    assert result.counterparty_breaks == ()


def test_unknown_reason_code_is_left_for_a_human(trade: Trade) -> None:
    status = not_matched(trade.trade_id, "ZZZZ")

    result = attribute([], [trade], [status], [trade.trade_id], raised_at=RAISED_AT)

    assert result.unrecognised == (status,)
    assert result.counterparty_breaks == ()


def test_status_for_a_trade_we_dont_have_is_stray(trade: Trade) -> None:
    stray = matched("T-NOT-OURS")

    result = attribute(
        [], [trade], [matched(trade.trade_id), stray], [trade.trade_id], raised_at=RAISED_AT
    )

    assert result.stray_statuses == (stray,)


def test_two_statuses_for_one_instruction_are_rejected(trade: Trade) -> None:
    with pytest.raises(InvalidMatchInput, match="Two CSD statuses"):
        attribute(
            [],
            [trade],
            [matched(trade.trade_id), not_matched(trade.trade_id)],
            [trade.trade_id],
            raised_at=RAISED_AT,
        )


# reconcile_three_way(): the engine wrapper


def run_engine(
    data: GeneratedData, statuses: list[StatusMessage]
) -> tuple[list[BookEntry], ToleranceSet, ThreeWayReport]:
    book = [
        BookEntry(t, data.ssi_for(t).custodian_bic, data.ssi_for(t).safekeeping_account)
        for t in data.book
    ]
    tolerances = load_tolerance_set(DEFAULT_TOLERANCES)
    return (
        book,
        tolerances,
        reconcile_three_way(book, data.statement, statuses, tolerances, raised_at=RAISED_AT),
    )


def as_messages(statuses: tuple[CsdStatus, ...]) -> list[StatusMessage]:
    return [
        StatusMessage(f"STAT-{n}", s.instruction_reference, s.matched, s.reason)
        for n, s in enumerate(statuses, start=1)
    ]


def test_two_way_part_is_unchanged(data: GeneratedData) -> None:
    book, tolerances, report = run_engine(data, as_messages(data.statuses))

    assert report.two_way == reconcile(book, data.statement, tolerances, raised_at=RAISED_AT)


def test_counterparty_breaks_join_the_queue_and_leave_clean(data: GeneratedData) -> None:
    _, _, before = run_engine(data, as_messages(data.statuses))
    victim = before.clean[0].match.trade.trade_id  # clean on both legs to start with
    statuses = tuple(
        replace(s, matched=False, reason="CMIS") if s.instruction_reference == victim else s
        for s in data.statuses
    )

    _, _, report = run_engine(data, as_messages(statuses))

    (brk,) = report.counterparty_breaks
    assert brk.break_type == BreakType.UNMATCHED_INSTRUCTION
    assert brk in report.breaks
    assert victim not in {result.match.trade.trade_id for result in report.clean}
    assert len(report.clean) == len(before.clean) - 1


def test_every_break_has_a_leg(data: GeneratedData) -> None:
    _, _, report = run_engine(data, as_messages(data.statuses))

    assert set(report.legs) == {brk.break_id for brk in report.breaks}


# Scoring against the manifest


@pytest.fixture(scope="module")
def scores_42() -> ThreeWayScores:
    return run_three_way(GenerationConfig(trades=2000, seed=42), DEFAULT_TOLERANCES)


def test_no_break_lands_on_the_wrong_leg(scores_42: ThreeWayScores) -> None:
    assert scores_42.internal_wrong_leg == 0
    assert scores_42.internal_right_leg + scores_42.internal_leg_unknown == scores_42.internal_found


def test_every_counterparty_break_is_found(scores_42: ThreeWayScores) -> None:
    assert scores_42.counterparty_injected > 0
    assert scores_42.counterparty_found == scores_42.counterparty_injected
    assert scores_42.false_counterparty_breaks == 0


def test_same_seed_gives_the_same_scores(scores_42: ThreeWayScores) -> None:
    assert run_three_way(GenerationConfig(trades=2000, seed=42), DEFAULT_TOLERANCES) == scores_42


def test_a_wrongly_typed_counterparty_break_is_counted(data: GeneratedData) -> None:
    with_breaks = generate(GenerationConfig(trades=300, seed=3, counterparty_break_rate=0.2))
    item = with_breaks.counterparty_breaks[0]
    wrong_reason = "DDAT" if item.reason != "DDAT" else "DQUA"
    statuses = tuple(
        replace(s, reason=wrong_reason) if s.instruction_reference == item.trade_id else s
        for s in with_breaks.statuses
    )
    _, _, report = run_engine(with_breaks, as_messages(statuses))

    scores = score_three_way(report, with_breaks)

    assert scores.counterparty_wrong_type == 1
    assert scores.counterparty_found == scores.counterparty_injected - 1


def test_table_and_command_line(
    scores_42: ThreeWayScores, capsys: pytest.CaptureFixture[str]
) -> None:
    table = format_table(scores_42, GenerationConfig(trades=2000, seed=42))
    assert "| Metric | Value |\n|---|---|" in table

    main(["--trades", "300", "--seed", "1"])
    assert "Counterparty breaks found" in capsys.readouterr().out


def test_missing_statuses_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        reconcile_three_way_files(
            tmp_path / "book.csv",
            tmp_path / "statement.csv",
            tmp_path / "statuses.txt",
            DEFAULT_TOLERANCES,
            raised_at=RAISED_AT,
        )
