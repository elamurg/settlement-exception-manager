"""Tests for settlement.domain.tolerance.
Hand-computed figures use the EUR rule (5.00 or 0.01%) on our consideration of 150,250.00:
allowed = max(5.00, 0.0001 x 150,250.00) = max(5.00, 15.025) = 15.025.
"""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from settlement.domain.errors import InvalidModel
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.matching import Match
from settlement.domain.models import Party, SettlementMethod, Side, StatementLine, Trade
from settlement.domain.tolerance import ToleranceRule, ToleranceSet, evaluate

LEI_BANK = LEI("HWUPKR0MPOU8FGXBT394")
ISIN_SAP = ISIN("DE0007164600")


def rule(
    currency: str = "EUR",
    instrument_type: str = "*",
    absolute: str = "5.00",
    percentage: str = "0.0001",
) -> ToleranceRule:
    """A ToleranceRule from short string arguments; amounts become Decimals."""
    return ToleranceRule(currency, instrument_type, Decimal(absolute), Decimal(percentage))


FALLBACK = rule("*", "*", "1.00", "0")


def tolerance_set(*rules: ToleranceRule) -> ToleranceSet:
    """A v1 set with the given rules plus the fallback rule."""
    return ToleranceSet(version="v1", effective_from=date(2026, 10, 1), rules=(*rules, FALLBACK))


EUR_SET = tolerance_set(rule("EUR"))

BASE_TRADE = Trade(
    trade_id="T1",
    isin=ISIN_SAP,
    side=Side.BUY,
    quantity=Decimal("1000"),
    price=Decimal("150.25"),
    consideration=Decimal("150250.00"),
    currency="EUR",
    counterparty=Party(party_id="CP-001", name="Example Bank plc", lei=LEI_BANK),
    trade_date=date(2026, 10, 8),
    intended_settlement_date=date(2026, 10, 9),
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
    intended_settlement_date=date(2026, 10, 9),
    reference="T1",
)


def make_match(theirs: str = "150250.00", **line_changes: Any) -> Match:
    """BASE_TRADE matched with a line reporting `theirs` as consideration."""
    return Match(BASE_TRADE, replace(BASE_LINE, consideration=Decimal(theirs), **line_changes))


def test_valid_rules_can_be_built() -> None:
    assert rule().currency == "EUR"
    assert FALLBACK.currency == "*"
    assert rule(percentage="1").percentage == Decimal("1")  # 100% is the upper limit


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"currency": "eur"}, "Currency", id="lowercase_currency"),
        pytest.param({"currency": "EURO"}, "Currency", id="four_letter_currency"),
        pytest.param({"currency": ""}, "Currency", id="empty_currency"),
        pytest.param({"currency": None}, "Currency", id="currency_not_text"),
        pytest.param({"instrument_type": " "}, "instrument_type", id="blank_instrument_type"),
        pytest.param({"absolute": Decimal("-1")}, "absolute", id="negative_absolute"),
        pytest.param({"absolute": 5.0}, "absolute", id="float_absolute"),
        pytest.param({"absolute": Decimal("NaN")}, "absolute", id="nan_absolute"),
        pytest.param({"percentage": Decimal("-0.0001")}, "percentage", id="negative_percentage"),
        pytest.param({"percentage": Decimal("1.5")}, "Percentage", id="percentage_over_one"),
        pytest.param({"percentage": Decimal("Infinity")}, "percentage", id="infinite_percentage"),
    ],
)
def test_invalid_rule_raises(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(InvalidModel, match=message):
        replace(rule(), **changes)


@pytest.mark.parametrize(
    ("currency", "instrument_type", "expected"),
    [
        ("*", "*", True),
        ("EUR", "*", False),
        ("*", "EQUITY", False),
        ("EUR", "EQUITY", False),
    ],
)
def test_is_fallback(currency: str, instrument_type: str, expected: bool) -> None:
    assert rule(currency, instrument_type).is_fallback is expected


def test_valid_set_can_be_built() -> None:
    assert EUR_SET.version == "v1"
    assert EUR_SET.rules == (rule("EUR"), FALLBACK)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        pytest.param({"version": " "}, "version", id="blank_version"),
        pytest.param(
            {"effective_from": datetime(2026, 10, 1, 9, 0)}, "date", id="datetime_not_date"
        ),
        pytest.param({"effective_from": "2026-10-01"}, "date", id="date_as_text"),
        pytest.param({"rules": [rule(), FALLBACK]}, "tuple", id="rules_as_list"),
        pytest.param({"rules": ()}, "tuple", id="no_rules"),
        pytest.param({"rules": ({"currency": "EUR"}, FALLBACK)}, "ToleranceRule", id="raw_dict"),
        pytest.param({"rules": (rule(), rule(), FALLBACK)}, "Duplicate", id="duplicate_rule"),
        pytest.param({"rules": (rule(),)}, "fallback", id="no_fallback"),
        pytest.param({"rules": (FALLBACK, FALLBACK)}, "Duplicate", id="two_fallbacks"),
    ],
)
def test_invalid_set_raises(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(InvalidModel, match=message):
        replace(EUR_SET, **changes)


def test_same_currency_different_instrument_types_are_not_duplicates() -> None:
    rules = tolerance_set(rule("EUR", "EQUITY"), rule("EUR", "BOND"), rule("EUR", "*")).rules
    assert len(rules) == 4


FULL_SET = tolerance_set(
    rule("EUR", "EQUITY", absolute="2.00"),
    rule("EUR", "*", absolute="5.00"),
    rule("*", "EQUITY", absolute="8.00"),
)


@pytest.mark.parametrize(
    ("currency", "instrument_type", "expected"),
    [
        pytest.param("EUR", "EQUITY", ("EUR", "EQUITY"), id="exact"),
        pytest.param("EUR", "BOND", ("EUR", "*"), id="currency_only"),
        pytest.param("GBP", "EQUITY", ("*", "EQUITY"), id="instrument_only"),
        pytest.param("GBP", "BOND", ("*", "*"), id="fallback"),
    ],
)
def test_rule_for_picks_most_specific(
    currency: str, instrument_type: str, expected: tuple[str, str]
) -> None:
    chosen = FULL_SET.rule_for(currency, instrument_type)
    assert (chosen.currency, chosen.instrument_type) == expected


def test_rule_for_currency_beats_instrument_type() -> None:
    # No EUR/EQUITY rule: both EUR/* and */EQUITY could apply, and the currency rule wins.
    tolerances = tolerance_set(rule("EUR", "*"), rule("*", "EQUITY"))
    chosen = tolerances.rule_for("EUR", "EQUITY")
    assert (chosen.currency, chosen.instrument_type) == ("EUR", "*")


@pytest.mark.parametrize(
    ("theirs", "difference", "within"),
    [
        pytest.param("150250.00", "0.00", True, id="no_difference"),
        pytest.param("150253.00", "3.00", True, id="well_inside"),
        pytest.param("150265.025", "15.025", True, id="exactly_at_limit"),
        pytest.param("150265.03", "15.03", False, id="half_a_cent_over"),
        pytest.param("150240.00", "-10.00", True, id="they_report_less"),
        pytest.param("150234.97", "-15.03", False, id="they_report_less_over_limit"),
    ],
)
def test_evaluate_figures(theirs: str, difference: str, within: bool) -> None:
    result = evaluate(make_match(theirs), EUR_SET)

    assert result.difference == Decimal(difference)
    assert result.allowed == Decimal("15.025")  # percentage beats the 5.00 floor
    assert result.within is within


def test_absolute_floor_wins_for_small_trades() -> None:
    # 0.01% of 1,000.00 is 0.10, so the 5.00 absolute amount applies.
    trade = replace(BASE_TRADE, quantity=Decimal("10"), consideration=Decimal("1000.00"))
    line = replace(BASE_LINE, quantity=Decimal("10"))

    inside = evaluate(Match(trade, replace(line, consideration=Decimal("1005.00"))), EUR_SET)
    outside = evaluate(Match(trade, replace(line, consideration=Decimal("1005.01"))), EUR_SET)

    assert inside.allowed == Decimal("5.00")
    assert inside.within is True
    assert outside.within is False


def test_currency_mismatch_is_never_within() -> None:
    result = evaluate(make_match("150250.00", currency="GBP"), EUR_SET)

    assert result.difference == Decimal("0")
    assert result.allowed == Decimal("0")
    assert result.within is False


def test_unlisted_currency_uses_fallback() -> None:
    trade = replace(BASE_TRADE, currency="USD")
    line = replace(BASE_LINE, currency="USD", consideration=Decimal("150251.00"))

    result = evaluate(Match(trade, line), EUR_SET)

    assert result.rule is FALLBACK
    assert result.allowed == Decimal("1.00")  # max(1.00, 0% of anything)
    assert result.within is True


def test_fop_with_zero_consideration_is_within() -> None:
    trade = replace(
        BASE_TRADE, settlement_method=SettlementMethod.FOP, consideration=Decimal("0.00")
    )
    line = replace(BASE_LINE, consideration=Decimal("0.00"))

    assert evaluate(Match(trade, line), EUR_SET).within is True


def test_instrument_type_selects_the_specific_rule() -> None:
    result = evaluate(make_match("150253.00"), FULL_SET, instrument_type="EQUITY")

    assert (result.rule.currency, result.rule.instrument_type) == ("EUR", "EQUITY")


def test_result_records_rule_version_and_match() -> None:
    match = make_match("150253.00")

    result = evaluate(match, EUR_SET)

    assert result.tolerance_set_version == "v1"
    assert result.rule == rule("EUR")
    assert result.match is match
