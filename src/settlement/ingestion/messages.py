"""Parse simplified ISO 15022 (SWIFT) settlement messages into a common format for
ingestion into the settlement system: MT541/543 instructions and MT548 status messages."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

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
