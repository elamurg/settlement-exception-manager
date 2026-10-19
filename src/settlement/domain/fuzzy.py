"""Fuzzy pass: turn the exact matching and tolerance checks left
over into typed breaks. A near match must be unique in both directions and
anything that can't be decided goes to 'unresolved' for a human check."""

# abc. = abstract base classes
# Sequence = an ordered collecntion I can loop over
# Mapping = something i can look things up in by key
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime

from settlement.domain.errors import InvalidMatchInput, InvalidModel
from settlement.domain.matching import (
    AmbiguousGroup,
    EconomicKey,
    Match,
    MatchResult,
    key_of_line,
    key_of_trade,
)
from settlement.domain.models import (
    BIC_PATTERN,
    Break,
    BreakType,
    FieldDifference,
    StatementLine,
    Trade,
    require_text,
)
from settlement.domain.tolerance import ToleranceResult

# dif ISIN or dif side is dif trade
NEAR_MATCH_FIELDS: Mapping[str, BreakType] = {
    "quantity": BreakType.QUANTITY_BREAK,
    "intended_settlement_date": BreakType.DATE_MISMATCH,
    "counterparty_lei": BreakType.STATIC_DATA,
}


@dataclass(frozen=True, slots=True)
class SettlementDetails:
    """The counterparty's settlement details that are being held on file from the SSI."""

    bic: str
    account: str

    def __post_init__(self) -> None:
        if not isinstance(self.bic, str) or not BIC_PATTERN.fullmatch(self.bic):
            raise InvalidModel(f"Settlement details BIC must be valid: {self.bic!r}")
        require_text("account", self.account)


@dataclass(frozen=True, slots=True)
class FuzzyResult:
    breaks: tuple[Break, ...]
    unresolved_trades: tuple[Trade, ...]
    unresolved_lines: tuple[StatementLine, ...]
    unresolved_groups: tuple[AmbiguousGroup, ...]


def find_breaks(
    match_result: MatchResult,
    tolerance_results: Sequence[ToleranceResult],
    settlement_details: Mapping[str, SettlementDetails],
    *,
    raised_at: datetime,
) -> FuzzyResult:
    """Raise a typed break for everything that has one clear cause, leave the rest unresolved."""
    checked = [result.match for result in tolerance_results]
    if sorted(checked, key=_match_id) != sorted(match_result.matched, key=_match_id):
        raise InvalidMatchInput("Tolerance results must cover exactly the matched pairs.")
    near, unresolved_trades, unresolved_lines = _near_match_breaks(
        match_result.unmatched_trades, match_result.unmatched_lines, raised_at
    )
    duplicates, unresolved_groups = _duplicate_breaks(match_result.ambiguous, raised_at)
    # the * in front of each list unpacks it into a new one
    # needed to make a flay list of break objects
    breaks = [
        *_price_breaks(tolerance_results, raised_at),
        *_ssi_breaks(tolerance_results, settlement_details, raised_at),
        *near,
        *duplicates,
    ]
    return FuzzyResult(
        breaks=tuple(sorted(breaks, key=lambda b: b.break_id)),
        unresolved_trades=tuple(sorted(unresolved_trades, key=lambda t: t.trade_id)),
        unresolved_lines=tuple(sorted(unresolved_lines, key=lambda line: line.line_id)),
        unresolved_groups=tuple(unresolved_groups),
    )


def _price_breaks(
    tolerance_results: Sequence[ToleranceResult],
    raised_at: datetime,
) -> list[Break]:
    """A PRICE_BREAK for every matched pair outsife tolerance"""
    breaks: list[Break] = []
    for result in tolerance_results:
        if result.within:
            continue
        trade, line = result.match.trade, result.match.line
        if trade.currency != line.currency:
            difference = FieldDifference("currency", ours=trade.currency, theirs=line.currency)
        else:
            difference = FieldDifference(
                "consideration",
                ours=str(trade.consideration),
                theirs=str(line.consideration),
            )
        breaks.append(_make_break(BreakType.PRICE_BREAK, trade, line, (difference,), raised_at))
    return breaks


def _ssi_breaks(
    tolerance_results: Sequence[ToleranceResult],
    settlement_details: Mapping[str, SettlementDetails],
    raised_at: datetime,
) -> list[Break]:
    """An SSI_MISMATCH where the custodian settled against details that differ from ours."""
    breaks: list[Break] = []
    for result in tolerance_results:
        trade, line = result.match.trade, result.match.line
        on_file = settlement_details.get(trade.trade_id)
        if on_file is None:
            continue

        evidence: list[FieldDifference] = []
        if line.counterparty_bic is not None and line.counterparty_bic != on_file.bic:
            evidence.append(
                FieldDifference("counterparty_bic", ours=on_file.bic, theirs=line.counterparty_bic)
            )
        if line.counterparty_account is not None and line.counterparty_account != on_file.account:
            evidence.append(
                FieldDifference(
                    "counterparty_account", ours=on_file.account, theirs=line.counterparty_account
                )
            )
        if evidence:
            breaks.append(
                _make_break(BreakType.SSI_MISMATCH, trade, line, tuple(evidence), raised_at)
            )
    return breaks


def _near_match_breaks(
    trades: Sequence[Trade],
    lines: Sequence[StatementLine],
    raised_at: datetime,
) -> tuple[list[Break], list[Trade], list[StatementLine]]:
    """Pair unmatched trades and lines that differ in exactly one near-match field."""
    candidates_for_trade: defaultdict[str, list[tuple[StatementLine, FieldDifference]]] = (
        defaultdict(list)
    )
    candidates_for_line: defaultdict[str, list[Trade]] = defaultdict(list)

    for trade in trades:
        for line in lines:
            differences = _differences(trade, line)
            if len(differences) == 1 and differences[0].field in NEAR_MATCH_FIELDS:
                candidates_for_trade[trade.trade_id].append((line, differences[0]))
                candidates_for_line[line.line_id].append(trade)

    breaks: list[Break] = []
    unresolved_trades: list[Trade] = []
    used_line_ids: set[str] = set()
    for trade in trades:
        candidates = candidates_for_trade.get(trade.trade_id, [])
        if not candidates:
            missing = FieldDifference("statement_line", ours="present", theirs="missing")
            breaks.append(
                _make_break(BreakType.UNMATCHED_INSTRUCTION, trade, None, (missing,), raised_at)
            )
        elif len(candidates) == 1 and len(candidates_for_line[candidates[0][0].line_id]) == 1:
            line, difference = candidates[0]
            break_type = NEAR_MATCH_FIELDS[difference.field]
            breaks.append(_make_break(break_type, trade, line, (difference,), raised_at))
            used_line_ids.add(line.line_id)
        else:
            unresolved_trades.append(trade)
    unresolved_lines = [line for line in lines if line.line_id not in used_line_ids]
    return breaks, unresolved_trades, unresolved_lines


def _duplicate_breaks(
    groups: Sequence[AmbiguousGroup],
    raised_at: datetime,
) -> tuple[list[Break], list[AmbiguousGroup]]:
    """A DUPLICATE_BOOKING on every trade in a group of identical trades with one line.
    Each booking is flagged instead of picking which is the duplicate."""
    breaks: list[Break] = []
    unresolved: list[AmbiguousGroup] = []
    for group in groups:
        if len(group.trades) >= 2 and len(group.lines) == 1:
            (line,) = group.lines
            count = FieldDifference("booking_count", ours=str(len(group.trades)), theirs="1")
            for trade in group.trades:
                breaks.append(
                    _make_break(BreakType.DUPLICATE_BOOKING, trade, line, (count,), raised_at)
                )
        else:
            unresolved.append(group)
    return breaks, unresolved


def _differences(trade: Trade, line: StatementLine) -> list[FieldDifference]:
    """Every economic key field where our tradde and their line disagree"""
    ours = key_of_trade(trade)
    theirs = key_of_line(line)
    differences: list[FieldDifference] = []
    for key_field in fields(EconomicKey):
        our_value = getattr(ours, key_field.name)
        their_value = getattr(theirs, key_field.name)
        if our_value != their_value:
            differences.append(
                FieldDifference(key_field.name, ours=str(our_value), theirs=str(their_value))
            )
    return differences


def _make_break(
    break_type: BreakType,
    trade: Trade | None,
    line: StatementLine | None,
    evidence: tuple[FieldDifference, ...],
    raised_at: datetime,
) -> Break:
    """A Break with a deterministic id: the type plus the trade id or line id."""
    if trade is not None:
        record_id, settles = trade.trade_id, trade.intended_settlement_date
    elif line is not None:
        record_id, settles = line.line_id, line.intended_settlement_date
    else:
        raise InvalidModel("A break needs a trade or statement line.")
    return Break(
        break_id=f"B-{break_type.value}-{record_id}",
        break_type=break_type,
        ours=trade,
        theirs=line,
        evidence=evidence,
        intended_settlement_date=settles,
        raised_at=raised_at,
    )


def _match_id(match: Match) -> tuple[str, str]:
    return (match.trade.trade_id, match.line.line_id)
