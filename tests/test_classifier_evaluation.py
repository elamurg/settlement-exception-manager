"""Tests for scoring the classifier agains the golden set, with a scripted model."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from settlement.agents.errors import AgentTimeout
from settlement.agents.fake import FakeModelClient, reply
from settlement.agents.ledger import InMemoryLedger
from settlement.agents.pricing import parse_price_list
from settlement.agents.prompts import Prompt
from settlement.agents.runner import AgentContext
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import (
    Break,
    BreakType,
    FieldDifference,
    Party,
    SettlementMethod,
    Side,
    Trade,
)
from settlement.evaluation.classification import ClassifierSetup, score_classifier
from settlement.evaluation.metrics import Ratio
from settlement.evaluation.outcomes import Outcomes, frozen

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
PRICES = parse_price_list(
    """
version: "test"
currency: "USD"
models:
  claude-opus-5-5: {input: "4.00", output: "20.00", cache_read: "0.20", cache_write: "5.00"}
"""
)
PROMPT = Prompt(name="classifier", version="v1", text="Classify the break.")

TRADE = Trade(
    trade_id="T-1",
    isin=ISIN("US0378331005"),
    side=Side.BUY,
    quantity=Decimal("1000"),
    price=Decimal("150.25"),
    consideration=Decimal("150250.00"),
    currency="USD",
    counterparty=Party("P1", "Barclays", LEI("5493001KJTIIGC8Y1R12")),
    trade_date=date(2026, 11, 16),
    intended_settlement_date=date(2026, 11, 17),
    settlement_method=SettlementMethod.DVP,
)
BREAK = Break(
    break_id="B-QUANTITY_BREAK-T-1",
    break_type=BreakType.QUANTITY_BREAK,
    ours=TRADE,
    theirs=None,  # the classifier only needs the evidence for these tests
    evidence=(FieldDifference("quantity", ours="1000", theirs="900"),),
    intended_settlement_date=date(2026, 11, 17),
    raised_at=NOW,
)

SPLIT = replace(BREAK, break_id="B-QUANTITY_BREAK-split")
PRICE = replace(
    BREAK,
    break_id="B-PRICE_BREAK-price",
    break_type=BreakType.PRICE_BREAK,
    evidence=(FieldDifference("consideration", ours="150250.00", theirs="150300.00"),),
)
FOUND: list[tuple[str, Break]] = [("split/T-1", SPLIT), ("price/T-1", PRICE)]
EXPECTED = Outcomes(
    clean=frozenset(),
    broken=frozen(
        {"split/T-1": {BreakType.CORPORATE_ACTION}, "price/T-1": {BreakType.PRICE_BREAK}}
    ),
    unresolved=frozenset(),
)


def says(cause: str, field: str) -> str:
    return json.dumps(
        {
            "root_cause": cause,
            "evidence_fields": [field],
            "confidence": "0.8",
            "explanation": "test",
        }
    )


def setup(client: FakeModelClient, limit: int | None = None) -> ClassifierSetup:
    context = AgentContext(client=client, ledger=InMemoryLedger(), prices=PRICES, clock=lambda: NOW)
    return ClassifierSetup(prompt=PROMPT, context=context, limit=limit)


def test_engine_and_classifier_are_scored_on_the_same_records() -> None:
    client = FakeModelClient(
        reply(says("CORPORATE_ACTION", "quantity")), reply(says("PRICE_BREAK", "consideration"))
    )

    scores = score_classifier(FOUND, EXPECTED, setup(client))

    assert scores.engine[BreakType.CORPORATE_ACTION].recall == Ratio(0, 1)
    assert scores.classifier[BreakType.CORPORATE_ACTION].recall == Ratio(1, 1)
    assert scores.engine[BreakType.QUANTITY_BREAK].precision == Ratio(0, 1)
    assert BreakType.QUANTITY_BREAK not in scores.classifier
    assert scores.classifier[BreakType.PRICE_BREAK].precision == Ratio(1, 1)


def test_every_call_is_costed_including_failures() -> None:
    client = FakeModelClient(
        reply(says("CORPORATE_ACTION", "quantity")), AgentTimeout("classifier", "took too long")
    )

    scores = score_classifier(FOUND, EXPECTED, setup(client))

    assert scores.breaks == 2
    assert scores.cost == Decimal("0.014")
    assert scores.cost_per_break == Decimal("0.007")
    assert scores.failed == (("B-PRICE_BREAK-price", "classifier: took too long"),)
    assert scores.classifier[BreakType.PRICE_BREAK].recall == Ratio(0, 1)


def test_a_limit_classifies_only_the_first_records() -> None:
    client = FakeModelClient(reply(says("CORPORATE_ACTION", "quantity")))

    scores = score_classifier(FOUND, EXPECTED, setup(client, limit=1))

    assert (scores.records, scores.breaks) == (1, 1)
    assert len(client.requests) == 1


def test_a_trade_with_two_breaks_needs_both_types() -> None:
    """REALLY_TWO: one record, two breaks, each clessified; the record needs both labels."""
    found = [("two/T-1", SPLIT), ("two/T-1", PRICE)]
    expected = Outcomes(
        clean=frozenset(),
        broken=frozen({"two/T-1": {BreakType.QUANTITY_BREAK, BreakType.PRICE_BREAK}}),
        unresolved=frozenset(),
    )
    client = FakeModelClient(
        reply(says("QUANTITY_BREAK", "quantity")), reply(says("PRICE_BREAK", "consideration"))
    )

    scores = score_classifier(found, expected, setup(client))

    assert scores.records == 1
    assert scores.classifier[BreakType.QUANTITY_BREAK].recall == Ratio(1, 1)
    assert scores.classifier[BreakType.PRICE_BREAK].recall == Ratio(1, 1)


def test_a_limit_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="limit"):
        setup(FakeModelClient(), limit=0)
