"""Test for ISO 15022 message parser"""

from datetime import date
from decimal import Decimal

import pytest

from settlement.domain.identifiers import ISIN
from settlement.domain.models import Side
from settlement.ingestion.errors import ParseError
from settlement.ingestion.messages import (
    InstructionMessage,
    check_sections,
    parse_mt541,
    parse_mt543,
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


# MT541 / MT543 instructions

MT541 = """\
:16R:GENL
:20C::SEME//INSTR-000123
:23G:NEWM
:16S:GENL
:16R:TRADDET
:98A::TRAD//20261005
:98A::SETT//20261006
:35B:ISIN DE0007164600
:16S:TRADDET
:16R:FIAC
:36B::SETT//UNIT/1000,
:97A::SAFE//0012345678
:16S:FIAC
:16R:SETDET
:19A::SETT//EUR150250,00
:16S:SETDET
"""


def replace_line(old: str, new: str) -> str:
    """The example MT541 with one line swapped for another."""
    assert old in MT541
    return MT541.replace(old, new)


def test_mt541_is_parsed() -> None:
    assert parse_mt541(MT541) == InstructionMessage(
        reference="INSTR-000123",
        function="NEWM",
        side=Side.BUY,
        trade_date=date(2026, 10, 5),
        settlement_date=date(2026, 10, 6),
        isin=ISIN("DE0007164600"),
        quantity=Decimal("1000"),
        account="0012345678",
        currency="EUR",
        amount=Decimal("150250.00"),
    )


def test_mt543_is_a_sell() -> None:
    assert parse_mt543(MT541).side == Side.SELL


def test_cancellation_is_accepted() -> None:
    assert parse_mt541(replace_line(":23G:NEWM", ":23G:CANC")).function == "CANC"


@pytest.mark.parametrize(
    "line",
    [
        ":20C::SEME//INSTR-000123",
        ":23G:NEWM",
        ":98A::TRAD//20261005",
        ":98A::SETT//20261006",
        ":35B:ISIN DE0007164600",
        ":36B::SETT//UNIT/1000,",
        ":97A::SAFE//0012345678",
        ":19A::SETT//EUR150250,00",
    ],
)
def test_every_field_is_required(line: str) -> None:
    label = line.split("//")[0] if "//" in line else line[:5]

    with pytest.raises(ParseError, match=f"message ends without {label}"):
        parse_mt541(replace_line(line + "\n", ""))


def test_a_repeated_field_is_rejected() -> None:
    text = replace_line(":98A::SETT//20261006", ":98A::SETT//20261006\n:98A::SETT//20261007")

    with pytest.raises(
        ParseError, match=r"line 8, .*:98A::SETT appears more than once \(lines 7 and 8\)"
    ):
        parse_mt541(text)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (":23G:NEWM", ":23G:XXXX", "must be NEWM or CANC"),
        (":98A::TRAD//20261005", ":98A::TRAD//2026-10-05", "Invalid SWIFT date"),
        (
            ":98A::SETT//20261006",
            ":98A::SETT//20261004",
            "settlement date is before the trade date",
        ),
        (":35B:ISIN DE0007164600", ":35B:ISIN DE0007164601", "check digit"),  # last digit changed
        (":35B:ISIN DE0007164600", ":35B:DE0007164600", "expected 'ISIN'"),
        (":36B::SETT//UNIT/1000,", ":36B::SETT//FAMT/1000,", "must be UNIT"),  # bonds: not yet
        (":36B::SETT//UNIT/1000,", ":36B::SETT//UNIT/0,", "more than zero"),
        (":36B::SETT//UNIT/1000,", ":36B::SETT//UNIT/1000.00", "Invalid SWIFT decimal"),
        (":19A::SETT//EUR150250,00", ":19A::SETT//150250,00", "currency then a SWIFT number"),
        (":19A::SETT//EUR150250,00", ":19A::SETT//EUR0,", "more than zero"),
    ],
)
def test_bad_values_name_the_line_and_field(old: str, new: str, message: str) -> None:
    line = MT541.splitlines().index(old) + 1
    label = new.split("//")[0] if "//" in new else new[:5]

    with pytest.raises(ParseError) as caught:
        parse_mt541(replace_line(old, new))

    assert caught.value.line == line
    assert caught.value.field == label
    assert message in str(caught.value)


def test_broken_sections_are_rejected() -> None:
    with pytest.raises(ParseError, match="SETDET is never closed"):
        parse_mt541(replace_line(":16S:SETDET\n", ""))


def test_empty_message_is_rejected() -> None:
    with pytest.raises(ParseError, match="message is empty"):
        parse_mt541("\n\n")


def test_error_names_the_message_type() -> None:
    with pytest.raises(ParseError, match="^MT543 line 3, field ':23G:'"):
        parse_mt543(replace_line(":23G:NEWM", ":23G:XXXX"))
