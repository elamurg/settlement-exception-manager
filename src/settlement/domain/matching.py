"""Matches the economic keys in the trades book and the custodian statement."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from settlement.domain.errors import InvalidMatchInput
from settlement.domain.identifiers import ISIN, LEI
from settlement.domain.models import Side, StatementLine, Trade


# frozen dataclass is hashable
@dataclass(frozen=True, slots=True)
class EconomicKey:
    isin: ISIN
    side: Side
    quantity: Decimal
    intended_settlement_date: date
    counterparty_lei: LEI


@dataclass(frozen=True, slots=True)
class Match:
    trade: Trade
    line: StatementLine


@dataclass(frozen=True, slots=True)
class AmbiguousGroup:
    key: EconomicKey
    trades: tuple[Trade, ...]
    lines: tuple[StatementLine, ...]


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: tuple[Match, ...]
    unmatched_trades: tuple[Trade, ...]
    unmatched_lines: tuple[StatementLine, ...]
    ambiguous: tuple[AmbiguousGroup, ...]


def key_of_trade(trade: Trade) -> EconomicKey:
    return EconomicKey(
        trade.isin,
        trade.side,
        trade.quantity,
        trade.intended_settlement_date,
        trade.counterparty.lei,
    )


def key_of_line(line: StatementLine) -> EconomicKey:
    return EconomicKey(
        line.isin,
        line.side,
        line.quantity,
        line.intended_settlement_date,
        line.counterparty_lei,
    )


# Sequence is an ordered collecntion of elements from the model that loops
# and counts with len() and index with [0]
def match_exact(book: Sequence[Trade], statement: Sequence[StatementLine]) -> MatchResult:
    """Pair book trades with statement lines that share an economic key.
    Only a clean one-to-one is a match, anything else is unmatched or ambiguous,
    never guessed."""
    seen_trade_ids: set[str] = set()
    for trade in book:
        if trade.trade_id in seen_trade_ids:
            raise InvalidMatchInput(f"Duplicate trade_id in book: {trade.trade_id!r}")
        seen_trade_ids.add(trade.trade_id)
    seen_line_ids: set[str] = set()
    for line in statement:
        if line.line_id in seen_line_ids:
            raise InvalidMatchInput(f"Duplicate line_id in statement: {line.line_id!r}")
        seen_line_ids.add(line.line_id)

    trades_by_key: defaultdict[EconomicKey, list[Trade]] = defaultdict(list)
    for trade in book:
        trades_by_key[key_of_trade(trade)].append(trade)

    lines_by_key: defaultdict[EconomicKey, list[StatementLine]] = defaultdict(list)
    for line in statement:
        lines_by_key[key_of_line(line)].append(line)

    matched: list[Match] = []
    unmatched_trades: list[Trade] = []
    unmatched_lines: list[StatementLine] = []
    ambiguous: list[AmbiguousGroup] = []
    for key in trades_by_key.keys() | lines_by_key.keys():
        trades = trades_by_key.get(key, [])
        lines = lines_by_key.get(key, [])
        if len(trades) == 1 and len(lines) == 1:
            matched.append(Match(trade=trades[0], line=lines[0]))
        elif not trades:
            unmatched_lines.extend(lines)
        elif not lines:
            unmatched_trades.extend(trades)
        else:
            ambiguous.append(
                AmbiguousGroup(
                    key=key,
                    trades=tuple(sorted(trades, key=lambda t: t.trade_id)),
                    lines=tuple(sorted(lines, key=lambda line: line.line_id)),
                )
            )

    return MatchResult(
        matched=tuple(sorted(matched, key=lambda m: m.trade.trade_id)),
        unmatched_trades=tuple(sorted(unmatched_trades, key=lambda t: t.trade_id)),
        unmatched_lines=tuple(sorted(unmatched_lines, key=lambda line: line.line_id)),
        ambiguous=tuple(sorted(ambiguous, key=lambda a: a.trades[0].trade_id)),
    )
