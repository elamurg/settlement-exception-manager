"""Parse simplified ISO 15022 (SWIFT) settlement messages into a common format for
ingestion into the settlement system: MT541/543 instructions and MT548 status messages."""

import re
from datetime import date
from decimal import Decimal

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
