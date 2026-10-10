"""Parse the internal trade book (book.csv)."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from settlement.domain.models import (
    BIC_PATTERN,
    Party,
    SettlementMethod,
    Side,
    Trade,
    TradeStatus,
)
from settlement.ingestion.csvfile import Row, read_rows, require_unique

BOOK_COLUMNS = [
    "trade_id",
    "isin",
    "side",
    "quantity",
    "price",
    "consideration",
    "currency",
    "counterparty_id",
    "counterparty_name",
    "counterparty_lei",
    "counterparty_bic",
    "counterparty_account",
    "trade_date",
    "intended_settlement_date",
    "settlement_method",
    "status",
]


@dataclass(frozen=True, slots=True)
class BookEntry:
    """A trade, plus the counterparty settlement details (SSI) we hold on file for it."""

    trade: Trade
    counterparty_bic: str
    counterparty_account: str


def parse_book(lines: Iterable[str], source: str = "book.csv") -> tuple[BookEntry, ...]:
    entries = []
    seen: dict[str, int] = {}
    for row in read_rows(lines, source, BOOK_COLUMNS):
        entry = _parse_entry(row)
        require_unique(row, "trade_id", entry.trade.trade_id, seen)
        entries.append(entry)
    return tuple(entries)


def load_book(path: Path) -> tuple[BookEntry, ...]:
    with path.open(newline="") as file:
        return parse_book(file, source=path.name)


def _parse_entry(row: Row) -> BookEntry:
    counterparty = row.build(
        lambda: Party(
            party_id=row.text("counterparty_id"),
            name=row.text("counterparty_name"),
            lei=row.lei("counterparty_lei"),
        )
    )
    trade = row.build(
        lambda: Trade(
            trade_id=row.text("trade_id"),
            isin=row.isin("isin"),
            side=row.choice("side", Side),
            quantity=row.decimal("quantity"),
            price=row.decimal("price"),
            consideration=row.decimal("consideration"),
            currency=row.code("currency"),
            counterparty=counterparty,
            trade_date=row.date("trade_date"),
            intended_settlement_date=row.date("intended_settlement_date"),
            settlement_method=row.choice("settlement_method", SettlementMethod),
            status=row.choice("status", TradeStatus),
        )
    )
    bic = row.code("counterparty_bic")
    if not BIC_PATTERN.fullmatch(bic):
        raise row.error(f"is not a valid BIC: {bic!r}", "counterparty_bic")
    return BookEntry(
        trade=trade, counterparty_bic=bic, counterparty_account=row.text("counterparty_account")
    )
