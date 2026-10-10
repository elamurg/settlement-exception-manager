"""Tests for the domain models (SE-04, issue #4).

Each test starts from a valid object and changes one field with `dataclasses.replace`,
which runs validation again. Only the standard library and settlement.domain are imported.
"""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from settlement.domain.errors import DomainError, InvalidModel
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import (
    SSI,
    Allocation,
    AuditEvent,
    Break,
    BreakType,
    FieldDifference,
    Party,
    Resolution,
    ResolutionAction,
    ResolutionStatus,
    SettlementInstruction,
    SettlementMethod,
    Side,
    StatementLine,
    Trade,
    TradeStatus,
)

TRADE_DATE = date(2026, 10, 5)  # Monday
SETTLEMENT_DATE = date(2026, 10, 6)  # T+1
RAISED_AT = datetime(2026, 10, 6, 9, 30, tzinfo=UTC)
NAIVE_TIME = datetime(2026, 10, 6, 9, 30)


# Valid objects to start each test from


def make_party() -> Party:
    return Party(party_id="CP-001", name="Example Bank plc", lei=LEI("HWUPKR0MPOU8FGXBT394"))


def make_ssi() -> SSI:
    return SSI(
        party_id="CP-001",
        currency="EUR",
        custodian_bic="DEUTDEFF",
        safekeeping_account="SAFE-123",
        cash_account="CASH-456",
        valid_from=date(2026, 1, 1),
    )


def make_trade() -> Trade:
    return Trade(
        trade_id="T-001",
        isin=ISIN("DE0007164600"),
        side=Side.BUY,
        quantity=Decimal("1000"),
        price=Decimal("150.25"),
        consideration=Decimal("150250.00"),
        currency="EUR",
        counterparty=make_party(),
        trade_date=TRADE_DATE,
        intended_settlement_date=SETTLEMENT_DATE,
        settlement_method=SettlementMethod.DVP,
    )


def make_instruction() -> SettlementInstruction:
    return SettlementInstruction(
        instruction_id="I-001",
        trade_id="T-001",
        isin=ISIN("DE0007164600"),
        side=Side.BUY,
        quantity=Decimal("1000"),
        consideration=Decimal("150250.00"),
        currency="EUR",
        ssi=make_ssi(),
        intended_settlement_date=SETTLEMENT_DATE,
    )


def make_statement_line() -> StatementLine:
    return StatementLine(
        line_id="L-001",
        source="custodian",
        isin=ISIN("DE0007164600"),
        side=Side.BUY,
        quantity=Decimal("900"),
        consideration=Decimal("135225.00"),
        currency="EUR",
        counterparty_lei=LEI("HWUPKR0MPOU8FGXBT394"),
        intended_settlement_date=SETTLEMENT_DATE,
        reference="T-001",
    )


def make_difference() -> FieldDifference:
    return FieldDifference(field="quantity", ours="1000", theirs="900")


def make_break() -> Break:
    return Break(
        break_id="B-001",
        break_type=BreakType.QUANTITY_BREAK,
        ours=make_trade(),
        theirs=make_statement_line(),
        evidence=(make_difference(),),
        intended_settlement_date=SETTLEMENT_DATE,
        raised_at=RAISED_AT,
    )


def make_resolution() -> Resolution:
    return Resolution(
        resolution_id="R-001",
        break_id="B-001",
        action=ResolutionAction.CHASE_COUNTERPARTY,
        rationale="Custodian shows 900; confirm the quantity with the counterparty.",
        proposed_by="user-1",
    )


def make_audit_event() -> AuditEvent:
    return AuditEvent(
        event_id="E-001",
        entity_type="Break",
        entity_id="B-001",
        event_type="BREAK_RAISED",
        actor="system",
        occurred_at=RAISED_AT,
    )


# Every model can be built with valid data


def test_every_model_can_be_built() -> None:
    make_party()
    make_ssi()
    make_trade()
    make_instruction()
    make_statement_line()
    make_difference()
    make_break()
    make_resolution()
    make_audit_event()


def test_defaults() -> None:
    assert make_trade().status == TradeStatus.BOOKED
    assert make_resolution().status == ResolutionStatus.PROPOSED
    assert make_resolution().approved_by is None
    assert make_break().confidence is None


# Empty text fields


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize(
    ("make", "field"),
    [
        (make_party, "party_id"),
        (make_party, "name"),
        (make_ssi, "safekeeping_account"),
        (make_trade, "trade_id"),
        (make_instruction, "instruction_id"),
        (make_statement_line, "reference"),
        (make_difference, "field"),
        (make_break, "break_id"),
        (make_resolution, "rationale"),
        (make_audit_event, "actor"),
    ],
)
def test_empty_text_is_rejected(make: Callable[[], Any], field: str, blank: str) -> None:
    valid = make()

    with pytest.raises(InvalidModel, match=field):
        replace(valid, **{field: blank})


# Decimal only: quantities and money


@pytest.mark.parametrize(
    "quantity",
    [
        1000.0,  # float
        1000,  # int
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_trade_quantity_must_be_a_positive_finite_decimal(quantity: object) -> None:
    with pytest.raises(InvalidModel, match="quantity"):
        replace(make_trade(), quantity=quantity)  # type: ignore[arg-type]


def test_trade_price_can_be_zero_but_not_negative() -> None:
    replace(make_trade(), price=Decimal("0"))

    with pytest.raises(InvalidModel, match="price"):
        replace(make_trade(), price=Decimal("-0.01"))


@pytest.mark.parametrize(
    "make", [make_instruction, make_statement_line], ids=["instruction", "statement_line"]
)
@pytest.mark.parametrize("quantity", [Decimal("0"), 900.0])
def test_other_quantities_must_be_positive_decimals(
    make: Callable[[], Any], quantity: object
) -> None:
    with pytest.raises(InvalidModel, match="quantity"):
        replace(make(), quantity=quantity)


def test_allocation_quantity_must_be_positive() -> None:
    Allocation(allocation_id="A-1", trade_id="T-001", account="FUND-1", quantity=Decimal("500"))

    with pytest.raises(InvalidModel, match="quantity"):
        Allocation(allocation_id="A-1", trade_id="T-001", account="FUND-1", quantity=Decimal("0"))


# Trade rules


def test_dvp_trade_needs_consideration() -> None:
    with pytest.raises(InvalidModel, match="consideration"):
        replace(make_trade(), consideration=Decimal("0"))


def test_fop_trade_allows_zero_consideration() -> None:
    trade = replace(
        make_trade(), settlement_method=SettlementMethod.FOP, consideration=Decimal("0")
    )

    assert trade.consideration == Decimal("0")


def test_fop_trade_rejects_negative_consideration() -> None:
    with pytest.raises(InvalidModel, match="consideration"):
        replace(make_trade(), settlement_method=SettlementMethod.FOP, consideration=Decimal("-1"))


def test_settlement_date_before_trade_date_is_rejected() -> None:
    with pytest.raises(InvalidModel, match="before trade date"):
        replace(make_trade(), intended_settlement_date=date(2026, 10, 2))


def test_same_day_settlement_is_allowed() -> None:
    replace(make_trade(), intended_settlement_date=TRADE_DATE)


@pytest.mark.parametrize("currency", ["eur", "EU", "EURO", "", "E1R"])
def test_currency_must_be_three_uppercase_letters(currency: str) -> None:
    with pytest.raises(InvalidModel, match="currency"):
        replace(make_trade(), currency=currency)


# SSI and settlement instructions


@pytest.mark.parametrize("bic", ["DEUTDEFF", "DEUTDEFF500"])
def test_ssi_accepts_8_and_11_character_bics(bic: str) -> None:
    replace(make_ssi(), custodian_bic=bic)


@pytest.mark.parametrize("bic", ["DEUTDEF", "DEUTDEFF5", "DEUTDEFF5000", "deutdeff", "1EUTDEFF"])
def test_ssi_rejects_invalid_bics(bic: str) -> None:
    with pytest.raises(InvalidModel, match="BIC"):
        replace(make_ssi(), custodian_bic=bic)


def test_ssi_currency_must_be_valid() -> None:
    with pytest.raises(InvalidModel, match="currency"):
        replace(make_ssi(), currency="euro")


def test_instruction_ssi_must_be_in_the_instruction_currency() -> None:
    usd_ssi = replace(make_ssi(), currency="USD")

    with pytest.raises(InvalidModel, match="SSI currency"):
        replace(make_instruction(), ssi=usd_ssi)


# FieldDifference


def test_field_difference_values_must_differ() -> None:
    with pytest.raises(InvalidModel, match="equal values"):
        FieldDifference(field="quantity", ours="1000", theirs="1000")


# Break


def test_break_with_only_our_side_is_allowed() -> None:
    replace(make_break(), theirs=None)


def test_break_with_only_their_side_is_allowed() -> None:
    replace(make_break(), ours=None)


def test_break_can_compare_an_instruction() -> None:
    replace(make_break(), ours=make_instruction())


def test_break_needs_at_least_one_side() -> None:
    with pytest.raises(InvalidModel, match="at least one side"):
        replace(make_break(), ours=None, theirs=None)


def test_break_without_evidence_is_rejected() -> None:
    with pytest.raises(InvalidModel, match="at least one piece of evidence"):
        replace(make_break(), evidence=())


def test_break_evidence_must_be_a_tuple() -> None:
    with pytest.raises(InvalidModel, match="tuple"):
        replace(make_break(), evidence=[make_difference()])  # type: ignore[arg-type]


def test_break_evidence_must_be_field_differences() -> None:
    with pytest.raises(InvalidModel, match="FieldDifference"):
        replace(make_break(), evidence=("quantity differs",))  # type: ignore[arg-type]


def test_break_type_must_be_a_break_type() -> None:
    with pytest.raises(InvalidModel, match="BreakType"):
        replace(make_break(), break_type="QUANTITY_BREAK")  # type: ignore[arg-type]


@pytest.mark.parametrize("confidence", [Decimal("0"), Decimal("0.85"), Decimal("1")])
def test_break_confidence_between_0_and_1_is_allowed(confidence: Decimal) -> None:
    replace(make_break(), confidence=confidence, prompt_version="classifier-v1")


@pytest.mark.parametrize("confidence", [Decimal("-0.01"), Decimal("1.01"), Decimal("NaN"), 0.85])
def test_break_confidence_outside_0_to_1_is_rejected(confidence: object) -> None:
    with pytest.raises(InvalidModel, match="confidence"):
        replace(make_break(), confidence=confidence)  # type: ignore[arg-type]


def test_break_prompt_version_cannot_be_blank() -> None:
    with pytest.raises(InvalidModel, match="prompt_version"):
        replace(make_break(), prompt_version="")


# Timestamps must carry a timezone


def test_break_raised_at_must_be_timezone_aware() -> None:
    with pytest.raises(InvalidModel, match="raised_at"):
        replace(make_break(), raised_at=NAIVE_TIME)


def test_audit_event_occurred_at_must_be_timezone_aware() -> None:
    with pytest.raises(InvalidModel, match="occurred_at"):
        replace(make_audit_event(), occurred_at=NAIVE_TIME)


def test_audit_event_records_before_and_after() -> None:
    event = replace(make_audit_event(), before="BOOKED", after="ALLOCATED")

    assert (event.before, event.after) == ("BOOKED", "ALLOCATED")


# Resolution


def test_resolution_action_must_be_a_resolution_action() -> None:
    with pytest.raises(InvalidModel, match="ResolutionAction"):
        replace(make_resolution(), action="ESCALATE")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["approved_by", "reason_code"])
def test_resolution_optional_text_cannot_be_blank(field: str) -> None:
    with pytest.raises(InvalidModel, match=field):
        replace(make_resolution(), **{field: ""})  # type: ignore[arg-type]


# Immutability


@pytest.mark.parametrize(
    ("make", "field", "value"),
    [
        (make_trade, "status", TradeStatus.SETTLED),
        (make_trade, "quantity", Decimal("1")),
        (make_break, "evidence", ()),
        (make_resolution, "status", ResolutionStatus.APPROVED),
        (make_audit_event, "actor", "someone-else"),
    ],
)
def test_models_cannot_be_changed_after_creation(
    make: Callable[[], Any], field: str, value: object
) -> None:
    model = make()

    with pytest.raises(FrozenInstanceError):
        setattr(model, field, value)


def test_changing_a_trade_creates_a_new_object() -> None:
    trade = make_trade()

    allocated = replace(trade, status=TradeStatus.ALLOCATED)

    assert trade.status == TradeStatus.BOOKED
    assert allocated.status == TradeStatus.ALLOCATED


def test_invalid_model_is_a_domain_error() -> None:
    with pytest.raises(DomainError):
        replace(make_trade(), quantity=Decimal("0"))
