"""ISIN and LEI identifiers for settlement domain objects."""
# ISIN (international security identification number) = is a 12-digit alphanumeric code that
# uniquely identifies a specific financial security globally across trading, clearing,
# and settlement.

# LEI (legal entity identifier) = is a 20-character alphanumeric code that uniquely identifies
# a legal entity that engages in financial transactions.

import re
from dataclasses import dataclass

from settlement.domain.errors import InvalidIdentifier

# 2-letter country code, 9 alphanumeric characters, 1 check digit.
_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

# 18 alphanumeric characters, 2 check digits.
_LEI_PATTERN = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")


def _letters_to_digits(text: str) -> str:
    """Replace each letter with its number (A=10 ... Z=35); digits stay as they are.
    int(char, 36) reads a character as a base-36 number, which is exactly this mapping:
    "7" -> 7, "A" -> 10, "Z" -> 35.
    """
    return "".join(str(int(char, 36)) for char in text)


def _isin_check_digit_ok(isin: str) -> bool:
    """Luhn check over the first 11 characters, compared with the 12th."""
    digits = _letters_to_digits(isin[:-1])
    total = 0

    for position, char in enumerate(reversed(digits)):
        digit = int(char)
        if position % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    expected = (10 - total % 10) % 10
    return expected == int(isin[-1])


def _lei_checksum_ok(lei: str) -> bool:
    """ISO 7064 MOD 97-10: the whole LEI, as one number, leaves remainder 1 when divided by 97."""
    return int(_letters_to_digits(lei)) % 97 == 1


@dataclass(frozen=True, slots=True)
class ISIN:
    """International Securities Identification Number, validated on construction."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidIdentifier(f"ISIN must be a string, got {type(self.value).__name__}")
        if not _ISIN_PATTERN.fullmatch(self.value):
            raise InvalidIdentifier(f"Invalid ISIN format: {self.value!r}")
        if not _isin_check_digit_ok(self.value):
            raise InvalidIdentifier(f"Invalid ISIN check digit: {self.value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class LEI:
    """Legal Entity Identifier, validated on construction."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidIdentifier(f"LEI must be a string, got {type(self.value).__name__}")
        if not _LEI_PATTERN.fullmatch(self.value):
            raise InvalidIdentifier(f"Invalid LEI format: {self.value!r}")
        if not _lei_checksum_ok(self.value):
            raise InvalidIdentifier(f"Invalid LEI check digits: {self.value!r}")

    def __str__(self) -> str:
        return self.value
