"""Evaluation metrics (SE-17): pure functions, each small enough to check by hand.

Inputs are plain sets, lists and counts, never a report or a file, so every metric can be
unit tested against an example worked out on paper. The evaluation runner (SE-18) gathers
the inputs from a real run and calls these.

Rates are floats: they're proportions for people to read, not money or quantities. Cost is
money, so it stays Decimal.
"""

from collections.abc import Sequence, Set
from dataclasses import dataclass

from settlement.domain.models import BreakType


@dataclass(frozen=True, slots=True)
class Ratio:
    """A count out of a total, e.g. 3 matched out of 4 clean trades."""

    count: int
    total: int

    def __post_init__(self) -> None:
        if not 0 <= self.count <= self.total:
            raise ValueError(f"count must be between 0 and total, got {self.count}/{self.total}")

    @property
    def value(self) -> float | None:
        """count / total, or None when there was nothing to count: no data isn't 0%."""
        return self.count / self.total if self.total else None


def match_rate(expected_clean: Set[str], reported_clean: Set[str]) -> Ratio:
    """Of the trades that should come out clean, how many we reported clean."""
    return Ratio(len(expected_clean & reported_clean), len(expected_clean))


def false_break_rate(expected_clean: Set[str], broken: Set[str]) -> Ratio:
    """Of the trades that should come out clean, how many we raised a break on.

    The expensive error: every false break is an ops person chasing something that's fine.
    """
    return Ratio(len(expected_clean & broken), len(expected_clean))


@dataclass(frozen=True, slots=True)
class TypeAccuracy:
    """How well one break type was classified."""

    precision: Ratio  # of the breaks we called this type, how many really were
    recall: Ratio  # of the breaks that really were this type, how many we called it


def classification_by_type(
    outcomes: Sequence[tuple[BreakType | None, BreakType | None]],
) -> dict[BreakType, TypeAccuracy]:
    """Precision and recall for every break type that appears, actual or predicted.

    Each outcome is (actual, predicted) for one trade, where None means no break. A false
    break (None, X) lowers X's precision; a miss (X, None) lowers X's recall.
    """
    by_type: dict[BreakType, TypeAccuracy] = {}
    for break_type in BreakType:  # enum order, so the result is the same every run
        correct = sum(1 for actual, predicted in outcomes if actual == predicted == break_type)
        called = sum(1 for _, predicted in outcomes if predicted == break_type)
        real = sum(1 for actual, _ in outcomes if actual == break_type)
        if called or real:
            by_type[break_type] = TypeAccuracy(
                precision=Ratio(correct, called), recall=Ratio(correct, real)
            )
    return by_type
