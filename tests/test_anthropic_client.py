from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic.types import Message as ApiMessage
from anthropic.types import RefusalStopDetails, StopReason, TextBlock
from anthropic.types import Usage as ApiUsage

from settlement.agents.anthropic_client import AnthropicModelClient
from settlement.agents.client import DEFAULT_MODEL, Message, ModelRequest
from settlement.agents.errors import AgentTimeout, AgentUnavailable
from settlement.agents.records import Usage

REQUEST = ModelRequest(
    agent="judge",
    model=DEFAULT_MODEL,
    system="Say MATCH or BREAK.",
    messages=(Message(role="user", text="Trade T-1"),),
    schema={"type": "object"},
    effort="low",
    max_tokens=100,
)
HTTP_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class StubMessages:
    """Stands in for sdk.messages: remembers what create() was called with."""

    def __init__(self, result: ApiMessage | Exception) -> None:
        self.result = result
        self.sent: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> ApiMessage:
        self.sent = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StubSdk:
    def __init__(self, result: ApiMessage | Exception) -> None:
        self.messages = StubMessages(result)


def client_for(result: ApiMessage | Exception) -> tuple[AnthropicModelClient, StubSdk]:
    sdk = StubSdk(result)
    return AnthropicModelClient(cast(anthropic.Anthropic, sdk)), sdk


def api_message(
    text: str,
    stop_reason: StopReason = "end_turn",
    stop_details: RefusalStopDetails | None = None,
) -> ApiMessage:
    return ApiMessage(
        id="msg_1",
        type="message",
        role="assistant",
        model=DEFAULT_MODEL,
        content=[TextBlock(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=stop_details,
        stop_sequence=None,
        usage=ApiUsage(input_tokens=1000, output_tokens=500, cache_read_input_tokens=200),
    )


def test_sends_the_prompt_schema_and_effort() -> None:
    client, sdk = client_for(api_message('{"label": "BREAK"}'))

    client.complete(REQUEST)

    assert sdk.messages.sent["model"] == DEFAULT_MODEL
    assert sdk.messages.sent["system"] == "Say MATCH or BREAK."
    assert sdk.messages.sent["messages"] == [{"role": "user", "content": "Trade T-1"}]
    assert sdk.messages.sent["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }


def test_turns_the_api_message_into_a_reply() -> None:
    client, _ = client_for(api_message('{"label": "BREAK"}'))

    reply = client.complete(REQUEST)

    assert reply.text == '{"label": "BREAK"}'
    assert reply.stop_reason == "end_turn"
    assert reply.model == DEFAULT_MODEL
    assert reply.usage == Usage(input_tokens=1000, output_tokens=500, cache_read_tokens=200)


def test_a_refusal_keeps_its_category() -> None:
    details = RefusalStopDetails(type="refusal", category="cyber")
    client, _ = client_for(api_message("", stop_reason="refusal", stop_details=details))

    reply = client.complete(REQUEST)

    assert reply.stop_reason == "refusal"
    assert reply.refusal_category == "cyber"


@pytest.mark.parametrize(
    "error, expected",
    [
        (anthropic.APITimeoutError(request=HTTP_REQUEST), AgentTimeout),
        (anthropic.APIConnectionError(request=HTTP_REQUEST), AgentUnavailable),
        (
            anthropic.RateLimitError(
                "slow down", response=httpx2.Response(429, request=HTTP_REQUEST), body=None
            ),
            AgentUnavailable,
        ),
        (
            anthropic.InternalServerError(
                "oops", response=httpx2.Response(500, request=HTTP_REQUEST), body=None
            ),
            AgentUnavailable,
        ),
    ],
)
def test_sdk_errors_become_agent_errors(error: Exception, expected: type[Exception]) -> None:
    client, _ = client_for(error)

    with pytest.raises(expected):
        client.complete(REQUEST)


def test_a_bad_request_is_our_bug_and_is_not_hidden() -> None:
    error = anthropic.BadRequestError(
        "bad schema", response=httpx2.Response(400, request=HTTP_REQUEST), body=None
    )
    client, _ = client_for(error)

    with pytest.raises(anthropic.BadRequestError):
        client.complete(REQUEST)
