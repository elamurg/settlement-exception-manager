from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from settlement.agents.ledger import CostLedger, InMemoryLedger
from settlement.agents.records import CallRecord, Outcome, Usage
from settlement.domain.errors import InvalidModel

RECORD = CallRecord(
    agent="classifier",
    subject="BRK-1",
    prompt_version="v1",
    prompt_fingerprint="abc123def456",
    model="claude-opus-5-5",
    attempt=1,
    outcome=Outcome.OK,
    usage=Usage(input_tokens=1000, output_tokens=500),
    cost=Decimal("0.014"),
    at=datetime(2026, 11, 12, 9, 30, tzinfo=UTC),
)


@pytest.mark.parametrize(
    "change",
    [
        {"agent": " "},
        {"subject": ""},
        {"attempt": 0},
        {"cost": Decimal("-0.01")},
        {"at": datetime(2026, 11, 12, 9, 30)},  # no timezone
    ],
)
def test_a_record_that_cannot_be_costed_or_attributed_is_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(InvalidModel):
        replace(RECORD, **change)


def test_the_ledger_keeps_every_record_and_adds_up_the_cost() -> None:
    ledger = InMemoryLedger()

    ledger.record(RECORD)
    ledger.record(replace(RECORD, attempt=2, outcome=Outcome.INVALID))

    assert [call.attempt for call in ledger.calls] == [1, 2]
    assert ledger.total_cost() == Decimal("0.028")


def test_the_in_memory_ledger_fits_the_cost_ledger_interface() -> None:
    ledger: CostLedger = InMemoryLedger()  # mypy checks this line

    ledger.record(RECORD)
