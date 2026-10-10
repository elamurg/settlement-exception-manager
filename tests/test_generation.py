"""Tests for the synthetic data generator (SE-07).

The key test re-derives every difference between the book and the statement from
scratch and checks the manifest describes exactly those, no more and no fewer.
"""

import csv
import json
import random
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import (
    BreakType,
    FieldDifference,
    SettlementMethod,
    StatementLine,
    Trade,
)
from settlement.generation.__main__ import main
from settlement.generation.files import BOOK_COLUMNS, STATEMENT_COLUMNS
from settlement.generation.generator import (
    DEFAULT_BREAK_RATES,
    INJECTABLE,
    GeneratedData,
    GenerationConfig,
    generate,
    next_business_day,
    scaled_rates,
)
from settlement.generation.universe import make_isin, make_lei

# A high break rate so every type turns up many times in a small run.
BUSY = GenerationConfig(trades=2000, seed=7, break_rates=scaled_rates(0.6))


@pytest.fixture(scope="module")
def busy() -> GeneratedData:
    return generate(BUSY)


def observed_differences(trade: Trade, line: StatementLine | None) -> tuple[FieldDifference, ...]:
    """Compare a trade with the custodian's line for it, field by field."""
    if line is None:
        return (FieldDifference("statement_line", ours="present", theirs="missing"),)
    pairs = [
        ("isin", str(trade.isin), str(line.isin)),
        ("side", str(trade.side), str(line.side)),
        ("quantity", str(trade.quantity), str(line.quantity)),
        ("consideration", str(trade.consideration), str(line.consideration)),
        ("currency", trade.currency, line.currency),
        ("counterparty_lei", str(trade.counterparty.lei), str(line.counterparty_lei)),
        (
            "intended_settlement_date",
            trade.intended_settlement_date.isoformat(),
            line.intended_settlement_date.isoformat(),
        ),
    ]
    return tuple(
        FieldDifference(name, ours, theirs) for name, ours, theirs in pairs if ours != theirs
    )


# Ground truth


def test_manifest_describes_exactly_the_differences_in_the_data(busy: GeneratedData) -> None:
    lines = {line.reference: line for line in busy.statement}
    duplicate_ids = {extra for item in busy.breaks for extra in item.related_trade_ids}
    injected = {item.trade_id: item for item in busy.breaks}

    for trade in busy.book:
        if trade.trade_id in duplicate_ids:
            continue
        differences = observed_differences(trade, lines.get(trade.trade_id))
        item = injected.get(trade.trade_id)

        if item is None:
            assert differences == (), f"{trade.trade_id} differs but isn't in the manifest"
        elif item.break_type == BreakType.DUPLICATE_BOOKING:
            assert differences == ()  # the original matches; the problem is the second copy
        else:
            assert differences == item.evidence, trade.trade_id


def test_each_break_changes_the_field_its_type_says(busy: GeneratedData) -> None:
    expected_field = {
        BreakType.STATIC_DATA: "counterparty_lei",
        BreakType.QUANTITY_BREAK: "quantity",
        BreakType.PRICE_BREAK: "consideration",
        BreakType.DATE_MISMATCH: "intended_settlement_date",
        BreakType.UNMATCHED_INSTRUCTION: "statement_line",
        BreakType.DUPLICATE_BOOKING: "booking_count",
    }
    for item in busy.breaks:
        assert [difference.field for difference in item.evidence] == [
            expected_field[item.break_type]
        ]


def test_every_injectable_type_appears(busy: GeneratedData) -> None:
    assert {item.break_type for item in busy.breaks} == set(INJECTABLE)


def test_duplicate_is_the_same_trade_booked_twice(busy: GeneratedData) -> None:
    trades = {trade.trade_id: trade for trade in busy.book}
    references = {line.reference for line in busy.statement}
    duplicates = [item for item in busy.breaks if item.break_type == BreakType.DUPLICATE_BOOKING]

    for item in duplicates:
        (extra_id,) = item.related_trade_ids
        original, extra = trades[item.trade_id], trades[extra_id]
        assert replace(extra, trade_id=original.trade_id) == original
        assert extra_id not in references  # the custodian only knows about one


def test_unmatched_trades_have_no_statement_line(busy: GeneratedData) -> None:
    references = {line.reference for line in busy.statement}
    for item in busy.breaks:
        if item.break_type == BreakType.UNMATCHED_INSTRUCTION:
            assert item.trade_id not in references
            assert item.statement_line_id is None


def test_manifest_points_at_the_right_statement_line(busy: GeneratedData) -> None:
    lines = {line.line_id: line for line in busy.statement}
    for item in busy.breaks:
        if item.statement_line_id is not None:
            assert lines[item.statement_line_id].reference == item.trade_id


def test_row_counts_add_up(busy: GeneratedData) -> None:
    count = {kind: 0 for kind in INJECTABLE}
    for item in busy.breaks:
        count[item.break_type] += 1

    assert len(busy.book) == BUSY.trades + count[BreakType.DUPLICATE_BOOKING]
    assert len(busy.statement) == BUSY.trades - count[BreakType.UNMATCHED_INSTRUCTION]
    assert len({trade.trade_id for trade in busy.book}) == len(busy.book)
    assert len({line.line_id for line in busy.statement}) == len(busy.statement)


# Realistic data


def test_breaks_are_believable(busy: GeneratedData) -> None:
    for item in busy.breaks:
        (difference,) = item.evidence
        if item.break_type == BreakType.QUANTITY_BREAK:
            ours, theirs = Decimal(difference.ours), Decimal(difference.theirs)
            assert 0 < theirs < ours
            assert ours - theirs <= max(Decimal(100), ours / 10)
        if item.break_type == BreakType.PRICE_BREAK:
            ours, theirs = Decimal(difference.ours), Decimal(difference.theirs)
            assert Decimal("0.01") <= abs(ours - theirs) <= ours * Decimal("0.02")


def test_amounts_are_decimals_with_cents(busy: GeneratedData) -> None:
    for trade in busy.book:
        assert isinstance(trade.quantity, Decimal)
        assert trade.price.as_tuple().exponent == -2
        assert trade.consideration.as_tuple().exponent == -2
    for line in busy.statement:
        assert line.consideration.as_tuple().exponent == -2


def test_consideration_is_price_times_quantity_for_dvp(busy: GeneratedData) -> None:
    for trade in busy.book:
        if trade.settlement_method == SettlementMethod.DVP:
            assert trade.consideration == (trade.price * trade.quantity).quantize(Decimal("0.01"))
        else:
            assert trade.consideration == 0


def test_some_trades_are_free_of_payment(busy: GeneratedData) -> None:
    assert any(trade.settlement_method == SettlementMethod.FOP for trade in busy.book)


def test_settlement_is_the_next_business_day() -> None:
    assert next_business_day(date(2026, 10, 5)) == date(2026, 10, 6)  # Mon -> Tue
    assert next_business_day(date(2026, 10, 9)) == date(2026, 10, 12)  # Fri -> Mon
    assert next_business_day(date(2026, 10, 10)) == date(2026, 10, 12)  # Sat -> Mon

    friday = generate(GenerationConfig(trades=10, seed=1, trade_date=date(2026, 10, 9)))
    assert {trade.intended_settlement_date for trade in friday.book} == {date(2026, 10, 12)}


@pytest.mark.parametrize("seed", range(20))
def test_generated_identifiers_are_valid(seed: int) -> None:
    rng = random.Random(seed)
    # Constructing them runs the check-digit validation; this asserts it doesn't raise.
    assert isinstance(make_isin(rng, "GB"), ISIN)
    assert isinstance(make_lei(rng), LEI)


# Reproducibility and configuration


def test_same_seed_gives_the_same_data() -> None:
    config = GenerationConfig(trades=300, seed=42)

    assert generate(config) == generate(config)


def test_different_seeds_give_different_data() -> None:
    assert generate(GenerationConfig(trades=300, seed=1)) != generate(
        GenerationConfig(trades=300, seed=2)
    )


def test_zero_break_rate_gives_a_clean_statement() -> None:
    clean = generate(GenerationConfig(trades=500, seed=3, break_rates={}))
    lines = {line.reference: line for line in clean.statement}

    assert clean.breaks == ()
    assert len(clean.book) == len(clean.statement) == 500
    assert all(observed_differences(trade, lines[trade.trade_id]) == () for trade in clean.book)


@pytest.mark.parametrize("break_type", INJECTABLE)
def test_a_rate_of_one_breaks_every_trade(break_type: BreakType) -> None:
    data = generate(GenerationConfig(trades=50, seed=4, break_rates={break_type: 1.0}))

    assert len(data.breaks) == 50
    assert {item.break_type for item in data.breaks} == {break_type}


def test_default_rates_are_close_to_ten_percent() -> None:
    data = generate(GenerationConfig(trades=5000, seed=42))

    assert sum(DEFAULT_BREAK_RATES.values()) == pytest.approx(0.10)
    assert 400 <= len(data.breaks) <= 600


def test_scaled_rates_keep_the_mix() -> None:
    rates = scaled_rates(0.2)

    assert sum(rates.values()) == pytest.approx(0.2)
    assert rates[BreakType.STATIC_DATA] == pytest.approx(
        2 * DEFAULT_BREAK_RATES[BreakType.STATIC_DATA]
    )


@pytest.mark.parametrize(
    "rates",
    [
        {BreakType.SSI_MISMATCH: 0.1},  # needs SSI data the files don't carry
        {BreakType.STATIC_DATA: -0.1},
        {BreakType.STATIC_DATA: 0.6, BreakType.PRICE_BREAK: 0.6},
    ],
)
def test_invalid_rates_are_rejected(rates: dict[BreakType, float]) -> None:
    with pytest.raises(ValueError):
        GenerationConfig(trades=10, seed=1, break_rates=rates)


def test_at_least_one_trade() -> None:
    with pytest.raises(ValueError):
        GenerationConfig(trades=0, seed=1)


# Files and command line


def test_command_line_writes_three_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--trades", "200", "--seed", "42", "--out", str(tmp_path)])

    with (tmp_path / "book.csv").open() as file:
        book = list(csv.DictReader(file))
    with (tmp_path / "statement.csv").open() as file:
        statement = list(csv.DictReader(file))
    written = json.loads((tmp_path / "manifest.json").read_text())

    assert list(book[0]) == BOOK_COLUMNS
    assert list(statement[0]) == STATEMENT_COLUMNS
    assert written["seed"] == 42
    assert written["counts"]["book_rows"] == len(book)
    assert written["counts"]["statement_lines"] == len(statement)
    assert len(written["breaks"]) == written["counts"]["breaks"]
    assert "Wrote" in capsys.readouterr().out


def test_files_are_identical_for_the_same_seed(tmp_path: Path) -> None:
    for run in ("first", "second"):
        main(["--trades", "300", "--seed", "9", "--out", str(tmp_path / run)])

    for name in ("book.csv", "statement.csv", "manifest.json"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()


def test_command_line_rejects_bad_rates(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--trades", "10", "--seed", "1", "--break-rate", "1.5"])

    assert "at most 1" in capsys.readouterr().err
