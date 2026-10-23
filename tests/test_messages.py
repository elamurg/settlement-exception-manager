"""Test for ISO 15022 message parser"""

from datetime import date
from decimal import Decimal

import pytest

from settlement.ingestion.messages import parse_swift_date, parse_swift_decimal

# SWIFT decimals


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1000,", Decimal("1000.0")),  # whole number: comma, nothing after it
        ("150250,00", Decimal("150250.00")),
        ("0,5", Decimal("0.5")),
    ],
)
def test_swift_decimal_valid(text: str, expected: Decimal) -> None:
    """Test that valid SWIFT decimal values are parsed correctly."""
    assert parse_swift_decimal(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "1000.00",  # dot instead of comma
        "1000",  # no comma at all
        "1,000,00",  # two commas
        ",5",  # nothing before the comma
        "",
        "abc",
    ],
)
def test_swift_decimal_invalid(text: str) -> None:
    """Test that invalid SWIFT decimal values raise a ValueError."""
    with pytest.raises(ValueError):
        parse_swift_decimal(text)


# SWIFT dates


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20261005", date(2026, 10, 5)),
        ("20261231", date(2026, 12, 31)),
    ],
)
def test_swift_date_valid(text: str, expected: date) -> None:
    """Test that valid SWIFT dates are parsed correctly."""
    assert parse_swift_date(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-05",  # dashes: that's ISO format, not SWIFT
        "2026105",  # only 7 digits
        "202610050",  # 9 digits
        "20261305",  # right shape, but there's no month 13
        "20260230",  # right shape, but there's no 30 February
        "",
    ],
)
def test_swift_date_invalid(text: str) -> None:
    """Test that invalid SWIFT dates raise a ValueError."""
    with pytest.raises(ValueError):
        parse_swift_date(text)


def test_impossible_date_says_so() -> None:
    """A date with the right shape but no such day gets its own clear message."""
    with pytest.raises(ValueError, match="not a real date"):
        parse_swift_date("20261305")
