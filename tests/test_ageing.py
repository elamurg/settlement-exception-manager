"""Tests for settlement.domain.ageing: which break to fix first.

Dates are a real week in October 2026: Friday 9th, the weekend, Monday 12th, Tuesday 13th.
The default cut-off is 16:00 UTC and the default critical window is 4 hours.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from settlement.domain.ageing import Band, band_for, daily_exposure, deadline_for, prioritise
from settlement.domain.errors import InvalidModel
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import (
    Break,
    BreakType,
    FieldDifference,
    Party,
    SettlementMethod,
    Side,
    StatementLine,
    Trade,
)

FRI = date(2026, 10, 9)
SAT = date(2026, 10, 10)
MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)
WED = date(2026, 10, 14)
NO_HOLIDAYS: frozenset[date] = frozenset()
RATE = Decimal("0.0001")  # 1bp per day

LEI_BANK = LEI("HWUPKR0MPOU8FGXBT394")
ISIN_SAP = ISIN("DE0007164600")

BASE_TRADE = Trade(
    trade_id="T1",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    price=Decimal("150.25"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty=Party(party_id="CP-001", name="Example Bank plc", lei=LEI_BANK),
    trade_date=date(2026, 10, 1),
    intended_settlement_date=FRI,
    settlement_method=SettlementMethod.DVP,
)

BASE_LINE = StatementLine(
    line_id="L1",
    source="custodian",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty_lei=LEI_BANK,
    intended_settlement_date=FRI,
    reference="T1",
)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    """A UTC moment on a given day, e.g. at(FRI, 14) is Friday 14:00."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def make_break(break_id: str, settles: date, consideration: str = "150250.00") -> Break:
    """A quantity break on a trade settling on `settles`, with the given consideration."""
    trade = replace(
        BASE_TRADE,
        trade_id=f"T-{break_id}",
        intended_settlement_date=settles,
        consideration=Decimal(consideration),
    )
    return Break(
        break_id=break_id,
        break_type=BreakType.QUANTITY_BREAK,
        ours=trade,
        theirs=None,
        evidence=(FieldDifference("quantity", ours="1000", theirs="900"),),
        intended_settlement_date=settles,
        raised_at=at(date(2026, 10, 1), 9),
    )


def order(breaks: list[Break], now: datetime) -> list[str]:
    """The break ids in the order prioritise() puts them."""
    ranked = prioritise(breaks, now=now, holidays=NO_HOLIDAYS, daily_rate=RATE)
    return [item.brk.break_id for item in ranked]


# --- The ticket's "Done when" ------------------------------------------------------------


def test_break_two_hours_from_deadline_outranks_bigger_break_two_days_out() -> None:
    # Friday 14:00: the small break's cut-off is in 2 hours; the big one settles Tuesday.
    small_but_urgent = make_break("URGENT", settles=FRI, consideration="1000.00")
    big_but_later = make_break("BIG", settles=TUE, consideration="10000000.00")

    assert order([big_but_later, small_but_urgent], now=at(FRI, 14)) == ["URGENT", "BIG"]


# --- deadline_for ------------------------------------------------------------------------


def test_deadline_is_the_cutoff_on_the_settlement_date() -> None:
    assert deadline_for(FRI) == at(FRI, 16)
    assert deadline_for(FRI, time(12, 30)) == at(FRI, 12, 30)


# --- band_for ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "settles", "expected"),
    [
        pytest.param(at(FRI, 17), FRI, Band.OVERDUE, id="after_cutoff"),
        pytest.param(at(FRI, 16), FRI, Band.OVERDUE, id="exactly_at_cutoff"),
        pytest.param(at(FRI, 13), FRI, Band.CRITICAL, id="three_hours_left"),
        pytest.param(at(FRI, 12), FRI, Band.CRITICAL, id="exactly_four_hours_left"),
        pytest.param(at(FRI, 11, 59), FRI, Band.TODAY, id="just_over_four_hours_left"),
        pytest.param(at(FRI, 9), FRI, Band.TODAY, id="settles_today"),
        pytest.param(at(FRI, 9), MON, Band.NEXT_DAY, id="friday_to_monday_skips_weekend"),
        pytest.param(at(SAT, 10), MON, Band.NEXT_DAY, id="saturday_to_monday"),
        pytest.param(at(FRI, 9), TUE, Band.LATER, id="two_business_days_out"),
        pytest.param(at(MON, 9), WED, Band.LATER, id="monday_to_wednesday"),
    ],
)
def test_band(now: datetime, settles: date, expected: Band) -> None:
    assert band_for(deadline_for(settles), now, NO_HOLIDAYS) == expected


def test_holiday_does_not_count_as_a_day_away() -> None:
    # Friday to Tuesday is normally LATER, but with Monday a holiday it's the next business day.
    assert band_for(deadline_for(TUE), at(FRI, 9), NO_HOLIDAYS) == Band.LATER
    assert band_for(deadline_for(TUE), at(FRI, 9), frozenset({MON})) == Band.NEXT_DAY


# --- daily_exposure ----------------------------------------------------------------------


def test_exposure_is_consideration_times_rate_rounded_to_the_cent() -> None:
    # 150,250.00 x 0.0001 = 15.025, rounded half up to 15.03
    assert daily_exposure(make_break("B1", settles=FRI), RATE) == Decimal("15.03")


def test_exposure_uses_their_line_when_there_is_no_trade() -> None:
    brk = replace(make_break("B1", settles=FRI), ours=None, theirs=BASE_LINE)
    assert daily_exposure(brk, RATE) == Decimal("15.03")


# --- prioritise --------------------------------------------------------------------------


def test_bands_come_in_order_of_urgency() -> None:
    breaks = [
        make_break("LATER", settles=WED),
        make_break("NEXT", settles=MON),
        make_break("TODAY", settles=FRI),
        make_break("OVERDUE", settles=date(2026, 10, 8)),  # Thursday: already failed
    ]

    assert order(breaks, now=at(FRI, 9)) == ["OVERDUE", "TODAY", "NEXT", "LATER"]


def test_within_a_band_less_time_left_beats_bigger_exposure() -> None:
    # Both are LATER on Friday 09:00, but Tuesday's deadline comes first: time beats money.
    small_tuesday = make_break("TUE", settles=TUE, consideration="1000.00")
    big_wednesday = make_break("WED", settles=WED, consideration="5000000.00")

    assert order([big_wednesday, small_tuesday], now=at(FRI, 9)) == ["TUE", "WED"]


def test_bigger_exposure_breaks_a_tie() -> None:
    # Same deadline, same time left: the one that would cost more comes first.
    small = make_break("SMALL", settles=MON, consideration="1000.00")
    big = make_break("BIG", settles=MON, consideration="5000000.00")

    assert order([small, big], now=at(FRI, 9)) == ["BIG", "SMALL"]


def test_equal_breaks_are_ordered_by_break_id() -> None:
    breaks = [make_break(break_id, settles=MON) for break_id in ("B3", "B1", "B2")]

    assert order(breaks, now=at(FRI, 9)) == ["B1", "B2", "B3"]


def test_order_does_not_depend_on_input_order() -> None:
    breaks = [
        make_break("A", settles=FRI, consideration="1000.00"),
        make_break("B", settles=MON, consideration="9000.00"),
        make_break("C", settles=MON, consideration="2000.00"),
        make_break("D", settles=TUE),
    ]

    assert order(breaks, now=at(FRI, 9)) == order(breaks[::-1], now=at(FRI, 9))


def test_prioritised_break_explains_its_place() -> None:
    (item,) = prioritise(
        [make_break("B1", settles=MON)], now=at(FRI, 9), holidays=NO_HOLIDAYS, daily_rate=RATE
    )

    assert item.band == Band.NEXT_DAY
    assert item.deadline == at(MON, 16)
    assert item.time_left == at(MON, 16) - at(FRI, 9)
    assert item.business_days_left == 1
    assert item.daily_exposure == Decimal("15.03")


def test_now_without_a_timezone_is_rejected() -> None:
    with pytest.raises(InvalidModel):
        prioritise(
            [make_break("B1", settles=MON)],
            now=datetime(2026, 10, 9, 9, 0),  # no tzinfo
            holidays=NO_HOLIDAYS,
            daily_rate=RATE,
        )
