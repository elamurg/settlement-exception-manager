"""Score a run: compare what should have happened with what did, using the SE-17 metrics."""

from collections.abc import Mapping
from dataclasses import dataclass

from settlement.domain.models import BreakType
from settlement.evaluation.metrics import (
    Ratio,
    TypeAccuracy,
    classification_by_type,
    false_break_rate,
    left_for_human_rate,
    match_rate,
)
from settlement.evaluation.outcomes import Outcomes

_NO_BREAK: frozenset[BreakType] = frozenset()


@dataclass(frozen=True, slots=True)
class Scores:
    """The metrics a deterministic run can be measured on. Prioritisation, acceptance and
    cost need an ideal queue, resolutions and model costs, so they come with the agents."""

    match_rate: Ratio
    false_break_rate: Ratio
    left_for_human_rate: Ratio
    by_type: Mapping[BreakType, TypeAccuracy]


def score(expected: Outcomes, actual: Outcomes) -> Scores:
    records = sorted(expected.records | actual.records)  # sorted, so the same run scores the same
    return Scores(
        match_rate=match_rate(expected.clean, actual.clean),
        false_break_rate=false_break_rate(expected.clean, frozenset(actual.broken)),
        left_for_human_rate=left_for_human_rate(expected.unresolved, actual.unresolved),
        by_type=classification_by_type(
            [
                (expected.broken.get(record, _NO_BREAK), actual.broken.get(record, _NO_BREAK))
                for record in records
            ]
        ),
    )


def passed(expected: Outcomes, actual: Outcomes) -> bool:
    """Every record the case gives an answer for came out exactly that way.

    A record the case doesn't mention (a stray line it chose not to list) doesn't count.
    """
    return all(
        describe(expected, record) == describe(actual, record) for record in expected.records
    )


def describe(outcomes: Outcomes, record: str) -> str:
    """One record's outcome in words, e.g. "broken: PRICE_BREAK, SSI_MISMATCH"."""
    if record in outcomes.clean:
        return "clean"
    if record in outcomes.unresolved:
        return "unresolved"
    if record in outcomes.broken:
        return "broken: " + ", ".join(sorted(outcomes.broken[record]))
    return "nothing"
