"""The reference data trades are drawn from: instruments and counterparties.

Identifiers are random but valid, check digits included, so anything that parses them
later sees the same thing it would see in real data.
"""

import random
import string
from dataclasses import dataclass
from datetime import date

from settlement.domain.errors import InvalidIdentifier
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import SSI, Party

_ALPHANUMERIC = string.ascii_uppercase + string.digits

# Country of issue -> settlement currency.
_MARKETS = {"GB": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR", "IE": "EUR", "US": "USD"}

_NAME_STEMS = [
    "Northgate", "Harbour", "Albion", "Meridian", "Thames", "Rhine", "Alder", "Kestrel",
    "Fenwick", "Calder", "Holborn", "Lindqvist", "Marlow", "Ostrava", "Pennant", "Whitcombe",
]  # fmt: skip
_NAME_KINDS = ["Securities", "Capital Markets", "Bank", "Asset Management", "Brokers"]

# Where a custodian for each currency is based, for the country part of its BIC.
_CUSTODIAN_COUNTRIES = {"GBP": ["GB"], "EUR": ["DE", "FR", "NL", "IE"], "USD": ["US"]}
SSI_VALID_FROM = date(2026, 1, 1)


@dataclass(frozen=True, slots=True)
class Instrument:
    isin: ISIN
    currency: str


def make_isin(rng: random.Random, country: str) -> ISIN:
    """A random ISIN for `country` with a correct check digit."""
    body = country + "".join(rng.choices(_ALPHANUMERIC, k=9))
    # Exactly one of the ten digits passes the Luhn check; ISIN itself decides which.
    for digit in string.digits:
        try:
            return ISIN(body + digit)
        except InvalidIdentifier:
            continue
    raise AssertionError(f"no check digit found for {body}")  # unreachable


def make_lei(rng: random.Random) -> LEI:
    """A random LEI with correct ISO 7064 MOD 97-10 check digits."""
    # Real LEIs: 4-character issuer prefix, "00", then 12 characters for the entity.
    base = "".join(rng.choices(string.digits, k=4)) + "00"
    base += "".join(rng.choices(_ALPHANUMERIC, k=12))
    as_number = int("".join(str(int(char, 36)) for char in base + "00"))
    return LEI(f"{base}{98 - as_number % 97:02d}")


def make_instruments(rng: random.Random, count: int) -> list[Instrument]:
    countries = sorted(_MARKETS)
    instruments = []
    for _ in range(count):
        country = rng.choice(countries)
        instruments.append(Instrument(isin=make_isin(rng, country), currency=_MARKETS[country]))
    return instruments


def make_parties(rng: random.Random, count: int) -> list[Party]:
    names = [f"{stem} {kind}" for stem in _NAME_STEMS for kind in _NAME_KINDS]
    if count > len(names):
        raise ValueError(f"at most {len(names)} counterparties, asked for {count}")
    chosen = rng.sample(names, k=count)
    return [
        Party(party_id=f"CP-{number:03d}", name=name, lei=make_lei(rng))
        for number, name in enumerate(chosen, start=1)
    ]


def make_bic(rng: random.Random, country: str) -> str:
    """A random 8-character BIC: bank code, country, location."""
    bank = "".join(rng.choices(string.ascii_uppercase, k=4))
    return bank + country + "".join(rng.choices(_ALPHANUMERIC, k=2))


def make_account(rng: random.Random) -> str:
    return "".join(rng.choices(string.digits, k=10))


def make_ssis(rng: random.Random, parties: list[Party]) -> list[SSI]:
    """One SSI per counterparty per currency: where that counterparty settles."""
    return [
        SSI(
            party_id=party.party_id,
            currency=currency,
            custodian_bic=make_bic(rng, rng.choice(countries)),
            safekeeping_account=make_account(rng),
            cash_account=make_account(rng),
            valid_from=SSI_VALID_FROM,
        )
        for party in parties
        for currency, countries in sorted(_CUSTODIAN_COUNTRIES.items())
    ]
