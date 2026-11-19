"""Where call records go. In memory for now: a Postgres ledger can replace it later
without changing a single agent."""

from decimal import Decimal
from typing import Protocol

from settlement.agents.records import CallRecord


class CostLedger(Protocol):
    def record(self, call: CallRecord) -> None: ...


class InMemoryLedger:
    def __init__(self) -> None:
        self._calls: list[CallRecord] = []

    def record(self, call: CallRecord) -> None:
        self._calls.append(call)

    @property
    def calls(self) -> tuple[CallRecord, ...]:
        return tuple(self._calls)

    def total_cost(self) -> Decimal:
        return sum((call.cost for call in self._calls), Decimal(0))
