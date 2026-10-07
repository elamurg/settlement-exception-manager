"""Tests for settlement.domain.penalties"""

from datetime import date
from decimal import Decimal

import pytest

from settlement.domain.errors import InvalidPenaltyInput
from settlement.domain.penalties import (
    AssetClass,
    PartialSettlement,
    accrue_penalty,
    business_days,
    daily_rate_for,
    is_business_day,
    round_money,
)

THU = date(2026, 10, 8)
FRI = date(2026, 10, 9)
SAT = date(2026, 10, 10)
SUN = date(2026, 10, 11)
MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)

NO_HOLIDAYS: frozenset[date] = frozenset()
MON_HOLIDAY: frozenset[date] = frozenset({MON})

AMOUNT = Decimal("1000000")
RATE = Decimal("0.0001")  # 100 pounds per day on the full amount


def penalty(
    *,
    failed_amount: Decimal = AMOUNT,
    daily_rate: Decimal = RATE,
    intended_settlement_date: date = THU,
    settlement_date: date = TUE,
    holidays: frozenset[date] = NO_HOLIDAYS,
    partials: tuple[PartialSettlement, ...] = (),
) -> Decimal:
    """accrue_penalty with sensible defaults"""
    return accrue_penalty(
        failed_amount, daily_rate, intended_settlement_date, settlement_date, holidays, partials
    )


def test_same_day_settlement() -> None:
    assert penalty(daily_rate=RATE, intended_settlement_date=FRI, settlement_date=FRI) == Decimal(
        "0.00"
    )


def test_one_day_penalty() -> None:
    assert penalty(intended_settlement_date=THU, settlement_date=FRI) == Decimal("100.00")


def test_weekend_penalty() -> None:
    assert penalty(
        intended_settlement_date=FRI, settlement_date=MON, holidays=NO_HOLIDAYS
    ) == Decimal("100.00")


def test_holiday_penalty() -> None:
    assert penalty(
        intended_settlement_date=FRI, settlement_date=TUE, holidays=MON_HOLIDAY
    ) == Decimal("100.00")


def test_partial_penalty() -> None:
    assert penalty(
        intended_settlement_date=THU,
        settlement_date=MON,
        holidays=NO_HOLIDAYS,
        partials=(PartialSettlement(settled_on=FRI, amount=Decimal("500000")),),
    ) == Decimal("150.00")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("123.451", "123.45"),
        ("123.455", "123.46"),
        ("0.005", "0.01"),
        ("100", "100.00"),
    ],
)
def test_round_money(amount: str, expected: str) -> None:
    assert str(round_money(Decimal(amount))) == expected


def test_rounding_is_per_day() -> None:
    assert penalty(failed_amount=Decimal("1234567.89")) == Decimal("370.38")


@pytest.mark.parametrize(
    ("day", "holidays", "expected"),
    [
        (THU, NO_HOLIDAYS, True),
        (FRI, NO_HOLIDAYS, True),
        (SAT, NO_HOLIDAYS, False),
        (SUN, NO_HOLIDAYS, False),
        (MON, NO_HOLIDAYS, True),
        (MON, MON_HOLIDAY, False),
    ],
)
def test_is_business_day(day: date, holidays: frozenset[date], expected: bool) -> None:
    assert is_business_day(day, holidays) == expected


@pytest.mark.parametrize(
    ("start", "end", "holidays", "expected"),
    [
        (THU, TUE, NO_HOLIDAYS, [THU, FRI, MON]),  # weekend skipped
        (THU, TUE, MON_HOLIDAY, [THU, FRI]),  # holiday skipped
        (THU, FRI, NO_HOLIDAYS, [THU]),  # start included, end excluded
        (THU, THU, NO_HOLIDAYS, []),  # same day
        (SAT, MON, NO_HOLIDAYS, []),  # only weekend days
    ],
)
def test_business_days(
    start: date, end: date, holidays: frozenset[date], expected: list[date]
) -> None:
    assert business_days(start, end, holidays) == expected


def test_daily_rate_for() -> None:
    rates = {AssetClass.LIQUID_SHARES: Decimal("0.0001")}

    assert daily_rate_for(AssetClass.LIQUID_SHARES, rates) == Decimal("0.0001")
    with pytest.raises(InvalidPenaltyInput):
        daily_rate_for(AssetClass.CORPORATE_BONDS, rates)


# i didnt write this test, i havent learned how to test paramater inputs yet
WED = date(2026, 10, 7)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"failed_amount": Decimal("0")}, id="zero_amount"),
        pytest.param({"failed_amount": Decimal("-1")}, id="negative_amount"),
        pytest.param({"failed_amount": 1000000.0}, id="float_amount"),
        pytest.param({"daily_rate": Decimal("-0.0001")}, id="negative_rate"),
        pytest.param({"settlement_date": WED}, id="settles_before_isd"),
        pytest.param({"partials": (PartialSettlement(FRI, Decimal("0")),)}, id="zero_partial"),
        pytest.param(
            {"partials": (PartialSettlement(WED, Decimal("1")),)}, id="partial_before_isd"
        ),
        pytest.param(
            {"partials": (PartialSettlement(TUE, Decimal("1")),)}, id="partial_on_settlement"
        ),
        pytest.param({"partials": (PartialSettlement(FRI, AMOUNT),)}, id="partial_covers_all"),
    ],
)
def test_invalid_input_raises(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidPenaltyInput):
        penalty(**overrides)  # type: ignore[arg-type]
