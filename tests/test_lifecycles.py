"""Tests for settlement.domain.lifecycle."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import product
from pathlib import Path

import pytest

from settlement.domain.errors import IllegalTransition
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.lifecycle import can_transition, transition
from settlement.domain.models import Party, SettlementMethod, Side, Trade, TradeStatus

S = TradeStatus

LEGAL: set[tuple[TradeStatus, TradeStatus]] = {
    (S.BOOKED, S.ALLOCATED),
    (S.ALLOCATED, S.CONFIRMED),
    (S.CONFIRMED, S.AFFIRMED),
    (S.AFFIRMED, S.INSTRUCTED),
    (S.INSTRUCTED, S.MATCHED),
    (S.INSTRUCTED, S.FAILED),
    (S.MATCHED, S.SETTLED),
    (S.MATCHED, S.PARTIALLY_SETTLED),
    (S.MATCHED, S.FAILED),
    (S.PARTIALLY_SETTLED, S.SETTLED),
    (S.PARTIALLY_SETTLED, S.FAILED),
    (S.FAILED, S.RESOLVED),
    (S.FAILED, S.CANCELLED),
}
ILLEGAL = [pair for pair in product(S, S) if pair not in LEGAL]

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def make_trade(status: TradeStatus = S.BOOKED) -> Trade:
    return Trade(
        trade_id="T1",
        isin=ISIN("US0378331005"),
        side=Side.BUY,
        quantity=Decimal("100"),
        price=Decimal("10"),
        consideration=Decimal("1000"),
        currency="GBP",
        counterparty=Party("P1", "Barclays", LEI("5493001KJTIIGC8Y1R12")),
        trade_date=date(2026, 10, 5),
        intended_settlement_date=date(2026, 10, 6),
        settlement_method=SettlementMethod.DVP,
        status=status,
    )


# the thing after the @ means that it loops the same test several times, once for each value
# in the list (LEGAL or ILLEGAL) -> like recursion basically
@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_legal_transaction_succeeds(current: TradeStatus, target: TradeStatus) -> None:
    """Every allowed move works"""
    new_trade, event = transition(
        make_trade(current), target, actor="barclays", at=NOW, event_id="E1"
    )

    assert new_trade.status == target
    assert (event.before, event.after) == (current.value, target.value)


@pytest.mark.parametrize(("current", "target"), ILLEGAL)
def test_illegal_transaction_failed(current: TradeStatus, target: TradeStatus) -> None:
    """Every forbidden move is refused."""
    with pytest.raises(IllegalTransition):
        transition(make_trade(current), target, actor="Barclays", at=NOW, event_id="E2")


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_can_transition_matches_table(current: TradeStatus, target: TradeStatus) -> None:
    assert can_transition(current, target) == ((current, target) in LEGAL)


# other tests follow an arrange(set up what you need), act (call the one thing youre testing),
# assert (check the result by field) set up
def test_original_trade_unchanged() -> None:
    """After transition, trade.status is still the old status."""
    trade = make_trade(TradeStatus.BOOKED)
    transition(trade, TradeStatus.ALLOCATED, actor="barclays", at=NOW, event_id="E1")

    assert trade.status == TradeStatus.BOOKED


def test_event_records_inputs() -> None:
    trade = make_trade(S.BOOKED)
    _, event = transition(trade, TradeStatus.ALLOCATED, actor="barclays", at=NOW, event_id="E1")

    assert event.event_id == "E1"
    assert event.entity_type == "Trade"
    assert event.entity_id == "T1"
    assert event.event_type == "STATUS_CHANGED"
    assert event.actor == "barclays"
    assert event.occurred_at == NOW
    assert event.before == "BOOKED"
    assert event.after == "ALLOCATED"


def test_happy_path_to_settled() -> None:
    path = [S.ALLOCATED, S.CONFIRMED, S.AFFIRMED, S.INSTRUCTED, S.MATCHED, S.SETTLED]
    trade = make_trade(S.BOOKED)
    events = []

    for i, target in enumerate(path):
        trade, event = transition(trade, target, actor="Barclays", at=NOW, event_id=f"E{i}")
        events.append(event)

    assert trade.status == S.SETTLED
    assert [e.after for e in events] == [status.value for status in path]


def test_failed_path_to_resolved() -> None:
    path = [S.ALLOCATED, S.CONFIRMED, S.AFFIRMED, S.INSTRUCTED, S.MATCHED, S.FAILED, S.RESOLVED]
    trade = make_trade(S.BOOKED)

    for i, target in enumerate(path):
        trade, _ = transition(trade, target, actor="Barclays", at=NOW, event_id=f"E{i}")

    assert trade.status == S.RESOLVED


# i did not write this test nor do i understand it past the regex function
def test_status_only_changed_in_lifecycle() -> None:
    """No module except lifecycle.py can change a status via dataclasses"""
    src = Path(__file__).parent.parent / "src"
    pattern = re.compile(r"replace\([^)]*\bstatus\s*=")
    offenders = [
        path.name
        for path in src.rglob("*.py")
        if path.name != "lifecycle.py" and pattern.search(path.read_text())
    ]
    assert offenders == []
