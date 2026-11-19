"""Run one agent call: ask, check the answer, re-ask once if it's broken, cost and record
every call, and stamp the result with the prompt version that produced it."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from settlement.agents.client import (
    DEFAULT_MODEL,
    Effort,
    Message,
    ModelClient,
    ModelReply,
    ModelRequest,
)
from settlement.agents.errors import (
    AgentRefused,
    AgentTimeout,
    AgentTruncated,
    AgentUnavailable,
    InvalidAgentOutput,
)
from settlement.agents.ledger import CostLedger
from settlement.agents.pricing import PriceList, cost_of
from settlement.agents.prompts import Prompt
from settlement.agents.records import NO_USAGE, CallRecord, Outcome
from settlement.domain.errors import InvalidModel
from settlement.domain.models import require_text

MAX_ATTEMPTS = 2  # the first ask plus one re-ask. A model wrong twice needs a human, not a loop.
REASK = "Your answer was rejected: {problem}\nReply again with only JSON that fits the schema."

# What a broken answer raises: bad JSON is a ValueError, a parse function raises ValueError for
# wrong content, and a domain type raises InvalidModel. Anything else (KeyError, TypeError...)
# is a bug in our parse function: it should crash loudly, not buy a paid re-ask.
_BROKEN_ANSWER = (ValueError, InvalidModel)


@dataclass(frozen=True)
class AgentSpec[T]:
    """Everything that makes one agent different from another."""

    name: str
    prompt: Prompt
    schema: dict[str, Any]  # the JSON shape the API must return
    parse: Callable[[dict[str, Any]], T]  # JSON -> our type; raises ValueError on a bad answer
    effort: Effort = "medium"
    max_tokens: int = 8000
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        require_text("name", self.name)
        if self.max_tokens < 1:
            raise InvalidModel("max_tokens must be at least 1.")


@dataclass(frozen=True)
class AgentContext:
    """The outside world an agent needs. Passed in, so a test can swap every part."""

    client: ModelClient
    ledger: CostLedger
    prices: PriceList
    clock: Callable[[], datetime]


@dataclass(frozen=True)
class AgentResult[T]:
    """A checked answer, stamped with the prompt that produced it and what it cost."""

    output: T
    agent: str
    prompt_version: str
    prompt_fingerprint: str
    model: str
    records: tuple[CallRecord, ...]

    @property
    def cost(self) -> Decimal:
        return sum((record.cost for record in self.records), Decimal(0))


def run_agent[T](
    spec: AgentSpec[T], user_input: str, *, subject: str, context: AgentContext
) -> AgentResult[T]:
    # Fail before paying for anything if the model we ask for has no price.
    cost_of(NO_USAGE, spec.model, context.prices)
    messages = [Message(role="user", text=user_input)]
    records: list[CallRecord] = []
    problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = _ask(spec, messages, subject, attempt, context)
        if reply.stop_reason == "refusal":
            _keep(spec, subject, attempt, Outcome.REFUSED, reply, context)
            category = reply.refusal_category or "none given"
            raise AgentRefused(spec.name, f"the model declined (category: {category})")
        if reply.stop_reason != "end_turn":
            # max_tokens, or another early stop: the JSON is unfinished. Not worth a re-ask,
            # because the same limit would cut the second answer off too.
            _keep(spec, subject, attempt, Outcome.TRUNCATED, reply, context)
            raise AgentTruncated(
                spec.name, f"stopped early ({reply.stop_reason}), max_tokens={spec.max_tokens}"
            )
        try:
            output = _parse(spec, reply.text)
        except _BROKEN_ANSWER as e:
            problem = str(e)
            records.append(_keep(spec, subject, attempt, Outcome.INVALID, reply, context))
            messages += [
                Message(role="assistant", text=reply.text),
                Message(role="user", text=REASK.format(problem=problem)),
            ]
            continue
        records.append(_keep(spec, subject, attempt, Outcome.OK, reply, context))
        return AgentResult(
            output=output,
            agent=spec.name,
            prompt_version=spec.prompt.version,
            prompt_fingerprint=spec.prompt.fingerprint,
            model=reply.model,
            records=tuple(records),
        )
    raise InvalidAgentOutput(spec.name, f"answer still invalid after one re-ask: {problem}")


def _ask[T](
    spec: AgentSpec[T], messages: list[Message], subject: str, attempt: int, context: AgentContext
) -> ModelReply:
    request = ModelRequest(
        agent=spec.name,
        model=spec.model,
        system=spec.prompt.text,
        messages=tuple(messages),
        schema=spec.schema,
        effort=spec.effort,
        max_tokens=spec.max_tokens,
    )
    try:
        return context.client.complete(request)
    except (AgentTimeout, AgentUnavailable):
        # No answer means no tokens to bill, but the attempt happened: record it at zero cost.
        _keep(spec, subject, attempt, Outcome.FAILED, None, context)
        raise


def _parse[T](spec: AgentSpec[T], text: str) -> T:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return spec.parse(data)


def _keep[T](
    spec: AgentSpec[T],
    subject: str,
    attempt: int,
    outcome: Outcome,
    reply: ModelReply | None,
    context: AgentContext,
) -> CallRecord:
    """Build the record and write it to the ledger straight away, so it survives a raise."""
    usage = reply.usage if reply else NO_USAGE
    model = reply.model if reply else spec.model
    # Price by the model that answered. If the API names one our price list doesn't know,
    # price it as the model we asked for: a call we paid for must never go unrecorded.
    priced_as = model if model in context.prices.models else spec.model
    record = CallRecord(
        agent=spec.name,
        subject=subject,
        prompt_version=spec.prompt.version,
        prompt_fingerprint=spec.prompt.fingerprint,
        model=model,
        attempt=attempt,
        outcome=outcome,
        usage=usage,
        cost=cost_of(usage, priced_as, context.prices),
        at=context.clock(),
        request_id=reply.request_id if reply else None,
    )
    context.ledger.record(record)
    return record
