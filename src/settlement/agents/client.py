"""The one interface agents use to reach a model. Tests plug in FakeModelClient,
production plugs in AnthropicModelClient, and the agents can't tell the difference."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from settlement.agents.records import Usage

DEFAULT_MODEL = "claude-opus-5-5"

type Effort = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class ModelRequest:
    agent: str  # who is asking, so errors can say which agent failed
    model: str
    system: str  # the prompt file's text
    messages: tuple[Message, ...]
    schema: dict[str, Any]  # the JSON shape the answer must have
    effort: Effort
    max_tokens: int


@dataclass(frozen=True)
class ModelReply:
    text: str
    stop_reason: str
    model: str  # the model that answered
    usage: Usage
    request_id: str | None = None
    refusal_category: str | None = None


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelReply: ...
