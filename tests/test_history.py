"""Tests for rebuilding past state from audit events (SE-15c).

The replay itself is pure and needs no database. The last group runs a trade through the
real lifecycle, stores every event in MongoDB, and rebuilds its status at past moments:
the ticket's "Done when".
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pymongo import MongoClient

from settlement.domain.errors import InvalidModel
from settlement.domain.history import StateAsOf, state_as_of
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.lifecycle import transition
from settlement.domain.models import (
    AuditEvent,
    Party,
    SettlementMethod,
    Side,
    Trade,
    TradeStatus,
)
from settlement.storage.audit import COLLECTION, AuditStore

MONDAY = datetime(2026, 10, 5, tzinfo=UTC)


def at(hour: int, minute: int = 0) -> datetime:
    return MONDAY + timedelta(hours=hour, minutes=minute)


def change(event_id: str, when: datetime, before: str | None, after: str | None) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        entity_type="Trade",
        entity_id="T-1",
        event_type="STATUS_CHANGED",
        actor=f"user-{event_id}",
        occurred_at=when,
        before=before,
        after=after,
    )


HISTORY = [
    change("E-1", at(9), "BOOKED", "ALLOCATED"),
    change("E-2", at(10), "ALLOCATED", "CONFIRMED"),
    change("E-3", at(11), "CONFIRMED", "AFFIRMED"),
]


# The replay, without a database


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (at(10, 30), "CONFIRMED"),  # between E-2 and E-3
        (at(9, 0), "ALLOCATED"),  # exactly when E-1 happened: it counts
        (at(10, 59), "CONFIRMED"),
        (at(11, 0), "AFFIRMED"),
        (at(23, 0), "AFFIRMED"),  # after everything: the latest state
    ],
)
def test_state_at_a_moment(moment: datetime, expected: str) -> None:
    result = state_as_of(HISTORY, moment)

    assert result is not None
    assert result.state == expected


def test_result_says_when_and_who() -> None:
    assert state_as_of(HISTORY, at(10, 30)) == StateAsOf(
        state="CONFIRMED", since=at(10), changed_by="user-E-2", event_id="E-2"
    )


def test_before_anything_happened_gives_none() -> None:
    assert state_as_of(HISTORY, at(8, 59)) is None


def test_no_events_gives_none() -> None:
    assert state_as_of([], at(12)) is None


def test_order_given_doesnt_matter() -> None:
    assert state_as_of(list(reversed(HISTORY)), at(10, 30)) == state_as_of(HISTORY, at(10, 30))


def test_a_gap_in_the_history_is_refused() -> None:
    """E-2 is missing: E-3 starts from CONFIRMED, but E-1 left it at ALLOCATED."""
    with pytest.raises(InvalidModel, match="doesn't join up: E-3 starts from 'CONFIRMED'"):
        state_as_of([HISTORY[0], HISTORY[2]], at(12))


def test_a_gap_after_the_moment_doesnt_matter() -> None:
    """Asking about 9:30 only needs E-1; a broken event later can't change that answer."""
    broken_later = change("E-9", at(15), "SETTLED", "CANCELLED")

    result = state_as_of([HISTORY[0], broken_later], at(9, 30))

    assert result is not None
    assert result.state == "ALLOCATED"


def test_an_event_that_leaves_no_state_is_refused() -> None:
    with pytest.raises(InvalidModel, match="E-4"):
        state_as_of([HISTORY[0], change("E-4", at(10), "ALLOCATED", None)], at(12))


def test_moment_must_have_a_timezone() -> None:
    with pytest.raises(InvalidModel, match="timezone"):
        state_as_of(HISTORY, datetime(2026, 10, 5, 12, 0))


# Through MongoDB, using the real lifecycle


@pytest.fixture
def store() -> Iterator[AuditStore]:
    url = os.environ.get("MONGO_URL")
    if not url:
        pytest.skip("MONGO_URL not set; start MongoDB with docker compose to run this")
    client: MongoClient[dict[str, Any]] = MongoClient(url, tz_aware=True)
    database = f"test_history_{uuid.uuid4().hex[:8]}"
    yield AuditStore(client[database][COLLECTION])
    client.drop_database(database)
    client.close()


def a_booked_trade() -> Trade:
    return Trade(
        trade_id="T-1",
        isin=ISIN("DE0007164600"),
        side=Side.BUY,
        quantity=Decimal("1000"),
        price=Decimal("150.25"),
        consideration=Decimal("150250.00"),
        currency="EUR",
        counterparty=Party("CP-1", "Example Bank plc", LEI("HWUPKR0MPOU8FGXBT394")),
        trade_date=date(2026, 10, 5),
        intended_settlement_date=date(2026, 10, 6),
        settlement_method=SettlementMethod.DVP,
    )


def test_done_when_state_is_rebuilt_from_events_alone(store: AuditStore) -> None:
    """SE-15: move a trade through the real lifecycle, keep only the events, rebuild."""
    trade = a_booked_trade()
    status_at: dict[datetime, TradeStatus] = {}
    steps = [
        (TradeStatus.ALLOCATED, at(9)),
        (TradeStatus.CONFIRMED, at(10)),
        (TradeStatus.AFFIRMED, at(11)),
        (TradeStatus.INSTRUCTED, at(12)),
        (TradeStatus.MATCHED, at(13)),
        (TradeStatus.SETTLED, at(15)),
    ]
    for number, (target, when) in enumerate(steps, start=1):
        trade, audit_event = transition(
            trade, target, actor="system", at=when, event_id=f"E-{number}"
        )
        store.append(audit_event)
        status_at[when + timedelta(minutes=30)] = trade.status  # what it was half an hour later

    for moment, expected in status_at.items():
        rebuilt = store.state_as_of("Trade", "T-1", moment)
        assert rebuilt is not None
        assert rebuilt.state == expected.value

    assert store.state_as_of("Trade", "T-1", at(8)) is None  # before the first change


def test_another_trades_events_dont_leak_in(store: AuditStore) -> None:
    store.append(HISTORY[0])
    store.append(
        AuditEvent("E-X", "Trade", "T-2", "STATUS_CHANGED", "system", at(10), "SETTLED", "FAILED")
    )

    rebuilt = store.state_as_of("Trade", "T-1", at(12))

    assert rebuilt is not None
    assert rebuilt.state == "ALLOCATED"
