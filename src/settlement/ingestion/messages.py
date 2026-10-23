"""Parse simplified ISO 15022 (SWIFT) settlement messages into a common format for
ingestion into the settlement system: MT541/543 instructions and MT548 status messages."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from settlement.domain.errors import InvalidIdentifier
from settlement.domain.identifiers import ISIN
from settlement.domain.models import Side
from settlement.ingestion.errors import ParseError

# SWIFT numbers: digits, a comma (always present), then optional digits. E.g. "1000,", "150250,00"
_SWIFT_DECIMAL = re.compile(r"[0-9]+,[0-9]*")


def parse_swift_decimal(value: str) -> Decimal:
    """Parse a SWIFT decimal value into a Decimal object."""
    if not _SWIFT_DECIMAL.fullmatch(value):
        raise ValueError(f"Invalid SWIFT decimal value: {value!r}")
    return Decimal(value.replace(",", "."))


# SWIFT dates: exactly 8 digits, YYYYMMDD. E.g. "20261005"
_SWIFT_DATE = re.compile(r"[0-9]{8}")


def parse_swift_date(text: str) -> date:
    """Parse a SWIFT date value into a date object."""
    if not _SWIFT_DATE.fullmatch(text):
        raise ValueError(f"Invalid SWIFT date value: {text!r}")
    try:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        raise ValueError(f"not a real date: {text!r}") from None


@dataclass(frozen=True, slots=True)
class Field:
    """One line of a SWIFT message, split into its parts."""

    line: int  # line number in the message, so errors can point to it
    tag: str  # e.g. "98A"
    qualifier: str | None  # e.g. "TRAD", or None for fields like ":23G:NEWM"
    value: str  # e.g. "20261005"


# A field line: colon, tag (2 digits + a letter), colon, then the rest. E.g. ":98A::TRAD//20261005"
_FIELD = re.compile(r":(?P<tag>[0-9]{2}[A-Z]):(?P<rest>.*)")

# The rest, with a qualifier: colon, 4-character qualifier, "//", value. E.g. ":TRAD//20261005"
_QUALIFIED = re.compile(r":(?P<qualifier>[A-Z0-9]{4})//(?P<value>.*)")


def split_fields(text: str, source: str = "message") -> list[Field]:
    """Split a message into fields, one per line. Blank lines are skipped."""
    fields: list[Field] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = _FIELD.fullmatch(line)
        if match is None:
            raise ParseError(source, number, f"not a SWIFT field: {line!r}")
        tag, rest = match["tag"], match["rest"]
        qualified = _QUALIFIED.fullmatch(rest)
        if qualified is not None:
            qualifier, value = qualified["qualifier"], qualified["value"]
        else:
            qualifier, value = None, rest
        if not value:
            raise ParseError(source, number, "has no value", field=tag)
        fields.append(Field(number, tag, qualifier, value))
    return fields


def check_sections(fields: list[Field], source: str = "message") -> None:
    """Every :16R: (open) must be closed by a matching :16S: (close), innermost first."""
    open_sections: list[Field] = []  # the stack of plates
    for field in fields:
        if field.tag == "16R":
            open_sections.append(field)  # put this section on the stack
        elif field.tag == "16S":
            if not open_sections:
                raise ParseError(
                    source, field.line, f"closes {field.value}, which was never opened", "16S"
                )
            opened = open_sections.pop()  # take the top section off the stack
            if opened.value != field.value:
                raise ParseError(
                    source,
                    field.line,
                    f"closes {field.value}, but {opened.value} (opened on line {opened.line}) "
                    "is still open",
                    "16S",
                )
    if open_sections:
        last = open_sections[-1]
        raise ParseError(source, last.line, f"{last.value} is never closed", "16R")


# MT541 / MT543: settlement instructions


@dataclass(frozen=True, slots=True)
class InstructionMessage:
    """A settlement instruction: MT541 (receive against payment, so we buy) or MT543
    (deliver against payment, so we sell)."""

    reference: str  # :20C::SEME//  our reference for this instruction
    function: str  # :23G:  NEWM (new) or CANC (cancel)
    side: Side  # from the message type
    trade_date: date  # :98A::TRAD//
    settlement_date: date  # :98A::SETT//
    isin: ISIN  # :35B:ISIN ...
    quantity: Decimal  # :36B::SETT//UNIT/...
    account: str  # :97A::SAFE//  our safekeeping account
    currency: str  # :19A::SETT//  the first 3 letters
    amount: Decimal  # :19A::SETT//  the rest


FUNCTIONS = {"NEWM", "CANC"}

# A settlement amount: 3-letter currency, then a SWIFT number. E.g. "EUR150250,00"
_AMOUNT = re.compile(r"(?P<currency>[A-Z]{3})(?P<number>[0-9]+,[0-9]*)")


def parse_mt541(text: str, source: str = "MT541") -> InstructionMessage:
    """MT541, receive against payment: we're buying."""
    return _parse_instruction(text, Side.BUY, source)


def parse_mt543(text: str, source: str = "MT543") -> InstructionMessage:
    """MT543, deliver against payment: we're selling."""
    return _parse_instruction(text, Side.SELL, source)


def _parse_instruction(text: str, side: Side, source: str) -> InstructionMessage:
    fields = split_fields(text, source)
    if not fields:
        raise ParseError(source, 1, "message is empty")
    check_sections(fields, source)

    def one(tag: str, qualifier: str | None = None) -> Field:
        return _one(fields, tag, qualifier, source)

    function = one("23G").value
    if function not in FUNCTIONS:
        raise ParseError(
            source, one("23G").line, f"must be NEWM or CANC, got {function!r}", ":23G:"
        )

    trade_date = _convert(one("98A", "TRAD"), parse_swift_date, source)
    settlement_field = one("98A", "SETT")
    settlement_date = _convert(settlement_field, parse_swift_date, source)
    if settlement_date < trade_date:
        raise ParseError(
            source,
            settlement_field.line,
            "settlement date is before the trade date",
            _label(settlement_field),
        )

    currency, amount = _convert(one("19A", "SETT"), _parse_amount, source)

    return InstructionMessage(
        reference=one("20C", "SEME").value,
        function=function,
        side=side,
        trade_date=trade_date,
        settlement_date=settlement_date,
        isin=_convert(one("35B"), _parse_isin, source),
        quantity=_convert(one("36B", "SETT"), _parse_quantity, source),
        account=one("97A", "SAFE").value,
        currency=currency,
        amount=amount,
    )


def _one(fields: list[Field], tag: str, qualifier: str | None, source: str) -> Field:
    """The one field with this tag and qualifier. Missing or repeated is an error."""
    label = _label_for(tag, qualifier)
    matches = [field for field in fields if field.tag == tag and field.qualifier == qualifier]
    if not matches:
        raise ParseError(source, fields[-1].line, f"message ends without {label}", label)
    if len(matches) > 1:
        lines = " and ".join(str(field.line) for field in matches)
        raise ParseError(
            source, matches[1].line, f"{label} appears more than once (lines {lines})", label
        )
    return matches[0]


def _convert[T](field: Field, convert: Callable[[str], T], source: str) -> T:
    """Run a converter on a field's value. Its error becomes a ParseError naming the line."""
    try:
        return convert(field.value)
    except (ValueError, InvalidIdentifier) as error:
        raise ParseError(source, field.line, str(error), _label(field)) from None


def _label_for(tag: str, qualifier: str | None) -> str:
    """How a field is written in a message: ":98A::SETT" or ":23G:"."""
    return f":{tag}::{qualifier}" if qualifier else f":{tag}:"


def _label(field: Field) -> str:
    return _label_for(field.tag, field.qualifier)


def _parse_isin(value: str) -> ISIN:
    """ "ISIN DE0007164600" -> ISIN("DE0007164600"), with its check digit validated."""
    if not value.startswith("ISIN "):
        raise ValueError(f"expected 'ISIN' then the code, got {value!r}")
    return ISIN(value.removeprefix("ISIN "))


def _parse_quantity(value: str) -> Decimal:
    """ "UNIT/1000," -> Decimal("1000"). UNIT means shares; FAMT (bonds) isn't supported."""
    if not value.startswith("UNIT/"):
        raise ValueError(f"quantity must be UNIT/<number> (shares), got {value!r}")
    quantity = parse_swift_decimal(value.removeprefix("UNIT/"))
    if quantity <= 0:
        raise ValueError(f"quantity must be more than zero, got {value!r}")
    return quantity


def _parse_amount(value: str) -> tuple[str, Decimal]:
    """ "EUR150250,00" -> ("EUR", Decimal("150250.00"))."""
    match = _AMOUNT.fullmatch(value)
    if match is None:
        raise ValueError(f"amount must be a currency then a SWIFT number, got {value!r}")
    amount = parse_swift_decimal(match["number"])
    if amount <= 0:
        raise ValueError(f"amount must be more than zero, got {value!r}")
    return match["currency"], amount
