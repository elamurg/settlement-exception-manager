import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from settlement.agents.client import ModelReply
from settlement.agents.errors import (
    AgentError,
    AgentRefused,
    AgentTimeout,
    AgentTruncated,
    InvalidAgentOutput,
    PricingError,
)
from settlement.agents.fake import FakeModelClient, reply
from settlement.agents.ledger import InMemoryLedger
from settlement.agents.pricing import parse_price_list
from settlement.agents.prompts import Prompt
from settlement.agents.records import Outcome
from settlement.agents.runner import AgentContext, AgentSpec, run_agent

NOW = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
PRICES = parse_price_list(
    """
version: "test"
currency: "USD"
models:
  claude-opus-5-5: {input: "4.00", output: "20.00", cache_read: "0.20", cache_write: "5.00"}
"""
)


@dataclass(frozen=True)
class Verdict:
    label: str


def parse_verdict(data: dict[str, Any]) -> Verdict:
    label = data.get("label")
    if label not in ("MATCH", "BREAK"):
        raise ValueError(f"label must be MATCH or BREAK, got {label!r}")
    return Verdict(label)


SPEC = AgentSpec(
    name="judge",
    prompt=Prompt(name="judge", version="v1", text="Say MATCH or BREAK."),
    schema={
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
        "additionalProperties": False,
    },
    parse=parse_verdict,
    effort="low",
)
GOOD = json.dumps({"label": "BREAK"})
BANANA = json.dumps({"label": "BANANA"})


def setup(*script: ModelReply | AgentError) -> tuple[AgentContext, FakeModelClient, InMemoryLedger]:
    client = FakeModelClient(*script)
    ledger = InMemoryLedger()
    context = AgentContext(client=client, ledger=ledger, prices=PRICES, clock=lambda: NOW)
    return context, client, ledger


# The happy path


def test_a_good_answer_comes_back_typed_costed_and_stamped() -> None:
    context, _, ledger = setup(reply(GOOD))

    result = run_agent(SPEC, "Trade T-1 against statement S-1", subject="BRK-1", context=context)

    assert result.output == Verdict("BREAK")
    assert result.prompt_version == "v1"
    assert result.prompt_fingerprint == SPEC.prompt.fingerprint
    assert result.cost == Decimal("0.014")
    [record] = ledger.calls
    assert (record.agent, record.subject, record.attempt) == ("judge", "BRK-1", 1)
    assert record.outcome == Outcome.OK
    assert record.at == NOW


def test_the_request_carries_the_prompt_schema_and_effort() -> None:
    context, client, _ = setup(reply(GOOD))

    run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    [request] = client.requests
    assert request.system == "Say MATCH or BREAK."
    assert request.schema == SPEC.schema
    assert request.effort == "low"
    assert [message.text for message in request.messages] == ["Trade T-1"]


# One re-ask


def test_a_bad_answer_gets_one_re_ask_that_says_what_was_wrong() -> None:
    context, client, _ = setup(reply(BANANA), reply(GOOD))

    result = run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    assert result.output == Verdict("BREAK")
    assert [record.outcome for record in result.records] == [Outcome.INVALID, Outcome.OK]
    assert result.cost == Decimal("0.028")  # the failed try still cost money
    second = client.requests[1]
    assert [message.role for message in second.messages] == ["user", "assistant", "user"]
    assert "BANANA" in second.messages[-1].text


def test_two_bad_answers_raise_a_clear_domain_error_and_stop() -> None:
    context, client, ledger = setup(reply("not json"), reply(BANANA), reply(GOOD))

    with pytest.raises(InvalidAgentOutput, match="judge: answer still invalid after one re-ask"):
        run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    assert len(client.requests) == 2  # never a third ask, even though a good answer was waiting
    assert [record.outcome for record in ledger.calls] == [Outcome.INVALID, Outcome.INVALID]


def test_a_json_list_is_not_an_answer() -> None:
    context, _, _ = setup(reply("[]"), reply("[]"))

    with pytest.raises(InvalidAgentOutput, match="expected a JSON object"):
        run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)


def test_a_bug_in_the_parse_function_crashes_instead_of_re_asking() -> None:
    def buggy(data: dict[str, Any]) -> Verdict:
        return Verdict(data["lable"])  # typo: a KeyError is our bug, not the model's

    context, client, _ = setup(reply(GOOD), reply(GOOD))

    with pytest.raises(KeyError):
        run_agent(replace(SPEC, parse=buggy), "Trade T-1", subject="BRK-1", context=context)

    assert len(client.requests) == 1  # no paid re-ask for our own mistake


# Stop reasons


def test_a_refusal_raises_agent_refused_with_the_category() -> None:
    context, _, ledger = setup(reply("", stop_reason="refusal", refusal_category="cyber"))

    with pytest.raises(AgentRefused, match="category: cyber"):
        run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    assert [record.outcome for record in ledger.calls] == [Outcome.REFUSED]


def test_running_out_of_tokens_is_truncated_and_not_re_asked() -> None:
    context, client, ledger = setup(reply('{"label": "BR', stop_reason="max_tokens"), reply(GOOD))

    with pytest.raises(AgentTruncated, match="max_tokens"):
        run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    assert len(client.requests) == 1
    assert [record.outcome for record in ledger.calls] == [Outcome.TRUNCATED]


def test_a_refusal_on_the_re_ask_is_still_a_refusal() -> None:
    context, _, ledger = setup(reply(BANANA), reply("", stop_reason="refusal"))

    with pytest.raises(AgentRefused, match="none given"):
        run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    assert [record.outcome for record in ledger.calls] == [Outcome.INVALID, Outcome.REFUSED]


# Pricing


def test_a_model_with_no_price_fails_before_any_call() -> None:
    context, client, _ = setup(reply(GOOD))

    with pytest.raises(PricingError, match="no price for model 'mystery-model'"):
        run_agent(replace(SPEC, model="mystery-model"), "x", subject="BRK-1", context=context)

    assert client.requests == []  # nothing was paid for


def test_an_answer_from_an_unlisted_model_is_still_recorded_and_costed() -> None:
    context, _, ledger = setup(reply(GOOD, model="claude-opus-5-5-20261101"))

    run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    [record] = ledger.calls
    assert record.model == "claude-opus-5-5-20261101"  # the truth about who answered
    assert record.cost == Decimal("0.014")  # priced as the model we asked for


# Failed calls, and the Done-when


def test_a_timeout_is_recorded_at_zero_cost_and_raised() -> None:
    context, _, ledger = setup(AgentTimeout("judge", "no answer in time"))

    with pytest.raises(AgentTimeout):
        run_agent(SPEC, "Trade T-1", subject="BRK-1", context=context)

    [record] = ledger.calls
    assert record.outcome == Outcome.FAILED
    assert record.cost == Decimal(0)


def test_done_when_every_call_is_costed_and_attributed() -> None:
    context, _, ledger = setup(
        reply(GOOD),  # BRK-1: right first time
        reply(BANANA),  # BRK-2: wrong, then fixed by the re-ask
        reply(GOOD),
        reply(BANANA),  # BRK-3: wrong twice
        reply("nope"),
        reply("", stop_reason="refusal"),  # BRK-4: declined
        AgentTimeout("judge", "no answer in time"),  # BRK-5: never answered
    )

    run_agent(SPEC, "1", subject="BRK-1", context=context)
    run_agent(SPEC, "2", subject="BRK-2", context=context)
    with pytest.raises(InvalidAgentOutput):
        run_agent(SPEC, "3", subject="BRK-3", context=context)
    with pytest.raises(AgentRefused):
        run_agent(SPEC, "4", subject="BRK-4", context=context)
    with pytest.raises(AgentTimeout):
        run_agent(SPEC, "5", subject="BRK-5", context=context)

    assert [(record.subject, record.outcome) for record in ledger.calls] == [
        ("BRK-1", Outcome.OK),
        ("BRK-2", Outcome.INVALID),
        ("BRK-2", Outcome.OK),
        ("BRK-3", Outcome.INVALID),
        ("BRK-3", Outcome.INVALID),
        ("BRK-4", Outcome.REFUSED),
        ("BRK-5", Outcome.FAILED),
    ]
    assert all(record.agent == "judge" for record in ledger.calls)
    assert all(record.prompt_version == "v1" for record in ledger.calls)
    # 6 answered calls x $0.014. The timeout never answered, so it cost nothing.
    assert ledger.total_cost() == Decimal("0.084")
