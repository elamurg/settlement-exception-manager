"""The only module that imports the Anthropic SDK. It turns a ModelRequest into an API call,
and SDK errors into our errors."""

import anthropic
from anthropic.types import (
    AssistantMessageParam,
    MessageParam,
    OutputConfigParam,
    TextBlock,
    UserMessageParam,
)
from anthropic.types import Message as ApiMessage

from settlement.agents.client import Message, ModelReply, ModelRequest
from settlement.agents.errors import AgentTimeout, AgentUnavailable
from settlement.agents.records import Usage

DEFAULT_TIMEOUT_SECONDS = 180.0  # Opus 5.5 always thinks first, so give it time
DEFAULT_MAX_RETRIES = 2  # the SDK retries 408, 409, 429, 5xx and connection errors


class AnthropicModelClient:
    def __init__(
        self,
        sdk: anthropic.Anthropic | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        # No key here: the SDK reads ANTHROPIC_API_KEY from the environment.
        # A test passes its own stub sdk, and then timeout and max_retries aren't used.
        self._sdk = sdk or anthropic.Anthropic(timeout=timeout, max_retries=max_retries)

    def complete(self, request: ModelRequest) -> ModelReply:
        messages = [_to_api(message) for message in request.messages]
        # The annotation lets mypy check this dict against the SDK's own type.
        output_config: OutputConfigParam = {
            "effort": request.effort,
            "format": {"type": "json_schema", "schema": request.schema},
        }
        try:
            reply = self._sdk.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=messages,
                output_config=output_config,
            )
        except anthropic.APITimeoutError:
            # Caught first, because a timeout is a kind of APIConnectionError.
            raise AgentTimeout(request.agent, "no answer in time, retries included") from None
        except (anthropic.APIConnectionError, anthropic.RateLimitError) as e:
            raise AgentUnavailable(request.agent, f"unreachable or rate-limited: {e}") from None
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise AgentUnavailable(request.agent, f"API error {e.status_code}") from None
            raise  # 400, 401, 404: our bug or our config. A retry won't fix it, so don't hide it.
        return _to_reply(reply)


def _to_api(message: Message) -> MessageParam:
    if message.role == "user":
        return UserMessageParam(role="user", content=message.text)
    return AssistantMessageParam(role="assistant", content=message.text)


def _to_reply(message: ApiMessage) -> ModelReply:
    # Opus 5.5 always thinks, so thinking blocks can come before the answer. Keep only text.
    text = "".join(block.text for block in message.content if isinstance(block, TextBlock))
    usage = message.usage
    return ModelReply(
        text=text,
        stop_reason=message.stop_reason or "",
        model=message.model,
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        ),
        # Set by the SDK on real HTTP replies only, so a built-by-hand Message has none.
        request_id=getattr(message, "_request_id", None),
        refusal_category=message.stop_details.category if message.stop_details else None,
    )
