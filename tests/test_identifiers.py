"""Tests for ISIN and LEI validation (SE-04, issue #4)."""

from dataclasses import FrozenInstanceError

import pytest

from settlement.domain.errors import DomainError, InvalidIdentifier
from settlement.domain.identifiers import ISIN, LEI

# Real, published identifiers, so the check digits are known to be right.
VALID_ISINS = [
    "US0378331005",  # Apple
    "GB0002634946",  # BAE Systems
    "DE0007164600",  # SAP
    "GB00B03MLX29",  # Shell: letters inside the body, not only the country code
]

VALID_LEIS = [
    "HWUPKR0MPOU8FGXBT394",  # Apple
    "5493001KJTIIGC8Y1R12",  # Bloomberg Finance
]


# ISIN


@pytest.mark.parametrize("value", VALID_ISINS)
def test_valid_isin_is_accepted(value: str) -> None:
    isin = ISIN(value)

    assert isin.value == value
    assert str(isin) == value


@pytest.mark.parametrize(
    "value",
    [
        "US0378331006",  # Apple with the last digit changed
        "GB0002634947",  # BAE with the last digit changed
        "US0378331050",  # two digits swapped
    ],
)
def test_isin_with_wrong_check_digit_is_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifier, match="check digit"):
        ISIN(value)


@pytest.mark.parametrize(
    "value",
    [
        "us0378331005",  # lowercase: parsers must normalise first
        "US037833100",  # 11 characters
        "US03783310055",  # 13 characters
        "",
        "120378331005",  # country code must be letters
        "US037833100A",  # check digit must be a digit
        "US03783310-5",  # punctuation
        " US0378331005",  # surrounding whitespace
    ],
)
def test_isin_with_wrong_format_is_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifier, match="format"):
        ISIN(value)


@pytest.mark.parametrize("value", [378331005, None, b"US0378331005"])
def test_isin_must_be_a_string(value: object) -> None:
    with pytest.raises(InvalidIdentifier, match="must be a string"):
        ISIN(value)  # type: ignore[arg-type]


# LEI


@pytest.mark.parametrize("value", VALID_LEIS)
def test_valid_lei_is_accepted(value: str) -> None:
    lei = LEI(value)

    assert lei.value == value
    assert str(lei) == value


@pytest.mark.parametrize(
    "value",
    [
        "HWUPKR0MPOU8FGXBT395",  # Apple with the last digit changed
        "5493001KJTIIGC8Y1R21",  # check digits swapped
    ],
)
def test_lei_with_wrong_check_digits_is_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifier, match="check digits"):
        LEI(value)


@pytest.mark.parametrize(
    "value",
    [
        "hwupkr0mpou8fgxbt394",  # lowercase
        "HWUPKR0MPOU8FGXBT39",  # 19 characters
        "HWUPKR0MPOU8FGXBT3944",  # 21 characters
        "",
        "HWUPKR0MPOU8FGXBT3A4",  # check digits must be digits
        "HWUPKR0MPOU8FGXB-394",  # punctuation
    ],
)
def test_lei_with_wrong_format_is_rejected(value: str) -> None:
    with pytest.raises(InvalidIdentifier, match="format"):
        LEI(value)


@pytest.mark.parametrize("value", [12345678901234567890, None])
def test_lei_must_be_a_string(value: object) -> None:
    with pytest.raises(InvalidIdentifier, match="must be a string"):
        LEI(value)  # type: ignore[arg-type]


# Shared behaviour


def test_identifiers_are_immutable() -> None:
    isin = ISIN("US0378331005")

    with pytest.raises(FrozenInstanceError):
        isin.value = "GB0002634946"  # type: ignore[misc]


def test_identifiers_compare_by_value() -> None:
    assert ISIN("US0378331005") == ISIN("US0378331005")
    assert LEI("HWUPKR0MPOU8FGXBT394") != LEI("5493001KJTIIGC8Y1R12")


def test_invalid_identifier_is_a_domain_error() -> None:
    with pytest.raises(DomainError):
        ISIN("not an isin")
