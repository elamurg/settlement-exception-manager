"""Write generated data to disk: book.csv, statement.csv and manifest.json.

Decimals are written as strings exactly as held, never through float, and dates as
ISO 8601. Nothing time-dependent is written, so the same seed gives identical files.
"""

import csv
import json
from pathlib import Path

from settlement.generation.generator import GENERATOR_VERSION, GeneratedData

BOOK_FILE = "book.csv"
STATEMENT_FILE = "statement.csv"
MANIFEST_FILE = "manifest.json"

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
    "trade_date",
    "intended_settlement_date",
    "settlement_method",
    "status",
]

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


def write_all(data: GeneratedData, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_book(data, out_dir / BOOK_FILE)
    write_statement(data, out_dir / STATEMENT_FILE)
    write_manifest(data, out_dir / MANIFEST_FILE)


def write_book(data: GeneratedData, path: Path) -> None:
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(BOOK_COLUMNS)
        for trade in data.book:
            writer.writerow(
                [
                    trade.trade_id,
                    trade.isin,
                    trade.side,
                    trade.quantity,
                    trade.price,
                    trade.consideration,
                    trade.currency,
                    trade.counterparty.party_id,
                    trade.counterparty.name,
                    trade.counterparty.lei,
                    trade.trade_date.isoformat(),
                    trade.intended_settlement_date.isoformat(),
                    trade.settlement_method,
                    trade.status,
                ]
            )


def write_statement(data: GeneratedData, path: Path) -> None:
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(STATEMENT_COLUMNS)
        for line in data.statement:
            writer.writerow(
                [
                    line.line_id,
                    line.source,
                    line.isin,
                    line.side,
                    line.quantity,
                    line.consideration,
                    line.currency,
                    line.counterparty_lei,
                    line.intended_settlement_date.isoformat(),
                    line.reference,
                ]
            )


def manifest(data: GeneratedData) -> dict[str, object]:
    counts = {str(kind): 0 for kind in data.config.break_rates}
    for injected in data.breaks:
        counts[str(injected.break_type)] += 1

    return {
        "generator_version": GENERATOR_VERSION,
        "seed": data.config.seed,
        "trades": data.config.trades,
        "trade_date": data.config.trade_date.isoformat(),
        "break_rates": {str(kind): rate for kind, rate in data.config.break_rates.items()},
        "counts": {
            "book_rows": len(data.book),
            "statement_lines": len(data.statement),
            "breaks": len(data.breaks),
            "by_type": counts,
        },
        "breaks": [
            {
                "break_type": str(injected.break_type),
                "trade_id": injected.trade_id,
                "statement_line_id": injected.statement_line_id,
                "related_trade_ids": list(injected.related_trade_ids),
                "evidence": [
                    {"field": item.field, "ours": item.ours, "theirs": item.theirs}
                    for item in injected.evidence
                ],
            }
            for injected in sorted(data.breaks, key=lambda injected: injected.trade_id)
        ],
    }


def write_manifest(data: GeneratedData, path: Path) -> None:
    path.write_text(json.dumps(manifest(data), indent=2) + "\n")
