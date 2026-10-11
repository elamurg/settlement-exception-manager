"""Tests for settlement.domain.penalties.

Every expected figure is computed by hand: EUR 1,000,000 failing at 1bp is EUR 100.00 per
business day on the full amount. Dates are a real week in October 2026.
"""

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

WED = date(2026, 10, 7)
THU = date(2026, 10, 8)
FRI = date(2026, 10, 9)
SAT = date(2026, 10, 10)
SUN = date(2026, 10, 11)
MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)

NO_HOLIDAYS: frozenset[date] = frozenset()
MON_HOLIDAY: frozenset[date] = frozenset({MON})

AMOUNT = Decimal("1000000")
RATE = Decimal("0.0001")  # 100.00 per day


def penalty(
    *,
    failed_amount: Decimal = AMOUNT,
    daily_rate: Decimal = RATE,
    intended_settlement_date: date = THU,
    settlement_date: date = TUE,
    holidays: frozenset[date] = NO_HOLIDAYS,
    partials: tuple[PartialSettlement, ...] = (),
) -> Decimal:
    """accrue_penalty with sensible defaults; each test overrides only what it is about."""
    return accrue_penalty(
        failed_amount, daily_rate, intended_settlement_date, settlement_date, holidays, partials
    )


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
        pytest.param(THU, TUE, NO_HOLIDAYS, [THU, FRI, MON], id="weekend_skipped"),
        pytest.param(THU, TUE, MON_HOLIDAY, [THU, FRI], id="holiday_skipped"),
        pytest.param(THU, FRI, NO_HOLIDAYS, [THU], id="start_included_end_excluded"),
        pytest.param(THU, THU, NO_HOLIDAYS, [], id="same_day"),
        pytest.param(SAT, MON, NO_HOLIDAYS, [], id="only_weekend"),
    ],
)
def test_business_days(
    start: date, end: date, holidays: frozenset[date], expected: list[date]
) -> None:
    assert business_days(start, end, holidays) == expected


def test_daily_rate_for_returns_configured_rate() -> None:
    rates = {AssetClass.LIQUID_SHARES: Decimal("0.0001")}
    assert daily_rate_for(AssetClass.LIQUID_SHARES, rates) == Decimal("0.0001")


def test_daily_rate_for_missing_asset_class_raises() -> None:
    rates = {AssetClass.LIQUID_SHARES: Decimal("0.0001")}
    with pytest.raises(InvalidPenaltyInput):
        daily_rate_for(AssetClass.CORPORATE_BONDS, rates)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("123.451", "123.45"),
        ("123.455", "123.46"),
        ("123.459", "123.46"),
        ("0.004", "0.00"),
        ("0.005", "0.01"),
        ("100", "100.00"),
    ],
)
def test_round_money(amount: str, expected: str) -> None:
    assert str(round_money(Decimal(amount))) == expected


def test_one_day_rounds_to_the_cent() -> None:
    assert penalty(failed_amount=Decimal("1234567.89"), settlement_date=FRI) == Decimal("123.46")


def test_rounding_is_per_day_not_on_the_total() -> None:
    assert penalty(failed_amount=Decimal("1234567.89")) == Decimal("370.38")


@pytest.mark.parametrize(
    ("intended", "settled", "holidays", "expected"),
    [
        pytest.param(THU, THU, NO_HOLIDAYS, "0", id="same_day_resolution"),
        pytest.param(THU, FRI, NO_HOLIDAYS, "100.00", id="one_day"),
        pytest.param(THU, TUE, NO_HOLIDAYS, "300.00", id="over_weekend"),
        pytest.param(THU, TUE, MON_HOLIDAY, "200.00", id="over_weekend_and_holiday"),
        pytest.param(FRI, MON, NO_HOLIDAYS, "100.00", id="friday_to_monday"),
        pytest.param(FRI, TUE, MON_HOLIDAY, "100.00", id="friday_to_tuesday_monday_holiday"),
        pytest.param(MON, TUE, MON_HOLIDAY, "0", id="intended_date_is_holiday"),
    ],
)
def test_penalty_figures(
    intended: date, settled: date, holidays: frozenset[date], expected: str
) -> None:
    result = penalty(intended_settlement_date=intended, settlement_date=settled, holidays=holidays)
    assert result == Decimal(expected)


def test_zero_rate_is_allowed() -> None:
    assert penalty(daily_rate=Decimal("0")) == Decimal("0")


def test_partial_on_friday() -> None:
    partials = (PartialSettlement(FRI, Decimal("600000")),)
    assert penalty(partials=partials) == Decimal("180.00")  # 100 + 40 + 40


def test_partial_on_intended_settlement_date() -> None:
    partials = (PartialSettlement(THU, Decimal("600000")),)
    assert penalty(partials=partials) == Decimal("120.00")  # 40 + 40 + 40


def test_two_partials() -> None:
    partials = (
        PartialSettlement(FRI, Decimal("300000")),
        PartialSettlement(MON, Decimal("300000")),
    )
    assert penalty(partials=partials) == Decimal("210.00")


def test_partial_on_weekend_counts_from_next_business_day() -> None:
    partials = (PartialSettlement(SAT, Decimal("600000")),)
    assert penalty(partials=partials) == Decimal("240.00")


def test_partial_settling_thursday_to_monday() -> None:
    partials = (PartialSettlement(FRI, Decimal("500000")),)
    result = penalty(settlement_date=MON, partials=partials)
    assert result == Decimal("150.00")


@pytest.mark.parametrize(
    ("settled_on", "amount"),
    [
        pytest.param(FRI, Decimal("0"), id="zero"),
        pytest.param(FRI, Decimal("-500000"), id="negative"),
        pytest.param(FRI, Decimal("NaN"), id="nan"),
        pytest.param(FRI, Decimal("Infinity"), id="infinity"),
        pytest.param(FRI, 500000.0, id="float"),
        pytest.param("2026-10-09", Decimal("500000"), id="date_as_text"),
    ],
)
def test_invalid_partial_settlement_raises(settled_on: object, amount: object) -> None:
    # Built inside the test, not in the list above: the list is evaluated when the file
    # loads, and an error there would stop every test in this file from running.
    with pytest.raises(InvalidPenaltyInput):
        PartialSettlement(settled_on, amount)  # type: ignore[arg-type]


# Each case changes one argument of an otherwise valid call; penalty(**overrides)
# unpacks {"failed_amount": X} into penalty(failed_amount=X).
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"failed_amount": Decimal("0")}, id="zero_amount"),
        pytest.param({"failed_amount": Decimal("-1")}, id="negative_amount"),
        pytest.param({"failed_amount": 1000000.0}, id="float_amount"),
        pytest.param({"failed_amount": Decimal("NaN")}, id="nan_amount"),
        pytest.param({"failed_amount": Decimal("Infinity")}, id="infinite_amount"),
        pytest.param({"daily_rate": 0.0001}, id="float_rate"),
        pytest.param({"daily_rate": Decimal("-0.0001")}, id="negative_rate"),
        pytest.param({"daily_rate": Decimal("NaN")}, id="nan_rate"),
        pytest.param({"daily_rate": Decimal("Infinity")}, id="infinite_rate"),
        pytest.param({"settlement_date": WED}, id="settles_before_intended_date"),
        pytest.param(
            {"partials": (PartialSettlement(WED, Decimal("1")),)}, id="partial_before_intended"
        ),
        pytest.param(
            {"partials": (PartialSettlement(TUE, Decimal("1")),)}, id="partial_on_settlement"
        ),
        pytest.param({"partials": (PartialSettlement(FRI, AMOUNT),)}, id="partial_covers_all"),
        pytest.param(
            {
                "partials": (
                    PartialSettlement(FRI, Decimal("600000")),
                    PartialSettlement(MON, Decimal("600000")),
                )
            },
            id="partials_exceed_amount",
        ),
    ],
)
def test_invalid_input_raises(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidPenaltyInput):
        penalty(**overrides)  # type: ignore[arg-type]
