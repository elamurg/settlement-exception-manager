"""Generate a book of trades and the custodian's statement of the same trades, with
breaks injected at configured rates.

Pure: no files, no clock. The same config always gives the same result, so the manifest
of injected breaks can serve as ground truth for evaluation (SE-11, SE-16).

Every trade on the book has, by default, one statement line that agrees with it on every
economic field. An injected break changes exactly one thing about that pairing, and the
manifest records what changed.
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from settlement.domain.models import (
    BreakType,
    FieldDifference,
    SettlementMethod,
    Side,
    StatementLine,
    Trade,
    TradeStatus,
)
from settlement.generation.universe import make_instruments, make_parties

GENERATOR_VERSION = "1"

# Break types a book-versus-statement pair can show. The others need data these two
# files don't carry (SSIs, cash balances, corporate action notices).
INJECTABLE = (
    BreakType.STATIC_DATA,
    BreakType.QUANTITY_BREAK,
    BreakType.PRICE_BREAK,
    BreakType.DATE_MISMATCH,
    BreakType.UNMATCHED_INSTRUCTION,
    BreakType.DUPLICATE_BOOKING,
)

# Probability that a trade gets each break: 10% in total. Static data is weighted
# highest, reflecting published fail causes.
DEFAULT_BREAK_RATES: Mapping[BreakType, float] = MappingProxyType(
    {
        BreakType.STATIC_DATA: 0.03,
        BreakType.QUANTITY_BREAK: 0.015,
        BreakType.PRICE_BREAK: 0.015,
        BreakType.DATE_MISMATCH: 0.015,
        BreakType.UNMATCHED_INSTRUCTION: 0.015,
        BreakType.DUPLICATE_BOOKING: 0.01,
    }
)

DEFAULT_TRADE_DATE = date(2026, 10, 5)
FOP_SHARE = 0.05
INSTRUMENT_COUNT = 40
PARTY_COUNT = 15
SOURCE = "custodian"

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    trades: int
    seed: int
    trade_date: date = DEFAULT_TRADE_DATE
    break_rates: Mapping[BreakType, float] = field(default=DEFAULT_BREAK_RATES)

    def __post_init__(self) -> None:
        if self.trades < 1:
            raise ValueError("trades must be at least 1")
        for break_type, rate in self.break_rates.items():
            if break_type not in INJECTABLE:
                raise ValueError(f"{break_type} can't be injected into a book and statement")
            if rate < 0:
                raise ValueError(f"rate for {break_type} must not be negative")
        if sum(self.break_rates.values()) > 1:
            raise ValueError("break rates must add up to at most 1")


@dataclass(frozen=True, slots=True)
class InjectedBreak:
    """One break the generator put in on purpose: the ground truth for evaluation."""

    break_type: BreakType
    trade_id: str
    statement_line_id: str | None  # None when the custodian has no line for the trade
    evidence: tuple[FieldDifference, ...]
    related_trade_ids: tuple[str, ...] = ()  # the second booking, for duplicates


@dataclass(frozen=True, slots=True)
class GeneratedData:
    config: GenerationConfig
    book: tuple[Trade, ...]
    statement: tuple[StatementLine, ...]
    breaks: tuple[InjectedBreak, ...]


def money(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def next_business_day(day: date) -> date:
    """The next Monday-to-Friday day. Holidays are ignored for now."""
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def scaled_rates(total: float) -> dict[BreakType, float]:
    """The default mix of break types, scaled so the rates add up to `total`."""
    default_total = sum(DEFAULT_BREAK_RATES.values())
    return {kind: rate * total / default_total for kind, rate in DEFAULT_BREAK_RATES.items()}


def _pick_break(rng: random.Random, rates: Mapping[BreakType, float]) -> BreakType | None:
    draw = rng.random()
    cumulative = 0.0
    for break_type in INJECTABLE:  # fixed order, so results don't depend on dict order
        cumulative += rates.get(break_type, 0.0)
        if draw < cumulative:
            return break_type
    return None


def _statement_fields(trade: Trade) -> dict[str, object]:
    """What the custodian reports for a trade when nothing has gone wrong."""
    return {
        "source": SOURCE,
        "isin": trade.isin,
        "side": trade.side,
        "quantity": trade.quantity,
        "consideration": trade.consideration,
        "currency": trade.currency,
        "counterparty_lei": trade.counterparty.lei,
        "intended_settlement_date": trade.intended_settlement_date,
        "reference": trade.trade_id,
    }


def generate(config: GenerationConfig) -> GeneratedData:
    rng = random.Random(config.seed)
    instruments = make_instruments(rng, INSTRUMENT_COUNT)
    parties = make_parties(rng, PARTY_COUNT)
    settlement_date = next_business_day(config.trade_date)

    book: list[Trade] = []
    statement_rows: list[dict[str, object]] = []
    # (break type, trade id, evidence, related trade ids); line ids are assigned later.
    pending: list[tuple[BreakType, str, tuple[FieldDifference, ...], tuple[str, ...]]] = []
    duplicates: list[Trade] = []

    for number in range(1, config.trades + 1):
        break_type = _pick_break(rng, config.break_rates)
        instrument = rng.choice(instruments)
        quantity = Decimal(rng.randint(2, 1000) * 100)
        price = Decimal(rng.randint(100, 50_000)).scaleb(-2)  # 1.00 to 500.00
        # A price break needs a consideration to disagree on, so it's always DVP.
        is_fop = break_type != BreakType.PRICE_BREAK and rng.random() < FOP_SHARE

        trade = Trade(
            trade_id=f"T-{number:06d}",
            isin=instrument.isin,
            side=rng.choice([Side.BUY, Side.SELL]),
            quantity=quantity,
            price=price,
            consideration=Decimal("0.00") if is_fop else money(quantity * price),
            currency=instrument.currency,
            counterparty=rng.choice(parties),
            trade_date=config.trade_date,
            intended_settlement_date=settlement_date,
            settlement_method=SettlementMethod.FOP if is_fop else SettlementMethod.DVP,
            status=TradeStatus.INSTRUCTED,
        )
        book.append(trade)
        row = _statement_fields(trade)

        if break_type is None:
            statement_rows.append(row)
            continue

        evidence: tuple[FieldDifference, ...]
        related: tuple[str, ...] = ()

        if break_type == BreakType.UNMATCHED_INSTRUCTION:
            evidence = (FieldDifference("statement_line", ours="present", theirs="missing"),)
        elif break_type == BreakType.DUPLICATE_BOOKING:
            duplicate = replace(trade, trade_id=f"T-{config.trades + len(duplicates) + 1:06d}")
            duplicates.append(duplicate)
            related = (duplicate.trade_id,)
            evidence = (FieldDifference("booking_count", ours="2", theirs="1"),)
            statement_rows.append(row)
        elif break_type == BreakType.QUANTITY_BREAK:
            # Short by up to about 10%, in lots of 100.
            lots = rng.randint(1, max(1, int(quantity) // 1000))
            theirs = quantity - Decimal(lots * 100)
            row["quantity"] = theirs
            evidence = (FieldDifference("quantity", ours=str(quantity), theirs=str(theirs)),)
            statement_rows.append(row)
        elif break_type == BreakType.PRICE_BREAK:
            # Off by 0.1% to 2%, either way, and never by less than a cent.
            difference = max(_CENT, money(trade.consideration * rng.randint(10, 200) / 10_000))
            theirs = trade.consideration + rng.choice([difference, -difference])
            row["consideration"] = theirs
            evidence = (
                FieldDifference("consideration", ours=str(trade.consideration), theirs=str(theirs)),
            )
            statement_rows.append(row)
        elif break_type == BreakType.DATE_MISMATCH:
            theirs_date = next_business_day(settlement_date)
            row["intended_settlement_date"] = theirs_date
            evidence = (
                FieldDifference(
                    "intended_settlement_date",
                    ours=settlement_date.isoformat(),
                    theirs=theirs_date.isoformat(),
                ),
            )
            statement_rows.append(row)
        else:  # STATIC_DATA: the custodian has the wrong counterparty
            wrong = rng.choice([party for party in parties if party != trade.counterparty])
            row["counterparty_lei"] = wrong.lei
            evidence = (
                FieldDifference(
                    "counterparty_lei", ours=str(trade.counterparty.lei), theirs=str(wrong.lei)
                ),
            )
            statement_rows.append(row)

        pending.append((break_type, trade.trade_id, evidence, related))

    book.extend(duplicates)
    # Shuffle so position gives nothing away: duplicates aren't all at the end, and
    # statement order doesn't follow book order.
    rng.shuffle(book)
    rng.shuffle(statement_rows)

    statement = tuple(
        StatementLine(line_id=f"L-{number:06d}", **row)  # type: ignore[arg-type]
        for number, row in enumerate(statement_rows, start=1)
    )
    line_for_trade = {line.reference: line.line_id for line in statement}

    breaks = tuple(
        InjectedBreak(
            break_type=break_type,
            trade_id=trade_id,
            statement_line_id=line_for_trade.get(trade_id),
            evidence=evidence,
            related_trade_ids=related,
        )
        for break_type, trade_id, evidence, related in pending
    )
    return GeneratedData(config=config, book=tuple(book), statement=statement, breaks=breaks)
