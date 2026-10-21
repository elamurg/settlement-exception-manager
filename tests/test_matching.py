"""Tests for settlement.domain.matching."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from itertools import chain
from typing import Any

import pytest

from settlement.domain.errors import InvalidMatchInput
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.matching import MatchResult, key_of_line, key_of_trade, match_exact
from settlement.domain.models import Party, SettlementMethod, Side, StatementLine, Trade

ISIN_SAP = ISIN("DE0007164600")
LEI_BANK = LEI("HWUPKR0MPOU8FGXBT394")
SETTLES = date(2026, 10, 9)

BASE_TRADE = Trade(
    trade_id="T1",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    price=Decimal("150.25"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty=Party(party_id="CP-001", name="Barclays", lei=LEI_BANK),
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


# replace creates a copy with some fields changed
def make_trade(trade_id: str = "T1", **changes: Any) -> Trade:
    """Base trade with a new id and any fields changed."""
    return replace(BASE_TRADE, trade_id=trade_id, **changes)


def make_line(line_id: str = "L1", **changes: Any) -> StatementLine:
    """Base line (same economic key as trade) with new id and any fields changed."""
    return replace(BASE_LINE, line_id=line_id, **changes)


def pairs(result: MatchResult) -> list[tuple[str, str]]:
    """Matched pairs as ids, so asserts read lijke ("T1", "L1")."""
    return [(match.trade.trade_id, match.line.line_id) for match in result.matched]


def test_trade_and_line_with_same_fields_have_equal_keys() -> None:
    assert key_of_trade(make_trade()) == key_of_line(make_line())


def test_quantity_100_and_100_00_are_the_same_key() -> None:
    trade = make_trade(quantity=Decimal("100"))
    line = make_line(quantity=Decimal("100.00"))

    assert key_of_trade(trade) == key_of_line(line)


def test_one_trade_one_line_match() -> None:
    result = match_exact([make_trade()], [make_line()])
    assert pairs(result) == [("T1", "L1")]
    assert result.unmatched_trades == ()
    assert result.unmatched_lines == ()
    assert result.ambiguous == ()


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"isin": ISIN("US0378331005")}, id="isin"),
        pytest.param({"side": Side.SELL}, id="side"),
        pytest.param({"quantity": Decimal("999")}, id="quantity"),
        pytest.param({"intended_settlement_date": date(2026, 10, 12)}, id="settlement_date"),
        pytest.param({"counterparty_lei": LEI("5493001KJTIIGC8Y1R12")}, id="counterparty"),
    ],
)
def test_any_key_field_differing_means_no_match(changes: dict[str, Any]) -> None:
    result = match_exact([make_trade()], [make_line(**changes)])
    assert pairs(result) == []
    assert [trade.trade_id for trade in result.unmatched_trades] == ["T1"]
    assert [line.line_id for line in result.unmatched_lines] == ["L1"]


def test_consideration_difference_still_matches() -> None:
    # consideration is not the key
    trade = make_trade()
    line = make_line(consideration=Decimal("1250"))
    result = match_exact([trade], [line])

    assert pairs(result) == [("T1", "L1")]


def test_several_different_keys_all_match() -> None:
    book = [
        make_trade("T1"),
        make_trade("T2", quantity=Decimal("500")),
        make_trade("T3", side=Side.SELL),
    ]
    statement = [
        make_line("L1"),
        make_line("L2", quantity=Decimal("500")),
        make_line("L3", side=Side.SELL),
    ]
    result = match_exact(book, statement)

    assert pairs(result) == [("T1", "L1"), ("T2", "L2"), ("T3", "L3")]
    assert result.unmatched_trades == ()
    assert result.unmatched_lines == ()
    assert result.ambiguous == ()


# test duplicates also
def test_many_to_one_is_ambiguous() -> None:
    """Testing two trades with the same key and one custodian line."""
    result = match_exact([make_trade("T1"), make_trade("T2")], [make_line("L1")])

    assert pairs(result) == []
    assert result.unmatched_trades == ()
    assert result.unmatched_lines == ()
    assert len(result.ambiguous) == 1

    (group,) = result.ambiguous
    assert [trade.trade_id for trade in group.trades] == ["T1", "T2"]
    assert [line.line_id for line in group.lines] == ["L1"]


def test_one_to_many_is_ambiguous() -> None:
    result = match_exact([make_trade("T1")], [make_line("L1"), make_line("L2")])

    assert pairs(result) == []
    assert result.unmatched_trades == ()
    assert result.unmatched_lines == ()
    assert len(result.ambiguous) == 1

    (group,) = result.ambiguous
    assert [trade.trade_id for trade in group.trades] == ["T1"]
    assert [line.line_id for line in group.lines] == ["L1", "L2"]


def test_duplicates_on_both_sides_are_not_paired_up() -> None:
    result = match_exact(
        [make_trade("T1"), make_trade("T2")],
        [make_line("L1"), make_line("L2")],
    )

    assert pairs(result) == []
    assert result.unmatched_trades == ()
    assert result.unmatched_lines == ()
    assert len(result.ambiguous) == 1

    (group,) = result.ambiguous
    assert [trade.trade_id for trade in group.trades] == ["T1", "T2"]
    assert [line.line_id for line in group.lines] == ["L1", "L2"]


# unmatched
def test_trade_without_line_and_line_without_trade() -> None:
    result = match_exact(
        [make_trade("T1")],
        [make_line("L2", side=Side.SELL)],
    )

    assert pairs(result) == []
    assert [trade.trade_id for trade in result.unmatched_trades] == ["T1"]
    assert [line.line_id for line in result.unmatched_lines] == ["L2"]
    assert result.ambiguous == ()


def test_empty_inputs_give_empty_result() -> None:
    result = match_exact(
        [],
        [],
    )

    assert pairs(result) == []
    assert [trade.trade_id for trade in result.unmatched_trades] == []
    assert [line.line_id for line in result.unmatched_lines] == []
    assert result.ambiguous == ()


# test set properties
# chain = joins several sequences into one
def test_every_record_appears_exactly_once() -> None:
    book = [
        make_trade("T1"),
        make_trade("T2"),
        make_trade("T3", side=Side.SELL),
        make_trade("T4", quantity=Decimal("7")),
    ]
    statement = [
        make_line("L1"),
        make_line("L2", quantity=Decimal("5")),
        make_line("L3", quantity=Decimal("5")),
        make_line("L4", quantity=Decimal("7")),
    ]
    result = match_exact(book, statement)

    trade_ids = list(
        chain(
            (m.trade.trade_id for m in result.matched),
            (t.trade_id for t in result.unmatched_trades),
            (t.trade_id for group in result.ambiguous for t in group.trades),
        )
    )

    line_ids = list(
        chain(
            (m.line.line_id for m in result.matched),
            (line.line_id for line in result.unmatched_lines),
            (line.line_id for group in result.ambiguous for line in group.lines),
        )
    )
    assert sorted(trade_ids) == ["T1", "T2", "T3", "T4"]
    assert sorted(line_ids) == ["L1", "L2", "L3", "L4"]


def test_result_does_not_depend_on_input_order() -> None:
    book = [make_trade("T1"), make_trade("T2"), make_trade("T3", side=Side.SELL)]
    statement = [make_line("L1"), make_line("L2", side=Side.SELL)]

    assert match_exact(book, statement) == match_exact(book[::-1], statement[::-1])


def test_duplicate_trade_id_raises() -> None:
    with pytest.raises(InvalidMatchInput):
        match_exact([make_trade("T1"), make_trade("T1")], [])


def test_duplicate_line_id_raises() -> None:
    with pytest.raises(InvalidMatchInput):
        match_exact([], [make_line("L1"), make_line("L1")])
