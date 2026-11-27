"""Tests for the break classifier. The model is always FakeModelClient."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from settlement.agents.classifier import (
    SCHEMA,
    Classification,
    classified,
    classify,
    describe_break,
    load_classifier_prompt,
    parse_classification,
)
from settlement.agents.errors import InvalidAgentOutput
from settlement.agents.fake import FakeModelClient, reply
from settlement.agents.ledger import InMemoryLedger
from settlement.agents.pricing import parse_price_list
from settlement.agents.prompts import Prompt
from settlement.agents.records import Outcome
from settlement.agents.runner import AgentContext
from settlement.domain.errors import InvalidModel
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import (
    Break,
    BreakType,
    FieldDifference,
    Party,
    SettlementMethod,
    Side,
    StatementLine,
    Trade,
)

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
PRICES = parse_price_list(
    """
version: "test"
currency: "USD"
models:
  claude-opus-5-5: {input: "4.00", output: "20.00", cache_read: "0.20", cache_write: "5.00"}
"""
)
PROMPT = Prompt(name="classifier", version="v1", text="Classify the break.")
LEI_OURS = LEI("5493001KJTIIGC8Y1R12")

TRADE = Trade(
    trade_id="T-1",
    isin=ISIN("US0378331005"),
    side=Side.BUY,
    quantity=Decimal("1000"),
    price=Decimal("150.25"),
    consideration=Decimal("15025.00"),
    currency="USD",
    counterparty=Party("P1", "Barclays", LEI_OURS),
    trade_date=date(2026, 11, 16),
    intended_settlement_date=date(2026, 11, 17),
    settlement_method=SettlementMethod.DVP,
)
LINE = StatementLine(
    line_id="L-1",
    source="CUSTODIAN",
    isin=ISIN("US0378331005"),
    side=Side.BUY,
    quantity=Decimal("900"),
    consideration=Decimal("150250.00"),
    currency="USD",
    counterparty_lei=LEI_OURS,
    intended_settlement_date=date(2026, 11, 17),
    reference="T-1",
)
BREAK = Break(
    break_id="B-QUANTITY_BREAK-T-1",
    break_type=BreakType.QUANTITY_BREAK,
    ours=TRADE,
    theirs=LINE,
    evidence=(FieldDifference("quantity", ours="1000", theirs="900"),),
    intended_settlement_date=date(2026, 11, 17),
    raised_at=NOW,
)


def answer(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "root_cause": "QUANTITY_BREAK",
        "evidence_fields": ["quantity"],
        "confidence": "0.9",
        "explanation": "We booked 1000 shares; the custodian shows 900.",
    }
    return data | overrides


def context(client: FakeModelClient, ledger: InMemoryLedger | None = None) -> AgentContext:
    return AgentContext(
        client=client,
        ledger=ledger or InMemoryLedger(),
        prices=PRICES,
        clock=lambda: NOW,
    )


def test_schema_allows_exactly_the_taxonomy() -> None:
    """Add a BreakType and the schema follows; nothing else is ever allowed."""
    assert SCHEMA["properties"]["root_cause"]["enum"] == [t.value for t in BreakType]


def test_schema_requires_every_field_and_nothing_extra() -> None:
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"])
    assert SCHEMA["additionalProperties"] is False


def test_the_model_never_sees_the_engines_label() -> None:
    text = describe_break(BREAK)

    assert "QUANTITY_BREAK" not in text
    assert "break_type" not in text
    assert "break_id" not in text


def test_the_model_sees_the_evidence_and_both_sides() -> None:
    data = json.loads(describe_break(BREAK))

    assert data["evidence"] == [{"field": "quantity", "ours": "1000", "theirs": "900"}]
    assert data["ours"]["quantity"] == "1000"
    assert data["theirs"]["quantity"] == "900"


def test_a_missing_side_is_shown_as_null() -> None:
    unmatched = replace(
        BREAK,
        theirs=None,
        evidence=(FieldDifference("statement_line", ours="present", theirs="missing"),),
    )
    assert json.loads(describe_break(unmatched))["theirs"] is None


def test_the_same_break_is_always_described_the_same() -> None:
    assert describe_break(BREAK) == describe_break(BREAK)


def test_a_good_answer_becomes_a_classification() -> None:
    result = parse_classification(answer(), BREAK)

    assert result == Classification(
        root_cause=BreakType.QUANTITY_BREAK,
        evidence_fields=("quantity",),
        confidence=Decimal("0.9"),
        explanation="We booked 1000 shares; the custodian shows 900.",
    )


@pytest.mark.parametrize("cause", ["OTHER", "quantity break", "", None])
def test_a_cause_outside_the_taxonomy_is_rejected(cause: Any) -> None:
    with pytest.raises(ValueError, match="root_cause must be one of"):
        parse_classification(answer(root_cause=cause), BREAK)


def test_citing_a_field_the_break_does_not_have_is_rejected() -> None:
    """The grounding rule: price is on the trade, but it isn't in this break's evidence."""
    with pytest.raises(ValueError, match="you cited \['price'\].*cite only from \['quantity'\]"):
        parse_classification(answer(evidence_fields=["quantity", "price"]), BREAK)


@pytest.mark.parametrize("confidence", [0.9, "1.5", "high", "-0.1", "0.123"])
def test_confidence_must_be_a_decimal_string_from_0_to_1(confidence: Any) -> None:
    with pytest.raises(ValueError, match="confidence"):
        parse_classification(answer(confidence=confidence), BREAK)


@pytest.mark.parametrize(
    "overrides",
    [{"evidence_fields": []}, {"evidence_fields": ["quantity", "quantity"]}, {"explanation": " "}],
)
def test_classification_checks_itself(overrides: dict[str, Any]) -> None:
    with pytest.raises(InvalidModel):
        parse_classification(answer(**overrides), BREAK)


def test_classify_sends_the_prompt_the_schema_and_the_break() -> None:
    client = FakeModelClient(reply(json.dumps(answer())))
    result = classify(BREAK, prompt=PROMPT, context=context(client))

    (request,) = client.requests
    assert request.agent == "classifier"
    assert request.system == "Classify the break."
    assert request.schema == SCHEMA
    assert request.effort == "low"
    assert request.messages[0].text == describe_break(BREAK)
    assert result.output.root_cause == BreakType.QUANTITY_BREAK
    assert result.prompt_version == "v1"


def test_an_ungrounded_answer_gets_one_reask_naming_the_field() -> None:
    client = FakeModelClient(
        reply(json.dumps(answer(evidence_fields=["price"]))),
        reply(json.dumps(answer())),
    )
    result = classify(BREAK, prompt=PROMPT, context=context(client))

    assert [r.outcome for r in result.records] == [Outcome.INVALID, Outcome.OK]
    reask = client.requests[1].messages[-1].text
    assert "you cited ['price']" in reask


def test_done_when_a_classification_citing_a_missing_field_fails() -> None:
    """Ungrounded twice: a clear domain error, and both paid calls are still on the ledger."""
    ledger = InMemoryLedger()
    ungrounded = json.dumps(answer(evidence_fields=["counterparty_bic"]))
    client = FakeModelClient(reply(ungrounded), reply(ungrounded))

    with pytest.raises(InvalidAgentOutput, match="classifier: answer still invalid"):
        classify(BREAK, prompt=PROMPT, context=context(client, ledger))

    assert len(client.requests) == 2
    assert [r.subject for r in ledger.calls] == ["B-QUANTITY_BREAK-T-1"] * 2
    assert ledger.total_cost() == Decimal("0.028")


def test_classified_copies_the_answer_onto_the_break() -> None:
    client = FakeModelClient(reply(json.dumps(answer(root_cause="CORPORATE_ACTION"))))
    result = classify(BREAK, prompt=PROMPT, context=context(client))

    relabelled = classified(BREAK, result)

    assert relabelled.break_type == BreakType.CORPORATE_ACTION
    assert relabelled.confidence == Decimal("0.9")
    assert relabelled.prompt_version == "v1"
    assert BREAK.break_type == BreakType.QUANTITY_BREAK


def test_prompt_v1_loads_and_names_every_break_type() -> None:
    prompt = load_classifier_prompt("v1")

    missing = [t.value for t in BreakType if t.value not in prompt.text]
    assert missing == []
