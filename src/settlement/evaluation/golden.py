"""Load the golden break set (SE-16): a versioned exam with a known right answer per case.

Each hand-written case starts from a standard trade and only writes what differs. The
trades and lines are then built as CSV and read with the SE-08 parsers, so a case is held
to exactly the same checks as a real file.
"""

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from settlement.domain.errors import DomainError
from settlement.domain.models import BreakType, StatementLine
from settlement.generation.generator import GenerationConfig
from settlement.ingestion.book import BOOK_COLUMNS, BookEntry, parse_book
from settlement.ingestion.custodian import (
    OPTIONAL_STATEMENT_COLUMNS,
    STATEMENT_COLUMNS,
    parse_statement,
)
from settlement.ingestion.errors import ParseError

GOLDEN_V1 = Path("golden/v1/golden.yaml")

# What every trade in a case is, unless the case says otherwise. A normal, valid DVP trade.
STANDARD_TRADE: Mapping[str, str] = MappingProxyType(
    {
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
)

# Statement fields a line copies from its trade when the case doesn't say otherwise.
_COPIED_FROM_TRADE = (
    "isin",
    "side",
    "quantity",
    "consideration",
    "currency",
    "counterparty_lei",
    "intended_settlement_date",
    "counterparty_bic",
    "counterparty_account",
)

_TOP_KEYS = {"version", "generated", "cases"}
_GENERATED_KEYS = {"trades", "seed"}
_CASE_KEYS = {"id", "kind", "title", "rationale", "book", "statement", "expected"}
_EXPECTED_KEYS = {"clean", "breaks", "unresolved"}
_BREAK_KEYS = {"type", "trade"}


class GoldenSetError(DomainError):
    """The golden set file is malformed. Says which file and where in it."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")


class CaseKind(StrEnum):
    """The kinds of hard case the ticket asks for."""

    TWO_CAUSES = "TWO_CAUSES"  # two plausible causes at once
    REALLY_TWO = "REALLY_TWO"  # one break that is really two
    CORPORATE_ACTION = "CORPORATE_ACTION"  # resolved by a corporate action
    MUST_BE_CLEAN = "MUST_BE_CLEAN"  # a trap: must produce nothing


@dataclass(frozen=True, slots=True)
class ExpectedBreak:
    break_type: BreakType
    trade_id: str


@dataclass(frozen=True, slots=True)
class Expected:
    """The right answer for a case. Every trade is in exactly one of the three."""

    clean: frozenset[str]
    breaks: tuple[ExpectedBreak, ...]
    unresolved: frozenset[str]


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    kind: CaseKind
    title: str
    rationale: str
    book: tuple[BookEntry, ...]
    statement: tuple[StatementLine, ...]
    expected: Expected


@dataclass(frozen=True, slots=True)
class GoldenSet:
    version: str
    generated: GenerationConfig  # the generated part: these settings plus its manifest
    cases: tuple[GoldenCase, ...]


def load_golden_set(path: Path = GOLDEN_V1) -> GoldenSet:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise GoldenSetError(path.name, f"cannot read file: {error}") from None
    return parse_golden_set(text, source=path.name)


def parse_golden_set(text: str, source: str = "golden.yaml") -> GoldenSet:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise GoldenSetError(source, f"not valid YAML: {error}") from None
    if not isinstance(data, dict):
        raise GoldenSetError(source, "expected a mapping with version, generated and cases")
    _require_keys(data, _TOP_KEYS, set(), source, "top level")

    generated = _mapping(data["generated"], source, "generated")
    _require_keys(generated, _GENERATED_KEYS, set(), source, "generated")
    try:
        config = GenerationConfig(
            trades=_integer(generated["trades"], source, "generated.trades"),
            seed=_integer(generated["seed"], source, "generated.seed"),
        )
    except ValueError as error:
        raise GoldenSetError(source, f"generated: {error}") from None

    raw_cases = data["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise GoldenSetError(source, "cases must be a non-empty list")
    cases = tuple(
        _parse_case(raw, source, f"cases[{number}]") for number, raw in enumerate(raw_cases, 1)
    )

    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise GoldenSetError(source, f"case id {case.case_id!r} is used twice")
        seen.add(case.case_id)

    return GoldenSet(
        version=_text(data["version"], source, "version"), generated=config, cases=cases
    )


def _parse_case(raw: Any, source: str, where: str) -> GoldenCase:
    case = _mapping(raw, source, where)
    _require_keys(case, _CASE_KEYS - {"statement"}, {"statement"}, source, where)
    case_id = _text(case["id"], source, f"{where}.id")
    where = f"{where} ({case_id})"

    kind_text = _text(case["kind"], source, f"{where}.kind")
    try:
        kind = CaseKind(kind_text)
    except ValueError:
        allowed = ", ".join(kind.value for kind in CaseKind)
        raise GoldenSetError(source, f"{where}.kind must be one of {allowed}") from None

    book = _parse_book(_list(case["book"], source, f"{where}.book"), source, where)
    trades = {entry.trade.trade_id: entry for entry in book}
    statement = _parse_statement(
        _list(case.get("statement", []), source, f"{where}.statement"), trades, source, where
    )

    return GoldenCase(
        case_id=case_id,
        kind=kind,
        title=_text(case["title"], source, f"{where}.title"),
        rationale=_text(case["rationale"], source, f"{where}.rationale"),
        book=book,
        statement=statement,
        expected=_parse_expected(case["expected"], set(trades), source, where),
    )


def _parse_book(rows: list[Any], source: str, where: str) -> tuple[BookEntry, ...]:
    if not rows:
        raise GoldenSetError(source, f"{where}.book needs at least one trade")
    full_rows = []
    for number, raw in enumerate(rows, 1):
        here = f"{where}.book[{number}]"
        row = _mapping(raw, source, here)
        _require_keys(row, {"trade_id"}, set(BOOK_COLUMNS) - {"trade_id"}, source, here)
        full_rows.append({**STANDARD_TRADE, **_texts(row, source, here)})
    return _read(parse_book, BOOK_COLUMNS, full_rows, source, f"{where}.book")


def _parse_statement(
    rows: list[Any], trades: Mapping[str, BookEntry], source: str, where: str
) -> tuple[StatementLine, ...]:
    columns = STATEMENT_COLUMNS + OPTIONAL_STATEMENT_COLUMNS
    full_rows = []
    for number, raw in enumerate(rows, 1):
        here = f"{where}.statement[{number}]"
        row = _texts(_mapping(raw, source, here), source, here)
        _require_keys(row, {"reference"}, set(columns) - {"reference"}, source, here)
        # A line starts as a perfect match for its trade (or the standard trade, for a
        # line whose trade isn't in the book).
        entry = trades.get(row["reference"])
        base = dict(STANDARD_TRADE) if entry is None else _as_row(entry)
        line = {name: base[name] for name in _COPIED_FROM_TRADE}
        line |= {"line_id": f"L-{number}", "source": "custodian"}
        full_rows.append(line | row)
    return _read(parse_statement, columns, full_rows, source, f"{where}.statement")


def _parse_expected(raw: Any, trade_ids: set[str], source: str, where: str) -> Expected:
    here = f"{where}.expected"
    expected = _mapping(raw, source, here)
    _require_keys(expected, set(), _EXPECTED_KEYS, source, here)

    clean = _trade_ids(expected.get("clean", []), source, f"{here}.clean")
    unresolved = _trade_ids(expected.get("unresolved", []), source, f"{here}.unresolved")
    breaks = []
    for number, raw_break in enumerate(_list(expected.get("breaks", []), source, here), 1):
        at = f"{here}.breaks[{number}]"
        item = _mapping(raw_break, source, at)
        _require_keys(item, _BREAK_KEYS, set(), source, at)
        type_text = _text(item["type"], source, f"{at}.type")
        try:
            break_type = BreakType(type_text)
        except ValueError:
            raise GoldenSetError(source, f"{at}.type {type_text!r} isn't a break type") from None
        breaks.append(ExpectedBreak(break_type, _text(item["trade"], source, f"{at}.trade")))

    # Every trade in exactly one outcome: a case can't forget a trade or answer twice.
    broken = {item.trade_id for item in breaks}
    for trade_id in sorted(clean | unresolved | broken):
        if trade_id not in trade_ids:
            raise GoldenSetError(source, f"{here} mentions {trade_id!r}, which isn't in the book")
    for trade_id in sorted(trade_ids):
        outcomes = [name for name, ids in [("clean", clean), ("unresolved", unresolved),
                                           ("breaks", broken)] if trade_id in ids]  # fmt: skip
        if len(outcomes) != 1:
            found = " and ".join(outcomes) or "none of clean, breaks, unresolved"
            raise GoldenSetError(
                source, f"{here}: {trade_id!r} must be in exactly one outcome, found {found}"
            )

    return Expected(clean=frozenset(clean), breaks=tuple(breaks), unresolved=frozenset(unresolved))


def _read[T](
    parse: Any, columns: list[str], rows: list[dict[str, str]], source: str, where: str
) -> tuple[T, ...]:
    """Build the rows as CSV and read them with the SE-08 parser, so the same checks apply."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, restval="")
    writer.writeheader()
    writer.writerows(rows)
    try:
        parsed: tuple[T, ...] = parse(buffer.getvalue().splitlines(), source=f"{source} {where}")
    except ParseError as error:
        # Line 2 of the CSV is row 1 of the case.
        row = f"[{error.line - 1}]" if error.line > 1 else ""
        field = f".{error.field}" if error.field else ""
        # The parser's text is "<where> line N, field 'x': <message>"; keep only the message.
        message = str(error).split(": ", 1)[1]
        raise GoldenSetError(source, f"{where}{row}{field}: {message}") from None
    return parsed


def _as_row(entry: BookEntry) -> dict[str, str]:
    trade = entry.trade
    return {
        "isin": str(trade.isin),
        "side": str(trade.side),
        "quantity": str(trade.quantity),
        "consideration": str(trade.consideration),
        "currency": trade.currency,
        "counterparty_lei": str(trade.counterparty.lei),
        "intended_settlement_date": trade.intended_settlement_date.isoformat(),
        "counterparty_bic": entry.counterparty_bic,
        "counterparty_account": entry.counterparty_account,
    }


def _require_keys(
    data: Mapping[Any, Any], required: set[str], optional: set[str], source: str, where: str
) -> None:
    missing = sorted(required - data.keys())
    unknown = sorted(str(key) for key in data.keys() - required - optional)
    if missing:
        raise GoldenSetError(source, f"{where}: missing {', '.join(missing)}")
    if unknown:
        raise GoldenSetError(source, f"{where}: unknown {', '.join(unknown)}")


def _mapping(value: Any, source: str, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GoldenSetError(source, f"{where} must be a mapping")
    return value


def _list(value: Any, source: str, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise GoldenSetError(source, f"{where} must be a list")
    return value


def _text(value: Any, source: str, where: str) -> str:
    if not isinstance(value, str):
        raise GoldenSetError(source, f"{where} must be a quoted string, got {value!r}")
    if not value.strip():
        raise GoldenSetError(source, f"{where} is empty")
    return value.strip()


def _texts(row: Mapping[str, Any], source: str, where: str) -> dict[str, str]:
    return {key: _text(value, source, f"{where}.{key}") for key, value in row.items()}


def _integer(value: Any, source: str, where: str) -> int:
    text = _text(value, source, where)
    if not text.isdigit():
        raise GoldenSetError(source, f"{where} must be a whole number, got {text!r}")
    return int(text)


def _trade_ids(value: Any, source: str, where: str) -> set[str]:
    ids = [
        _text(item, source, f"{where}[{n}]")
        for n, item in enumerate(_list(value, source, where), 1)
    ]
    if len(ids) != len(set(ids)):
        raise GoldenSetError(source, f"{where} lists a trade twice")
    return set(ids)
