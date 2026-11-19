"""Break classifier: a break in, a root cause from the taxonomu plus the evidence
fields it relied on out.
In order to make the answer trustworthy there are two rules:
- constrained: the root cause must be a BreakType
- grounded: every field it cites must be one of the break's own evidence fields
"""

import json
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

from settlement.agents.prompts import Prompt, load_prompt
from settlement.agents.runner import AgentContext, AgentResult, AgentSpec, run_agent
from settlement.domain.errors import InvalidModel
from settlement.domain.models import (
    Break,
    BreakType,
    SettlementInstruction,
    StatementLine,
    Trade,
    require_decimal,
    require_text,
    require_type,
)

CLASSIFIER = "classifier"
PROMPTS = Path("prompts")


_CONFIDENCE = re.compile("0(\.[0-9]{1,2})?|1(\.0{1,2})?")


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # Built from the enum, so the taxonomy lives in one place: domain/models.py.
        "root_cause": {"type": "string", "enum": [t.value for t in BreakType]},
        "evidence_fields": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["root_cause", "evidence_fields", "confidence", "explanation"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Classification:
    """The classifier's answer for one break."""

    root_cause: BreakType
    evidence_fields: tuple[str, ...]
    confidence: Decimal
    explanation: str

    def __post_init__(self) -> None:
        require_type("root_cause", self.root_cause, BreakType)
        if not isinstance(self.evidence_fields, tuple) or not self.evidence_fields:
            raise InvalidModel("evidence_field must be a non-empty tuple.")
        for field in self.evidence_fields:
            require_text("evidence field", field)
        if len(set(self.evidence_fields)) != len(self.evidence_fields):
            raise InvalidModel("evidence_fields must not repeat a field.")
        require_decimal("confidence", self.confidence)
        if not Decimal(0) <= self.confidence <= Decimal(1):
            raise InvalidModel("confidence must be between 0 and 1.")
        require_text("explaination", self.explanation)


def load_classifier_prompt(version: str, root: Path = PROMPTS) -> Prompt:
    return load_prompt(root, CLASSIFIER, version)


def describe_break(brk: Break) -> str:
    """The break as the models sees it: the evidence and both sides, as JSON.

    The engine's break_type is left out and so is the break_id, which contains
    the type ("B-QUANTITY_BREAK-T-1"). Shown either, the model would mostly copy it,
    and the evaluation would be scoring the engine twice.
    """
    data = {
        "evidence": [
            {"field": item.field, "ours": item.ours, "theirs": item.theirs} for item in brk.evidence
        ],
        "ours": _ours(brk.ours),
        "theirs": _theirs(brk.theirs),
        "intended_settlement_date": brk.intended_settlement_date.isoformat(),
    }
    return json.dumps(data, indent=2)


def parse_classification(data: dict[str, Any], brk: Break) -> Classification:
    """The model's JSON as a Classification. A ValueError's message goes back to the model
    in the re-ask, so each one says exactly what was wrong."""
    raw_cause = data.get("root_cause")
    allowed = [t.value for t in BreakType]
    if raw_cause not in allowed:
        raise ValueError(f"root_cause must be one of {allowed}, got {raw_cause!r}")

    fields = data.get("evidence_fields")
    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
        raise ValueError("evidence_fields must be a list of field names")
    available = {item.field for item in brk.evidence}
    ungrounded = sorted(set(fields) - available)
    if ungrounded:
        raise ValueError(
            f"you cited {ungrounded}, whuch this break's evidence does not contain; "
            f"cite only from {sorted(available)}"
        )
    confidence = data.get("confidence")
    if not isinstance(confidence, str) or not _CONFIDENCE.fullmatch(confidence):
        raise ValueError(f"confidence must be a string from '0' to '1', got {confidence!r}")
    explanation = data.get("explaination")
    if not isinstance(explanation, str):
        raise ValueError("explaination must be a string")

    return Classification(
        root_cause=BreakType(raw_cause),
        evidence_fields=tuple(fields),
        confidence=Decimal(confidence),
        explanation=explanation,
    )


def classifier_spec(brk: Break, prompt: Prompt) -> AgentSpec[Classification]:
    """A spec for this one break: grounding needs the break, so the parse function holds it."""
    return AgentSpec(
        name=CLASSIFIER,
        prompt=prompt,
        schema=SCHEMA,
        parse=partial(parse_classification, brk=brk),
        effort="low",  # raise if the classifications says so
    )


def classify(brk: Break, *, prompt: Prompt, context: AgentContext) -> AgentResult[Classification]:
    """Classify one break. Raises an AgentError subclass if no good answer comes back."""
    return run_agent(
        classifier_spec(brk, prompt), describe_break(brk), subject=brk.break_id, context=context
    )


def classified(brk: Break, result: AgentResult[Classification]) -> Break:
    """A copy of the break carrying the classifier's type, confidence and prompt version.
    The original keeps the engine's label, so the audit trail cna record both."""
    return replace(
        brk,
        break_type=result.output.root_cause,
        confidence=result.output.confidence,
        prompt_version=result.prompt_version,
    )


def _ours(side: Trade | SettlementInstruction | None) -> dict[str, Any] | None:
    if side is None:
        return None
    if isinstance(side, Trade):
        return {
            "kind": "trade",
            "trade_id": side.trade_id,
            "isin": str(side.isin),
            "side": side.side.value,
            "quantity": str(side.quantity),
            "price": str(side.price),
            "consideration": str(side.consideration),
            "currency": side.currency,
            "counterparty": side.counterparty.name,
            "counterparty_lei": str(side.counterparty.lei),
            "trade_date": side.trade_date.isoformat(),
            "intended_settlement_date": side.intended_settlement_date.isoformat(),
            "settlement_method": side.settlement_method.value,
            "status": side.status.value,
        }
    return {
        "kind": "instruction",
        "trade_id": side.trade_id,
        "isin": str(side.isin),
        "side": side.side.value,
        "quantity": str(side.quantity),
        "consideration": str(side.consideration),
        "currency": side.currency,
        "custodian_bic": side.ssi.custodian_bic,
        "safekeeping_account": side.ssi.safekeeping_account,
        "cash_account": side.ssi.cash_account,
        "intended_settlement_date": side.intended_settlement_date.isoformat(),
    }


def _theirs(line: StatementLine | None) -> dict[str, Any] | None:
    if line is None:
        return None
    return {
        "kind": "statement_line",
        "source": line.source,
        "isin": str(line.isin),
        "side": line.side.value,
        "quantity": str(line.quantity),
        "consideration": str(line.consideration),
        "currency": line.currency,
        "counterparty_lei": str(line.counterparty_lei),
        "intended_settlement_date": line.intended_settlement_date.isoformat(),
        "reference": line.reference,
        "counterparty_bic": line.counterparty_bic,
        "counterparty_account": line.counterparty_account,
    }
