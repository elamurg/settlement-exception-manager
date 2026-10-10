"""Tests for the book and custodian statement parsers (SE-08).

Round trip: data from the SE-07 generator, written to CSV, must parse back to exactly
the same domain objects. Then every kind of malformed input must raise a ParseError that
names the line and, where there is one, the field.
"""

from pathlib import Path

import pytest

from settlement.domain.errors import DomainError
from settlement.generation import files
from settlement.generation.generator import GeneratedData, GenerationConfig, generate, scaled_rates
from settlement.ingestion.book import BOOK_COLUMNS, load_book, parse_book
from settlement.ingestion.custodian import (
    OPTIONAL_STATEMENT_COLUMNS,
    STATEMENT_COLUMNS,
    load_statement,
    parse_statement,
)
from settlement.ingestion.errors import ParseError

BOOK_ROW = {
    "trade_id": "T-000001",
    "isin": "DE0007164600",
    "side": "BUY",
    "quantity": "1000",
    "price": "150.25",
    "consideration": "150250.00",
    "currency": "EUR",
    "counterparty_id": "CP-001",
    "counterparty_name": "Northgate Securities",
    "counterparty_lei": "HWUPKR0MPOU8FGXBT394",
    "counterparty_bic": "DEUTDEFF",
    "counterparty_account": "0012345678",
    "trade_date": "2026-10-05",
    "intended_settlement_date": "2026-10-06",
    "settlement_method": "DVP",
    "status": "INSTRUCTED",
}

STATEMENT_ROW = {
    "line_id": "L-000001",
    "source": "custodian",
    "isin": "DE0007164600",
    "side": "BUY",
    "quantity": "1000",
    "consideration": "150250.00",
    "currency": "EUR",
    "counterparty_lei": "HWUPKR0MPOU8FGXBT394",
    "intended_settlement_date": "2026-10-06",
    "reference": "T-000001",
    "counterparty_bic": "DEUTDEFF",
    "counterparty_account": "0012345678",
}


def csv_lines(rows: list[dict[str, str]]) -> list[str]:
    """A header from the first row's keys, then one line per row."""
    header = list(rows[0])
    return [",".join(header)] + [",".join(row[name] for name in header) for row in rows]


def book_with_bad_second_row(**changes: str) -> list[str]:
    """Two trades; the second (on line 3) has `changes` applied."""
    second = {**BOOK_ROW, "trade_id": "T-000002", **changes}
    return csv_lines([BOOK_ROW, second])


def statement_with_bad_second_row(**changes: str) -> list[str]:
    second = {**STATEMENT_ROW, "line_id": "L-000002", **changes}
    return csv_lines([STATEMENT_ROW, second])


# Round trip with the generator


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, GeneratedData]:
    data = generate(GenerationConfig(trades=1000, seed=11, break_rates=scaled_rates(0.5)))
    out = tmp_path_factory.mktemp("generated")
    files.write_all(data, out)
    return out, data


def test_book_round_trips(generated: tuple[Path, GeneratedData]) -> None:
    out, data = generated

    entries = load_book(out / files.BOOK_FILE)

    assert tuple(entry.trade for entry in entries) == data.book
    for entry in entries:
        ssi = data.ssi_for(entry.trade)
        assert (entry.counterparty_bic, entry.counterparty_account) == (
            ssi.custodian_bic,
            ssi.safekeeping_account,
        )


def test_statement_round_trips(generated: tuple[Path, GeneratedData]) -> None:
    out, data = generated

    assert load_statement(out / files.STATEMENT_FILE) == data.statement


def test_columns_match_what_the_generator_writes() -> None:
    assert BOOK_COLUMNS == files.BOOK_COLUMNS
    assert STATEMENT_COLUMNS + OPTIONAL_STATEMENT_COLUMNS == files.STATEMENT_COLUMNS


# Valid input


def test_valid_rows_parse() -> None:
    (entry,) = parse_book(csv_lines([BOOK_ROW]))
    (line,) = parse_statement(csv_lines([STATEMENT_ROW]))

    assert entry.trade.trade_id == "T-000001"
    assert entry.counterparty_bic == "DEUTDEFF"
    assert line.counterparty_account == "0012345678"


def test_spaces_and_lowercase_codes_are_normalised() -> None:
    row = {**BOOK_ROW, "isin": " de0007164600 ", "side": "buy", "currency": "eur "}

    (entry,) = parse_book(csv_lines([row]))

    assert str(entry.trade.isin) == "DE0007164600"
    assert entry.trade.side == "BUY"
    assert entry.trade.currency == "EUR"


def test_statement_settlement_details_are_optional() -> None:
    without = {k: v for k, v in STATEMENT_ROW.items() if k not in OPTIONAL_STATEMENT_COLUMNS}
    empty = {**STATEMENT_ROW, "counterparty_bic": "", "counterparty_account": ""}

    for row in (without, empty):
        (line,) = parse_statement(csv_lines([row]))
        assert (line.counterparty_bic, line.counterparty_account) == (None, None)


def test_blank_lines_are_ignored() -> None:
    lines = csv_lines([BOOK_ROW])

    assert len(parse_book([lines[0], "", lines[1], ""])) == 1


def test_header_only_gives_no_rows() -> None:
    assert parse_book([",".join(BOOK_COLUMNS)]) == ()


# Malformed book rows: each must name line 3 and the field


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"quantity": "12.5x"}, "quantity", "not a number"),
        ({"quantity": "NaN"}, "quantity", "not a finite number"),
        ({"price": ""}, "price", "is empty"),
        ({"trade_date": "05/10/2026"}, "trade_date", "not a date"),
        ({"isin": "DE0007164601"}, "isin", "check digit"),
        ({"isin": "DE000716460"}, "isin", "format"),
        ({"counterparty_lei": "HWUPKR0MPOU8FGXBT395"}, "counterparty_lei", "check digits"),
        ({"side": "HOLD"}, "side", "must be one of BUY, SELL"),
        ({"settlement_method": "CASH"}, "settlement_method", "must be one of DVP, FOP"),
        ({"status": "DONE"}, "status", "must be one of"),
        ({"trade_id": "  "}, "trade_id", "is empty"),
        ({"counterparty_bic": "DEUTDE"}, "counterparty_bic", "not a valid BIC"),
        ({"counterparty_account": ""}, "counterparty_account", "is empty"),
        ({"trade_id": "T-000001"}, "trade_id", "already appears on line 2"),
    ],
)
def test_malformed_book_field(changes: dict[str, str], field: str, message: str) -> None:
    with pytest.raises(ParseError) as caught:
        parse_book(book_with_bad_second_row(**changes))

    assert caught.value.line == 3
    assert caught.value.field == field
    assert message in str(caught.value)
    assert str(caught.value).startswith(f"book.csv line 3, field {field!r}: ")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quantity": "0"}, "quantity must be positive"),
        ({"intended_settlement_date": "2026-10-01"}, "before trade date"),
        ({"consideration": "0"}, "consideration must be positive"),  # DVP needs consideration
        ({"currency": "EURO"}, "currency"),
    ],
)
def test_book_rows_breaking_model_rules_name_the_line(
    changes: dict[str, str], message: str
) -> None:
    with pytest.raises(ParseError, match=f"^book.csv line 3: .*{message}"):
        parse_book(book_with_bad_second_row(**changes))


# Malformed statement rows


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"quantity": "lots"}, "quantity", "not a number"),
        ({"intended_settlement_date": "tomorrow"}, "intended_settlement_date", "not a date"),
        ({"counterparty_lei": "SHORT"}, "counterparty_lei", "format"),
        ({"side": "B"}, "side", "must be one of"),
        ({"reference": ""}, "reference", "is empty"),
        ({"line_id": "L-000001"}, "line_id", "already appears on line 2"),
    ],
)
def test_malformed_statement_field(changes: dict[str, str], field: str, message: str) -> None:
    with pytest.raises(ParseError) as caught:
        parse_statement(statement_with_bad_second_row(**changes))

    assert (caught.value.line, caught.value.field) == (3, field)
    assert message in str(caught.value)


def test_statement_bic_must_be_valid() -> None:
    with pytest.raises(ParseError, match="^statement.csv line 3: .*BIC"):
        parse_statement(statement_with_bad_second_row(counterparty_bic="NOTABIC"))


# File shape


def test_empty_file() -> None:
    with pytest.raises(ParseError, match="line 1: file is empty"):
        parse_book([])


def test_missing_column() -> None:
    row = {k: v for k, v in BOOK_ROW.items() if k != "price"}

    with pytest.raises(ParseError, match="line 1: missing columns: price"):
        parse_book(csv_lines([row]))


def test_unknown_column_is_rejected_not_ignored() -> None:
    # A misspelt optional column would otherwise be silently dropped.
    row = {**STATEMENT_ROW, "counterparty_acount": "0012345678"}

    with pytest.raises(ParseError, match="line 1: unknown columns: counterparty_acount"):
        parse_statement(csv_lines([row]))


def test_duplicated_column() -> None:
    lines = csv_lines([BOOK_ROW])
    lines = [lines[0] + ",price", lines[1] + ",1.00"]

    with pytest.raises(ParseError, match="line 1: duplicated columns: price"):
        parse_book(lines)


@pytest.mark.parametrize("extra", [",unexpected", ""])
def test_row_with_wrong_number_of_fields(extra: str) -> None:
    header, row = csv_lines([BOOK_ROW])
    row = row + extra if extra else row.rsplit(",", 1)[0]

    with pytest.raises(ParseError, match="line 2: expected 16 fields, got 1[57]"):
        parse_book([header, row])


def test_a_bad_row_is_never_skipped() -> None:
    good, bad = BOOK_ROW, {**BOOK_ROW, "trade_id": "T-000002", "quantity": "oops"}
    third = {**BOOK_ROW, "trade_id": "T-000003"}

    with pytest.raises(ParseError):
        parse_book(csv_lines([good, bad, third]))


def test_quoted_fields_and_line_numbers(tmp_path: Path) -> None:
    # A name with a comma is quoted; it must not shift the columns.
    row = {**BOOK_ROW, "counterparty_name": '"Northgate, Securities"'}
    path = tmp_path / "trades.csv"
    path.write_text("\n".join(csv_lines([BOOK_ROW, {**row, "trade_id": "T-2", "price": "x"}])))

    with pytest.raises(ParseError, match=r"^trades.csv line 3, field 'price'"):
        load_book(path)


def test_parse_error_is_a_domain_error() -> None:
    with pytest.raises(DomainError):
        parse_book([])
