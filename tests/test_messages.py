"""Test for ISO 15022 message parser"""

from datetime import date
from decimal import Decimal

import pytest

from settlement.ingestion.errors import ParseError
from settlement.ingestion.messages import (
    check_sections,
    parse_swift_date,
    parse_swift_decimal,
    split_fields,
)

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


# Splitting into fields


def test_field_with_qualifier() -> None:
    (field,) = split_fields(":98A::TRAD//20261005")
    assert (field.tag, field.qualifier, field.value) == ("98A", "TRAD", "20261005")


def test_field_without_qualifier() -> None:
    (field,) = split_fields(":23G:NEWM")
    assert (field.tag, field.qualifier, field.value) == ("23G", None, "NEWM")


def test_value_can_contain_slashes_and_spaces() -> None:
    quantity, security = split_fields(":36B::SETT//UNIT/1000,\n:35B:ISIN DE0007164600")
    assert (quantity.qualifier, quantity.value) == ("SETT", "UNIT/1000,")
    assert (security.qualifier, security.value) == (None, "ISIN DE0007164600")


def test_line_numbers_skip_blank_lines() -> None:
    fields = split_fields(":16R:GENL\n\n:23G:NEWM")
    assert [field.line for field in fields] == [1, 3]


@pytest.mark.parametrize(
    "line",
    [
        "hello",  # not a field at all
        "98A::TRAD//20261005",  # missing the opening colon
        ":9A::TRAD//20261005",  # tag too short
        ":98a::TRAD//20261005",  # tag letter must be uppercase
    ],
)
def test_not_a_field_is_a_parse_error(line: str) -> None:
    with pytest.raises(ParseError, match="line 2: not a SWIFT field"):
        split_fields(":16R:GENL\n" + line)


@pytest.mark.parametrize("line", [":23G:", ":98A::TRAD//"])
def test_field_without_a_value_is_a_parse_error(line: str) -> None:
    with pytest.raises(ParseError, match="line 1, field '.*': has no value"):
        split_fields(line)


# Sections (:16R: opens, :16S: closes)


def test_nested_sections_are_fine() -> None:
    text = """
:16R:GENL
:20C::SEME//INSTR-1
:16S:GENL
:16R:TRADDET
:16R:FIAC
:36B::SETT//UNIT/1000,
:16S:FIAC
:16S:TRADDET
"""
    check_sections(split_fields(text))  # no error means it passed


def test_closed_but_never_opened() -> None:
    fields = split_fields(":20C::SEME//INSTR-1\n:16S:GENL")

    with pytest.raises(
        ParseError, match="line 2, field '16S': closes GENL, which was never opened"
    ):
        check_sections(fields)


def test_closed_in_the_wrong_order() -> None:
    fields = split_fields(":16R:TRADDET\n:16R:FIAC\n:16S:TRADDET\n:16S:FIAC")

    with pytest.raises(
        ParseError, match=r"line 3, .*closes TRADDET, but FIAC \(opened on line 2\) is still open"
    ):
        check_sections(fields)


def test_never_closed() -> None:
    fields = split_fields(":16R:GENL\n:20C::SEME//INSTR-1")

    with pytest.raises(ParseError, match="line 1, field '16R': GENL is never closed"):
        check_sections(fields)


def test_never_closed_points_at_the_innermost_section() -> None:
    fields = split_fields(":16R:TRADDET\n:16R:FIAC\n:36B::SETT//UNIT/1000,")

    with pytest.raises(ParseError, match="line 2, field '16R': FIAC is never closed"):
        check_sections(fields)
