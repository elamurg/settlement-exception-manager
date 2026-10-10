"""Parse a custodian statement (statement.csv)."""

from collections.abc import Iterable
from pathlib import Path

from settlement.domain.models import Side, StatementLine
from settlement.ingestion.csvfile import Row, read_rows, require_unique

STATEMENT_COLUMNS = [
    "line_id",
    "source",
    "isin",
    "side",
    "quantity",
    "consideration",
    "currency",
    "counterparty_lei",
    "intended_settlement_date",
    "reference",
]

# Not every custodian reports the settlement details it used.
OPTIONAL_STATEMENT_COLUMNS = ["counterparty_bic", "counterparty_account"]


def parse_statement(
    lines: Iterable[str], source: str = "statement.csv"
) -> tuple[StatementLine, ...]:
    statement = []
    seen: dict[str, int] = {}
    for row in read_rows(lines, source, STATEMENT_COLUMNS, OPTIONAL_STATEMENT_COLUMNS):
        line = _parse_line(row)
        require_unique(row, "line_id", line.line_id, seen)
        statement.append(line)
    return tuple(statement)


def load_statement(path: Path) -> tuple[StatementLine, ...]:
    with path.open(newline="") as file:
        return parse_statement(file, source=path.name)


def _parse_line(row: Row) -> StatementLine:
    bic = row.optional_text("counterparty_bic")
    return row.build(
        lambda: StatementLine(
            line_id=row.text("line_id"),
            source=row.text("source"),
            isin=row.isin("isin"),
            side=row.choice("side", Side),
            quantity=row.decimal("quantity"),
            consideration=row.decimal("consideration"),
            currency=row.code("currency"),
            counterparty_lei=row.lei("counterparty_lei"),
            intended_settlement_date=row.date("intended_settlement_date"),
            reference=row.text("reference"),
            counterparty_bic=bic.upper() if bic else None,
            counterparty_account=row.optional_text("counterparty_account"),
        )
    )
