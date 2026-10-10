"""Settlement domain models."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from settlement.domain.errors import InvalidModel
from settlement.domain.identifiers import ISIN, LEI

BIC_PATTERN = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


# helper checks
def require_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidModel(f"{field_name} must not be empty.")


def require_decimal(field_name: str, value: object) -> None:
    if not isinstance(value, Decimal):
        raise InvalidModel(f"{field_name} must be a decimal number.")
    if not value.is_finite():
        raise InvalidModel(f"{field_name} must be a finite number.")


def require_positive(field_name: str, value: Decimal) -> None:
    require_decimal(field_name, value)
    if value <= 0:
        raise InvalidModel(f"{field_name} must be positive.")


def require_not_negative(field_name: str, value: Decimal) -> None:
    require_decimal(field_name, value)
    if value < 0:
        raise InvalidModel(f"{field_name} must not be negative.")


def require_currency(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not CURRENCY_PATTERN.fullmatch(value):
        raise InvalidModel(f"{field_name} must be a 3-letter currency code.")


def require_aware(field_name: str, value: datetime) -> None:
    # A timestamp without a timezone is ambiguous: 09:00 in London or in New York?
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidModel(f"{field_name} must be a timezone-aware datetime.")


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class SettlementMethod(StrEnum):
    DVP = "DVP"
    FOP = "FOP"


class TradeStatus(StrEnum):
    BOOKED = "BOOKED"
    ALLOCATED = "ALLOCATED"
    CONFIRMED = "CONFIRMED"
    AFFIRMED = "AFFIRMED"
    INSTRUCTED = "INSTRUCTED"
    MATCHED = "MATCHED"
    SETTLED = "SETTLED"
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
    FAILED = "FAILED"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class BreakType(StrEnum):
    """Break types for settlement domain objects."""

    SSI_MISMATCH = "SSI_MISMATCH"
    QUANTITY_BREAK = "QUANTITY_BREAK"
    PRICE_BREAK = "PRICE_BREAK"
    DATE_MISMATCH = "DATE_MISMATCH"
    MISSING_CONFIRMATION = "MISSING_CONFIRMATION"
    UNMATCHED_INSTRUCTION = "UNMATCHED_INSTRUCTION"
    DUPLICATE_BOOKING = "DUPLICATE_BOOKING"
    FUNDING_SHORTFALL = "FUNDING_SHORTFALL"
    STATIC_DATA = "STATIC_DATA"


class ResolutionAction(StrEnum):
    """Resolution actions for settlement domain objects."""

    AMEND_SSI = "AMEND_SSI"
    REINSTRUCT = "REINSTRUCT"
    CANCEL_AND_REBOOK = "CANCEL_AND_REBOOK"
    CHASE_COUNTERPARTY = "CHASE_COUNTERPARTY"
    ESCALATE = "ESCALATE"


class ResolutionStatus(StrEnum):
    """Resolution status for settlement domain objects."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AMENDED = "AMENDED"


@dataclass(frozen=True, slots=True)
class Party:
    party_id: str
    name: str
    lei: LEI

    def __post_init__(self) -> None:
        require_text("party_id", self.party_id)
        require_text("name", self.name)


@dataclass(frozen=True, slots=True)
class SSI:
    """Standard Settlement Instructions for a party."""

    party_id: str
    currency: str
    custodian_bic: str
    safekeeping_account: str
    cash_account: str
    valid_from: date

    def __post_init__(self) -> None:
        require_text("party_id", self.party_id)
        require_currency("currency", self.currency)
        if not BIC_PATTERN.fullmatch(self.custodian_bic):
            raise InvalidModel(f"SSI custodian BIC must be valid: {self.custodian_bic!r}")
        require_text("safekeeping_account", self.safekeeping_account)
        require_text("cash_account", self.cash_account)


@dataclass(frozen=True, slots=True)
class Trade:
    """Trade booked on our side."""

    trade_id: str
    isin: ISIN
    side: Side
    quantity: Decimal
    price: Decimal
    consideration: Decimal
    currency: str
    counterparty: Party
    trade_date: date
    intended_settlement_date: date
    settlement_method: SettlementMethod
    status: TradeStatus = TradeStatus.BOOKED  # default status is booked

    def __post_init__(self) -> None:
        require_text("trade_id", self.trade_id)
        require_positive("quantity", self.quantity)
        require_not_negative("price", self.price)
        require_currency("currency", self.currency)

        if self.settlement_method == SettlementMethod.DVP:
            require_positive("consideration", self.consideration)
        else:
            require_not_negative("consideration", self.consideration)

        if self.intended_settlement_date < self.trade_date:
            raise InvalidModel("Intended settlement date cannot be before trade date.")


# all allocations should add up to the trade quantity is not metioned in the following class
@dataclass(frozen=True, slots=True)
class Allocation:
    """One slice of the block trade, given to the client account."""

    allocation_id: str
    trade_id: str
    account: str
    quantity: Decimal

    def __post_init__(self) -> None:
        require_text("allocation_id", self.allocation_id)
        require_text("trade_id", self.trade_id)
        require_text("account", self.account)
        require_positive("quantity", self.quantity)


@dataclass(frozen=True, slots=True)
class SettlementInstruction:
    """The message to the custodian: Settle this trade, using these account details."""

    instruction_id: str
    trade_id: str
    isin: ISIN
    side: Side
    quantity: Decimal
    consideration: Decimal
    currency: str
    ssi: SSI
    intended_settlement_date: date

    def __post_init__(self) -> None:
        require_text("instruction_id", self.instruction_id)
        require_text("trade_id", self.trade_id)
        require_positive("quantity", self.quantity)
        require_not_negative("consideration", self.consideration)
        require_currency("currency", self.currency)

        if self.ssi.currency != self.currency:
            raise InvalidModel("SSI currency does not match the instruction currency.")


@dataclass(frozen=True, slots=True)
class StatementLine:
    line_id: str
    source: str
    isin: ISIN
    side: Side
    quantity: Decimal
    consideration: Decimal
    currency: str
    counterparty_lei: LEI
    intended_settlement_date: date
    reference: str
    # The counterparty's settlement details as the custodian used them. Compared with
    # the SSI on file to find SSI mismatches. Optional: not every statement carries them.
    counterparty_bic: str | None = None
    counterparty_account: str | None = None

    def __post_init__(self) -> None:
        require_text("line_id", self.line_id)
        require_text("source", self.source)
        require_positive("quantity", self.quantity)
        require_not_negative("consideration", self.consideration)
        require_currency("currency", self.currency)
        require_text("reference", self.reference)
        if self.counterparty_bic is not None and (
            not isinstance(self.counterparty_bic, str)
            or not BIC_PATTERN.fullmatch(self.counterparty_bic)
        ):
            raise InvalidModel(f"counterparty BIC must be valid: {self.counterparty_bic!r}")
        if self.counterparty_account is not None:
            require_text("counterparty_account", self.counterparty_account)


@dataclass(frozen=True, slots=True)
class FieldDifference:
    """One field where our record and their record disagree."""

    field: str
    ours: str
    theirs: str

    def __post_init__(self) -> None:
        require_text("field", self.field)
        if self.ours == self.theirs:
            raise InvalidModel(f"FieldDifference for {self.field!r} has equal values.")


@dataclass(frozen=True, slots=True)
class Break:
    """A mismatch between our side and their side, with the evidence that proves it."""

    break_id: str
    break_type: BreakType
    ours: Trade | SettlementInstruction | None
    theirs: StatementLine | None
    evidence: tuple[FieldDifference, ...]
    intended_settlement_date: date
    raised_at: datetime
    confidence: Decimal | None = None  # filled in later by the classifier
    prompt_version: str | None = None  # which prompt produced the classification

    def __post_init__(self) -> None:
        require_text("break_id", self.break_id)
        if not isinstance(self.break_type, BreakType):
            raise InvalidModel("break_type must be a BreakType.")
        if self.ours is None and self.theirs is None:
            raise InvalidModel("Break needs at least one side (ours or theirs).")
        if not isinstance(self.evidence, tuple):
            raise InvalidModel("evidence must be a tuple, not a list.")
        if not self.evidence:
            raise InvalidModel("Break must carry at least one piece of evidence.")
        if not all(isinstance(item, FieldDifference) for item in self.evidence):
            raise InvalidModel("evidence must contain only FieldDifference items.")
        require_aware("raised_at", self.raised_at)
        if self.confidence is not None:
            require_decimal("confidence", self.confidence)
            if not Decimal("0") <= self.confidence <= Decimal("1"):
                raise InvalidModel("confidence must be between 0 and 1.")
        if self.prompt_version is not None:
            require_text("prompt_version", self.prompt_version)


@dataclass(frozen=True, slots=True)
class Resolution:
    """A proposed fix for one break, waiting for (or holding) a human decision."""

    resolution_id: str
    break_id: str
    action: ResolutionAction
    rationale: str
    proposed_by: str
    status: ResolutionStatus = ResolutionStatus.PROPOSED
    approved_by: str | None = None
    reason_code: str | None = None
    # Maker-checker rules (approver != proposer, reason code required) belong to SE-14.

    def __post_init__(self) -> None:
        require_text("resolution_id", self.resolution_id)
        require_text("break_id", self.break_id)
        if not isinstance(self.action, ResolutionAction):
            raise InvalidModel("action must be a ResolutionAction.")
        require_text("rationale", self.rationale)
        require_text("proposed_by", self.proposed_by)
        if self.approved_by is not None:
            require_text("approved_by", self.approved_by)
        if self.reason_code is not None:
            require_text("reason_code", self.reason_code)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable entry in the audit log: who did what, to which record, and when."""

    event_id: str
    entity_type: str  # "Trade", "Break"
    entity_id: str
    event_type: str  # "STATUS_CHANGED", "BREAK_RAISED"
    actor: str  # a user id, or "system"
    occurred_at: datetime
    before: str | None = None
    after: str | None = None

    def __post_init__(self) -> None:
        require_text("event_id", self.event_id)
        require_text("entity_type", self.entity_type)
        require_text("entity_id", self.entity_id)
        require_text("event_type", self.event_type)
        require_text("actor", self.actor)
        require_aware("occurred_at", self.occurred_at)
