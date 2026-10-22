"""Fuzzy pass: turn what exact matching and tolerance checks left over into typed breaks.

A near match must be unique in both directions, and anything that can't be decided goes
to 'unresolved' for a human to check. Nothing is guessed.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime

from settlement.domain.errors import InvalidMatchInput, InvalidModel
from settlement.domain.identifiers import ISIN
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
    Side,
    StatementLine,
    Trade,
    require_text,
)
from settlement.domain.tolerance import ToleranceResult

# Key fields where a single difference still means "the same trade, with one thing wrong".
# ISIN and side are left out on purpose: a different ISIN or side is a different trade.
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
    """Raise a typed break for everything that has one clear cause; leave the rest unresolved.

    `tolerance_results` must hold exactly one result per matched pair in `match_result`.
    """
    checked = [result.match for result in tolerance_results]
    if sorted(checked, key=_match_id) != sorted(match_result.matched, key=_match_id):
        raise InvalidMatchInput("Tolerance results must cover exactly the matched pairs.")
    near, unresolved_trades, unresolved_lines = _near_match_breaks(
        match_result.unmatched_trades, match_result.unmatched_lines, raised_at
    )
    duplicates, unresolved_groups = _duplicate_breaks(match_result.ambiguous, raised_at)
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
    """A PRICE_BREAK for every matched pair outside tolerance."""
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
    """Check each matched pair's settlement details against our SSI on file.

    No SSI on file is itself a static data problem (we couldn't have instructed correctly),
    so it raises a STATIC_DATA break. Otherwise, an SSI_MISMATCH where the custodian settled
    against details that differ from ours. Details the custodian doesn't report (None) are
    not compared.
    """
    breaks: list[Break] = []
    for result in tolerance_results:
        trade, line = result.match.trade, result.match.line
        on_file = settlement_details.get(trade.trade_id)
        if on_file is None:
            no_ssi = FieldDifference(
                "ssi_on_file", ours="missing", theirs=line.counterparty_bic or "not reported"
            )
            breaks.append(_make_break(BreakType.STATIC_DATA, trade, line, (no_ssi,), raised_at))
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
    """Pair unmatched trades and lines that differ in exactly one near-match field.

    A pair counts only if it's unique both ways: the trade has one candidate line and that
    line has one candidate trade. A trade with no candidate is an unmatched instruction,
    unless a leftover line shares its ISIN, side and counterparty: that line is probably
    ours with more than one field wrong, so the trade is left unresolved instead of being
    reported as having no line. Everything else is left unresolved.
    """
    # Near matches share ISIN and side (neither is a near-match field), so only lines with
    # the same ISIN and side need comparing: thousands of comparisons, not millions.
    lines_by_isin_side: defaultdict[tuple[ISIN, Side], list[StatementLine]] = defaultdict(list)
    for line in lines:
        lines_by_isin_side[(line.isin, line.side)].append(line)

    candidates_for_trade: defaultdict[str, list[tuple[StatementLine, FieldDifference]]] = (
        defaultdict(list)
    )
    candidates_for_line: defaultdict[str, list[Trade]] = defaultdict(list)

    for trade in trades:
        for line in lines_by_isin_side.get((trade.isin, trade.side), []):
            differences = _differences(trade, line)
            if len(differences) == 1 and differences[0].field in NEAR_MATCH_FIELDS:
                candidates_for_trade[trade.trade_id].append((line, differences[0]))
                candidates_for_line[line.line_id].append(trade)

    breaks: list[Break] = []
    unresolved_trades: list[Trade] = []
    used_line_ids: set[str] = set()
    for trade in trades:
        candidates = candidates_for_trade.get(trade.trade_id, [])
        if not candidates and _has_similar_line(trade, lines_by_isin_side):
            unresolved_trades.append(trade)
        elif not candidates:
            # "no matching line", not "missing": a line may exist with too many differences.
            no_line = FieldDifference("statement_line", ours="present", theirs="no matching line")
            breaks.append(
                _make_break(BreakType.UNMATCHED_INSTRUCTION, trade, None, (no_line,), raised_at)
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


def _has_similar_line(
    trade: Trade,
    lines_by_isin_side: Mapping[tuple[ISIN, Side], Sequence[StatementLine]],
) -> bool:
    """A leftover line with the trade's ISIN, side and counterparty: probably ours, mis-reported."""
    return any(
        line.counterparty_lei == trade.counterparty.lei
        for line in lines_by_isin_side.get((trade.isin, trade.side), [])
    )


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
    """Every economic key field where our trade and their line disagree."""
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
