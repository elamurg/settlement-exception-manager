"""ACCRUAL PENALTIES: When a trade fails, the regulator charges a fine for every
business day it stays unsettled. This file is used as a calculator.

Given how much failed, for how long and what rate, it works out a penalty."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from settlement.domain.errors import InvalidPenaltyInput

# round it up to the nearest cent
_CENT = Decimal("0.01")


class AssetClass(StrEnum):
    LIQUID_SHARES = "LIQUID_SHARES"
    ILLIQUID_SHARES = "ILLIQUID_SHARES"
    SOVEREIGN_BONDS = "SOVEREIGN_BONDS"
    CORPORATE_BONDS = "CORPORATE_BONDS"


@dataclass(frozen=True, slots=True)
class PartialSettlement:
    """Only part of the failed amount is settled on a given day."""

    settled_on: date
    amount: Decimal


def is_business_day(day: date, holidays: frozenset[date]) -> bool:
    """Returns True if `day` is Monday to Friday and not a holiday."""
    if day.weekday() > 4:  # 5 = Sat, 6 = Sun
        return False
    if day in holidays:
        return False
    return True


def business_days(start: date, end: date, holidays: frozenset[date]) -> list[date]:
    """Amount of time between the when the trade is supposed to be settled
    (start - included in fine), to the day it actually is (end - not included),
    without holidays."""
    result = []
    day = start
    while day < end:
        if is_business_day(day, holidays):
            result.append(day)
        # check formating, timedelta increases the date by 1
        day = day + timedelta(days=1)
    return result


def daily_rate_for(asset_class: AssetClass, rates: Mapping[AssetClass, Decimal]) -> Decimal:
    if asset_class not in rates:
        raise InvalidPenaltyInput(f"No penalty rate can be configured for {asset_class}")
    return rates[asset_class]


# business decision to round up half
def round_money(amount: Decimal) -> Decimal:
    """Round to the same number of decimal places as 0.01"""
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def accrue_penalty(
    failed_amount: Decimal,
    daily_rate: Decimal,
    intended_settlement_date: date,
    settlement_date: date,
    holidays: frozenset[date],
    partials: tuple[PartialSettlement, ...] = (),
) -> Decimal:
    """Total penalty for a fail, rounded per day"""
    # reduse immposible imput
    # using isinstance() to check if a value is type x
    if not isinstance(failed_amount, Decimal) or failed_amount <= 0:
        raise InvalidPenaltyInput("Failed amount has to be a positive decimal number.")
    if not isinstance(daily_rate, Decimal) or daily_rate < 0:
        raise InvalidPenaltyInput("Daily rate has to be a decimal number larger than zero.")
    if settlement_date < intended_settlement_date:
        raise InvalidPenaltyInput(
            "Settlement date cannot come before the intended settlement date."
        )
    for partial in partials:
        if partial.amount == 0:
            raise InvalidPenaltyInput("Partial amount cannot be zero.")
        if partial.settled_on < intended_settlement_date or partial.settled_on >= settlement_date:
            raise InvalidPenaltyInput(
                "Partial settlement cannot come before the intended settlement date."
            )
    if sum(partial.amount for partial in partials) >= failed_amount:
        raise InvalidPenaltyInput("Sum of partials cannot be larger than failed amount.")

    # walk through the fail days
    total = Decimal("0")
    for day in business_days(intended_settlement_date, settlement_date, holidays):
        settled_so_far = sum(partial.amount for partial in partials if partial.settled_on <= day)
        still_failing = failed_amount - settled_so_far
        total = total + round_money(still_failing * daily_rate)
    return total
