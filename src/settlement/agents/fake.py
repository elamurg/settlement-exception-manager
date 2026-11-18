"""A scripted stand-in for the model, so tests never call the real API or spend money."""

from settlement.agents.client import DEFAULT_MODEL, ModelReply, ModelRequest
from settlement.agents.errors import AgentError
from settlement.agents.records import Usage


def reply(
    text: str,
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    model: str = DEFAULT_MODEL,
    refusal_category: str | None = None,
) -> ModelReply:
    """A reply with test-friendly defaults: 1,000 tokens in and 500 out ($0.014 on Opus 5.5)."""
    return ModelReply(
        text=text,
        stop_reason=stop_reason,
        model=model,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        request_id="req_fake",
        refusal_category=refusal_category,
    )


class FakeModelClient:
    """Answers with the scripted replies, in order. A scripted error is raised instead."""

    def __init__(self, *script: ModelReply | AgentError) -> None:
        self._script = list(script)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("FakeModelClient was called more times than the test scripted")
        step = self._script.pop(0)
        if isinstance(step, AgentError):
            raise step
        return step
