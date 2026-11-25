"""Score the break classifier (SE-20) against the golden set, next to the engine's labels.

The engine finds the breaks and labels each one. The classifier sees the same breaks
without that label and gives its own root cause. Both are scored on the same records, so
the report shows whether the classifier is better, worse, or just more expensive.

Only breaks the engine found are classified: a break it missed never reaches the classifier,
so match rate and false break rate don't change here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from settlement.agents.classifier import classify
from settlement.agents.errors import AgentError
from settlement.agents.ledger import CostLedger
from settlement.agents.prompts import Prompt
from settlement.agents.records import CallRecord
from settlement.agents.runner import AgentContext
from settlement.domain.models import Break, BreakType
from settlement.evaluation.metrics import TypeAccuracy, classification_by_type
from settlement.evaluation.outcomes import Outcomes


@dataclass(frozen=True, slots=True)
class ClassifierSetup:
    prompt: Prompt
    context: AgentContext
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.limit is not None:
            raise ValueError("limit must be at least 1")


@dataclass(frozen=True, slots=True)
class ClassifierScores:
    prompt_version: str
    prompt_fingerprint: str
    limit: int | None
    records: int
    breaks: int
    failed: tuple[tuple[str, str], ...]
    engine: Mapping[BreakType, TypeAccuracy]
    classifier: Mapping[BreakType, TypeAccuracy]
    cost: Decimal

    @property
    def cost_per_break(self) -> Decimal | None:
        return self.cost / self.breaks if self.breaks else None


class _Tally:
    """A ledger that keeps a running total and passes every record on to the real one.
    Failed calls raise instead of returning a result, so this is the only way to count them."""

    def __init__(self, inner: CostLedger) -> None:
        self.inner = inner
        self.total = Decimal(0)

    def record(self, record: CallRecord) -> None:
        self.total += record.cost
        self.inner.record(record)


def score_classifier(
    found: Sequence[tuple[str, Break]],
    expected: Outcomes,
    setup: ClassifierSetup,
) -> ClassifierScores:
    """Classify every break in 'found', given as (record, break), and score per break type.

    Records keep the order given, so that caller decided what a limited sample contains.
    A record's breaks are scored together, like the engine's: a trage with two problems
    needs both types, one from each of its breaks."""
    by_record: dict[str, list[Break]] = {}
    for record, brk in found:
        by_record.setdefault(record, []).append(brk)
    chosen = list(by_record)[: setup.limit]

    tally = _Tally(setup.context.ledger)
    context = replace(setup.context, ledger=tally)
    engine_pairs: list[tuple[frozenset[BreakType], set[BreakType]]] = []
    classifier_pairs: list[tuple[frozenset[BreakType], set[BreakType]]] = []
    failed: list[tuple[str, str]] = []
    for record in chosen:
        right = expected.broken.get(record, frozenset())
        engine_says: set[BreakType] = set()
        classifier_says: set[BreakType] = set()
        for brk in by_record[record]:
            engine_says.add(brk.break_type)
            try:
                result = classify(brk, prompt=setup.prompt, context=context)
            except AgentError as e:
                failed.append((brk.break_id, str(e)))
                continue
            classifier_says.add(result.output.root_cause)
        engine_pairs.append((right, engine_says))
        classifier_pairs.append((right, classifier_says))

    return ClassifierScores(
        prompt_version=setup.prompt.version,
        prompt_fingerprint=setup.prompt.fingerprint,
        limit=setup.limit,
        records=len(chosen),
        breaks=sum(len(by_record[record]) for record in chosen),
        failed=tuple(failed),
        engine=classification_by_type(engine_pairs),
        classifier=classification_by_type(classifier_pairs),
        cost=tally.total,
    )
