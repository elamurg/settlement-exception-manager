"""Tolerance rules for consideration differences on matched trades,
as a versioned set."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from settlement.domain.errors import InvalidModel
from settlement.domain.matching import Match
from settlement.domain.models import (
    CURRENCY_PATTERN,
    require_not_negative,
    require_text,
)

WILDCARD = "*"  # any currency or any instrument type


@dataclass(frozen=True, slots=True)
class ToleranceRule:
    currency: str
    instrument_type: str
    absolute: Decimal
    percentage: Decimal

    def __post_init__(self) -> None:
        if self.currency != WILDCARD and (
            not isinstance(self.currency, str) or not CURRENCY_PATTERN.fullmatch(self.currency)
        ):
            raise InvalidModel("Currency must be a 3-letter code or '*'.")
        require_text("instrument_type", self.instrument_type)
        require_not_negative("absolute", self.absolute)
        require_not_negative("percentage", self.percentage)
        if self.percentage > 1:
            raise InvalidModel("Percentage is a fraction. It cannot exceed 1.")

    @property
    def is_fallback(self) -> bool:
        return self.currency == WILDCARD and self.instrument_type == WILDCARD


@dataclass(frozen=True, slots=True)
class ToleranceSet:
    version: str
    effective_from: date
    rules: tuple[ToleranceRule, ...]

    # all() - returns true is all the elements in the instance are true
    # isinstance() - compares if the variable is a certain data structure
    def __post_init__(self) -> None:
        require_text("version", self.version)
        if not isinstance(self.effective_from, date) or isinstance(self.effective_from, datetime):
            raise InvalidModel("Effective_from must be a date.")
        if not isinstance(self.rules, tuple) or not self.rules:
            raise InvalidModel("Rules must be a non-empty tuple.")
        if not all(isinstance(rule, ToleranceRule) for rule in self.rules):
            raise InvalidModel("Rules must contain only ToleranceRule items.")

        seen: set[tuple[str, str]] = set()
        for rule in self.rules:
            key = (rule.currency, rule.instrument_type)
            if key in seen:
                raise InvalidModel(f"Duplicate rule for {rule.currency}/{rule.instrument_type}")
            seen.add(key)

        fallback = sum(1 for rule in self.rules if rule.is_fallback)
        if fallback != 1:
            raise InvalidModel(
                f"exactly one fallback rule ('*', '*') is required, found {fallback}"
            )

    def rule_for(self, currency: str, instrument_type: str) -> ToleranceRule:
        """Rule for a trade. Currency beats instrument type:
        EUR/* is chosen over */EQUITY, because tolerances are amounts of money."""
        by_key = {(rule.currency, rule.instrument_type): rule for rule in self.rules}
        for key in (
            (currency, instrument_type),
            (currency, WILDCARD),
            (WILDCARD, instrument_type),
            (WILDCARD, WILDCARD),
        ):
            if key in by_key:
                return by_key[key]
        raise AssertionError("Unreachable: __post_init__ guarantees a fallback rule.")


@dataclass(frozen=True, slots=True)
class ToleranceResult:
    """One matched pair checked against a tolerance set,
    with everything needed to explain it."""

    match: Match
    difference: Decimal
    allowed: Decimal
    within: bool
    rule: ToleranceRule
    tolerance_set_version: str


def evaluate(
    match: Match, tolerance_set: ToleranceSet, *, instrument_type: str = WILDCARD
) -> ToleranceResult:
    """Check a matched pair's consideration difference against the tolerance set.

    The allowed difference is the greater of the rule's absolute amount and its percentage
    of our consideration. Different currencies are never within tolerance.
    """
    ours = match.trade.consideration
    theirs = match.line.consideration
    difference = theirs - ours
    rule = tolerance_set.rule_for(match.trade.currency, instrument_type)

    if match.trade.currency != match.line.currency:
        allowed = Decimal("0")
    else:
        allowed = max(rule.absolute, rule.percentage * abs(ours))

    return ToleranceResult(
        match=match,
        difference=difference,
        allowed=allowed,
        within=match.trade.currency == match.line.currency and abs(difference) <= allowed,
        rule=rule,
        tolerance_set_version=tolerance_set.version,
    )
