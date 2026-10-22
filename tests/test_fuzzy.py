"""Tests for settlement.domain.fuzzy"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from settlement.domain.errors import InvalidMatchInput
from settlement.domain.fuzzy import FuzzyResult, SettlementDetails, find_breaks
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.matching import match_exact
from settlement.domain.models import (
    FieldDifference,
    Party,
    SettlementMethod,
    Side,
    StatementLine,
    Trade,
)
from settlement.domain.tolerance import ToleranceRule, ToleranceSet, evaluate

ISIN_SAP = ISIN("DE0007164600")
LEI_BANK = LEI("HWUPKR0MPOU8FGXBT394")
LEI_OTHER = LEI("5493001KJTIIGC8Y1R12")
SETTLES = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 18, 0, tzinfo=UTC)

TOLERANCES = ToleranceSet(
    version="v1",
    effective_from=date(2026, 10, 1),
    rules=(ToleranceRule("*", "*", Decimal("5.00"), Decimal("0")),),
)

BASE_TRADE = Trade(
    trade_id="T1",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    price=Decimal("150.25"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty=Party(party_id="CP-001", name="Chase Bank", lei=LEI_BANK),
    trade_date=date(2026, 10, 8),
    intended_settlement_date=SETTLES,
    settlement_method=SettlementMethod.DVP,
)

BASE_LINE = StatementLine(
    line_id="L1",
    source="custodian",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty_lei=LEI_BANK,
    intended_settlement_date=SETTLES,
    reference="T1",
)

ON_FILE = SettlementDetails(bic="DEUTDEFF", account="SAFE-123")


def make_trade(trade_id: str = "T1", **changes: Any) -> Trade:
    return replace(BASE_TRADE, trade_id=trade_id, **changes)


def make_line(line_id: str = "L1", **changes: Any) -> StatementLine:
    return replace(BASE_LINE, line_id=line_id, **changes)


def run(
    book: list[Trade],
    statement: list[StatementLine],
    details: dict[str, SettlementDetails] | None = None,
) -> FuzzyResult:
    """The real pipeline: exact match, tolerance check, then the fuzzy pass.

    Every trade has ON_FILE as its SSI unless `details` says otherwise, because a missing
    SSI is itself a break.
    """
    if details is None:
        details = {trade.trade_id: ON_FILE for trade in book}
    match_result = match_exact(book, statement)
    tolerance_results = [evaluate(match, TOLERANCES) for match in match_result.matched]
    return find_breaks(match_result, tolerance_results, details, raised_at=NOW)


def found(result: FuzzyResult) -> list[tuple[str, str]]:
    """Breaks as (type, our trade id or their line id), so asserts read like a table."""
    pairs: list[tuple[str, str]] = []
    for brk in result.breaks:
        if brk.ours is not None:
            pairs.append((brk.break_type.value, brk.ours.trade_id))
        elif brk.theirs is not None:
            pairs.append((brk.break_type.value, brk.theirs.line_id))
    return pairs


@pytest.mark.parametrize(
    ("changes", "break_type", "field"),
    [
        pytest.param({"quantity": Decimal("900")}, "QUANTITY_BREAK", "quantity", id="quantity"),
        pytest.param(
            {"intended_settlement_date": date(2026, 10, 12)},
            "DATE_MISMATCH",
            "intended_settlement_date",
            id="date",
        ),
        pytest.param(
            {"counterparty_lei": LEI_OTHER}, "STATIC_DATA", "counterparty_lei", id="counterparty"
        ),
    ],
)
def test_one_near_field_off_gives_typed_break(
    changes: dict[str, Any], break_type: str, field: str
) -> None:
    result = run([make_trade()], [make_line(**changes)])

    assert found(result) == [(break_type, "T1")]
    (brk,) = result.breaks
    assert [difference.field for difference in brk.evidence] == [field]
    assert brk.theirs is not None and brk.theirs.line_id == "L1"
    assert result.unresolved_trades == ()
    assert result.unresolved_lines == ()


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"isin": ISIN("US0378331005")}, id="isin"),
        pytest.param({"side": Side.SELL}, id="side"),
    ],
)
def test_isin_or_side_off_is_not_a_near_match(changes: dict[str, Any]) -> None:
    result = run([make_trade()], [make_line(**changes)])
    assert found(result) == [("UNMATCHED_INSTRUCTION", "T1")]
    assert [line.line_id for line in result.unresolved_lines] == ["L1"]
    assert result.unresolved_trades == ()


def test_two_fields_off_on_our_counterparty_is_unresolved() -> None:
    # Same ISIN, side and counterparty, but quantity AND date differ: probably our line,
    # mis-reported twice. Reporting "no matching line" would send ops chasing the custodian
    # for an instruction that exists, so a human looks at both instead.
    line = make_line(quantity=Decimal("900"), intended_settlement_date=date(2026, 10, 12))
    result = run([make_trade()], [line])

    assert found(result) == []
    assert [trade.trade_id for trade in result.unresolved_trades] == ["T1"]
    assert [line.line_id for line in result.unresolved_lines] == ["L1"]


def test_two_fields_off_with_another_counterparty_is_unmatched() -> None:
    # Quantity and counterparty both differ: not our counterparty's line, so the trade
    # really has no matching line.
    line = make_line(quantity=Decimal("900"), counterparty_lei=LEI_OTHER)
    result = run([make_trade()], [line])

    assert found(result) == [("UNMATCHED_INSTRUCTION", "T1")]
    assert [line.line_id for line in result.unresolved_lines] == ["L1"]


def test_trade_with_no_candidate_is_unmatched_instruction() -> None:
    result = run([make_trade()], [])

    assert found(result) == [("UNMATCHED_INSTRUCTION", "T1")]
    (brk,) = result.breaks
    assert brk.theirs is None
    assert brk.evidence == (
        FieldDifference("statement_line", ours="present", theirs="no matching line"),
    )


def test_two_candidate_lines_leave_trade_unresolved() -> None:
    result = run(
        [make_trade("T1")],
        [make_line("L1", quantity=Decimal("900")), make_line("L2", quantity=Decimal("800"))],
    )

    assert found(result) == []
    assert [trade.trade_id for trade in result.unresolved_trades] == ["T1"]
    assert [line.line_id for line in result.unresolved_lines] == ["L1", "L2"]


def test_two_trades_sharing_one_candidate_line_are_unresolved() -> None:
    result = run(
        [make_trade("T1", quantity=Decimal("1000")), make_trade("T2", quantity=Decimal("1100"))],
        [make_line("L1", quantity=Decimal("900"))],
    )
    assert found(result) == []
    assert [trade.trade_id for trade in result.unresolved_trades] == ["T1", "T2"]
    assert [line.line_id for line in result.unresolved_lines] == ["L1"]


def test_outside_tolerance_is_price_break() -> None:
    line = make_line(consideration=Decimal("150350.00"))
    result = run([make_trade()], [line])
    assert found(result) == [("PRICE_BREAK", "T1")]
    (brk,) = result.breaks
    assert brk.evidence == (FieldDifference("consideration", ours="150250.00", theirs="150350.00"),)
    assert result.unresolved_trades == ()
    assert result.unresolved_lines == ()


def test_within_tolerance_raises_nothing() -> None:
    result = run([make_trade()], [make_line(consideration=Decimal("150253.00"))])
    assert found(result) == []
    assert result.unresolved_trades == ()
    assert result.unresolved_lines == ()


def test_currency_mismatch_is_price_break_on_currency() -> None:
    result = run([make_trade(currency="EUR")], [make_line(currency="GBP")])
    assert found(result) == [("PRICE_BREAK", "T1")]
    (brk,) = result.breaks
    assert brk.evidence == (FieldDifference("currency", ours="EUR", theirs="GBP"),)
    assert result.unresolved_trades == ()
    assert result.unresolved_lines == ()


@pytest.mark.parametrize(
    ("line_changes", "expected"),
    [
        pytest.param(
            {"counterparty_bic": "BARCGB22"},
            FieldDifference("counterparty_bic", ours="DEUTDEFF", theirs="BARCGB22"),
            id="bic",
        ),
        pytest.param(
            {"counterparty_account": "SAFE-999"},
            FieldDifference("counterparty_account", ours="SAFE-123", theirs="SAFE-999"),
            id="account",
        ),
    ],
)
def test_ssi_different_from_file_is_ssi_mismatch(
    line_changes: dict[str, Any], expected: FieldDifference
) -> None:
    result = run([make_trade()], [make_line(**line_changes)], details={"T1": ON_FILE})

    assert found(result) == [("SSI_MISMATCH", "T1")]
    (brk,) = result.breaks
    assert brk.evidence == (expected,)


def test_ssi_not_reported_by_custodian_raises_nothing() -> None:
    # The custodian didn't report settlement details (None), so there's nothing to compare.
    result = run([make_trade()], [make_line()], details={"T1": ON_FILE})
    assert found(result) == []


@pytest.mark.parametrize(
    ("line_changes", "theirs"),
    [
        pytest.param({"counterparty_bic": "BARCGB22"}, "BARCGB22", id="custodian_reports_bic"),
        pytest.param({}, "not reported", id="custodian_reports_nothing"),
    ],
)
def test_no_ssi_on_file_is_static_data(line_changes: dict[str, Any], theirs: str) -> None:
    # A missing SSI is a fail cause in itself: we couldn't have instructed correctly.
    result = run([make_trade()], [make_line(**line_changes)], details={})

    assert found(result) == [("STATIC_DATA", "T1")]
    (brk,) = result.breaks
    assert brk.evidence == (FieldDifference("ssi_on_file", ours="missing", theirs=theirs),)


def test_duplicate_booking_flags_every_booking() -> None:
    result = run([make_trade("T1"), make_trade("T2")], [make_line("L1")])
    assert found(result) == [("DUPLICATE_BOOKING", "T1"), ("DUPLICATE_BOOKING", "T2")]
    for brk in result.breaks:
        assert brk.evidence == (FieldDifference("booking_count", ours="2", theirs="1"),)
        assert brk.theirs is not None and brk.theirs.line_id == "L1"
    assert result.unresolved_trades == ()
    assert result.unresolved_lines == ()
    assert result.unresolved_groups == ()


def test_one_trade_two_lines_group_is_unresolved() -> None:
    result = run([make_trade("T1")], [make_line("L1"), make_line("L2")])

    assert found(result) == []
    (group,) = result.unresolved_groups
    assert [trade.trade_id for trade in group.trades] == ["T1"]
    assert [line.line_id for line in group.lines] == ["L1", "L2"]
    assert result.unresolved_trades == ()
    assert result.unresolved_lines == ()


def test_every_record_ends_up_in_exactly_one_place() -> None:
    book = [
        make_trade("T1"),  # matched, within tolerance
        make_trade("T2", quantity=Decimal("2000")),  # near match: quantity
        make_trade("T3", isin=ISIN("US0378331005")),  # no candidate: unmatched
        make_trade("T4", quantity=Decimal("4000")),  # duplicate booking with T5
        make_trade("T5", quantity=Decimal("4000")),
    ]
    statement = [
        make_line("L1"),
        make_line("L2", quantity=Decimal("2100")),
        make_line("L4", quantity=Decimal("4000")),
        make_line("L9", side=Side.SELL, quantity=Decimal("9000")),  # no trade at all
    ]
    match_result = match_exact(book, statement)
    result = run(book, statement)

    matched_ids = {match.trade.trade_id for match in match_result.matched}
    in_breaks = {brk.ours.trade_id for brk in result.breaks if brk.ours}
    unresolved = {trade.trade_id for trade in result.unresolved_trades}
    in_groups = {trade.trade_id for group in result.unresolved_groups for trade in group.trades}

    assert matched_ids | in_breaks | unresolved | in_groups == {"T1", "T2", "T3", "T4", "T5"}
    assert not (in_breaks & unresolved) and not (in_breaks & in_groups)
    assert [line.line_id for line in result.unresolved_lines] == ["L9"]


def test_result_does_not_depend_on_input_order() -> None:
    book = [
        make_trade("T1"),  # matched, within tolerance
        make_trade("T2", quantity=Decimal("2000")),  # near match: quantity
        make_trade("T3", isin=ISIN("US0378331005")),  # no candidate: unmatched
        make_trade("T4", quantity=Decimal("4000")),  # duplicate booking with T5
        make_trade("T5", quantity=Decimal("4000")),
    ]
    statement = [
        make_line("L1"),
        make_line("L2", quantity=Decimal("2100")),
        make_line("L4", quantity=Decimal("4000")),
        make_line("L9", side=Side.SELL, quantity=Decimal("9000")),  # no trade at all
    ]
    assert run(book, statement) == run(book[::-1], statement[::-1])


def test_tolerance_results_must_cover_matched_pairs() -> None:
    match_result = match_exact([make_trade()], [make_line()])
    with pytest.raises(InvalidMatchInput):
        find_breaks(match_result, [], {}, raised_at=NOW)


def test_ssi_both_fields_differ_gives_one_break_with_two_evidence() -> None:
    line = make_line(counterparty_bic="BARCGB22", counterparty_account="SAFE-999")
    result = run([make_trade()], [line], details={"T1": ON_FILE})

    assert found(result) == [("SSI_MISMATCH", "T1")]
    (brk,) = result.breaks
    assert [d.field for d in brk.evidence] == ["counterparty_bic", "counterparty_account"]


def test_break_id_and_timestamp() -> None:
    result = run([make_trade()], [make_line(quantity=Decimal("900"))])
    (brk,) = result.breaks
    assert brk.break_id == "B-QUANTITY_BREAK-T1"
    assert brk.raised_at == NOW
    assert brk.intended_settlement_date == SETTLES
