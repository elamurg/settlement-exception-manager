"""Write generated data to disk: book.csv, statement.csv, statuses.txt and manifest.json.

Decimals are written as strings exactly as held, never through float, and dates as
ISO 8601. Nothing time-dependent is written, so the same seed gives identical files.
"""

import csv
import json
from pathlib import Path

from settlement.domain.attribution import CsdStatus
from settlement.generation.generator import GENERATOR_VERSION, GeneratedData

BOOK_FILE = "book.csv"
STATEMENT_FILE = "statement.csv"
STATUSES_FILE = "statuses.txt"
MANIFEST_FILE = "manifest.json"

# Ends each MT548 in statuses.txt, as "-" ends a message body in SWIFT.
MESSAGE_SEPARATOR = "-"

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
    "counterparty_bic",
    "counterparty_account",
]


def write_all(data: GeneratedData, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_book(data, out_dir / BOOK_FILE)
    write_statement(data, out_dir / STATEMENT_FILE)
    write_statuses(data, out_dir / STATUSES_FILE)
    write_manifest(data, out_dir / MANIFEST_FILE)


def write_book(data: GeneratedData, path: Path) -> None:
    with path.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(BOOK_COLUMNS)
        for trade in data.book:
            ssi = data.ssi_for(trade)  # the settlement details we hold on file
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
                    ssi.custodian_bic,
                    ssi.safekeeping_account,
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
                    line.counterparty_bic,
                    line.counterparty_account,
                ]
            )


def format_mt548(status: CsdStatus, reference: str) -> str:
    """One status as a simplified MT548, the shape `ingestion.messages.parse_mt548` reads."""
    lines = [
        ":16R:GENL",
        f":20C::SEME//{reference}",
        ":23G:INST",
        ":16R:LINK",
        f":20C::RELA//{status.instruction_reference}",
        ":16S:LINK",
        ":16R:STAT",
        f":25D::MTCH//{'MACH' if status.matched else 'NMAT'}",
    ]
    if status.reason is not None:
        lines += [":16R:REAS", f":24B::NMAT//{status.reason}", ":16S:REAS"]
    lines += [":16S:STAT", ":16S:GENL"]
    return "\n".join(lines) + "\n"


def write_statuses(data: GeneratedData, path: Path) -> None:
    """Every status as an MT548, one after another, each ended by a "-" line."""
    with path.open("w") as file:
        for number, status in enumerate(data.statuses, start=1):
            file.write(format_mt548(status, reference=f"STAT-{number:06d}"))
            file.write(MESSAGE_SEPARATOR + "\n")


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
        "counterparty_break_rate": data.config.counterparty_break_rate,
        "counts": {
            "book_rows": len(data.book),
            "statement_lines": len(data.statement),
            "statuses": len(data.statuses),
            "breaks": len(data.breaks),
            "by_type": counts,
            "counterparty_breaks": len(data.counterparty_breaks),
        },
        "breaks": [
            {
                "break_type": str(injected.break_type),
                "trade_id": injected.trade_id,
                "statement_line_id": injected.statement_line_id,
                "related_trade_ids": list(injected.related_trade_ids),
                "leg": str(injected.leg),
                "evidence": [
                    {"field": item.field, "ours": item.ours, "theirs": item.theirs}
                    for item in injected.evidence
                ],
            }
            for injected in sorted(data.breaks, key=lambda injected: injected.trade_id)
        ],
        # Leg-2 breaks: book and custodian agree, the CSD reports no match. Listed apart
        # from "breaks" so book vs statement scoring (SE-11) isn't affected.
        "counterparty_breaks": [
            {
                "break_type": str(item.break_type),
                "trade_id": item.trade_id,
                "reason": item.reason,
                "leg": str(item.leg),
            }
            for item in data.counterparty_breaks
        ],
    }


def write_manifest(data: GeneratedData, path: Path) -> None:
    path.write_text(json.dumps(manifest(data), indent=2) + "\n")
