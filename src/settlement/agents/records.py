"""What one model call used and cost, and who it was for."""

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from settlement.domain.errors import InvalidModel
from settlement.domain.models import require_aware, require_not_negative, require_text, require_type


@dataclass(frozen=True)
class Usage:
    """Tokens one call used, as the API reports them."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            # bool is a kind of int in Python, so True would sneak through without the 2nd check.
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidModel(f"{field.name} must be a whole number, 0 or more.")


NO_USAGE = Usage(input_tokens=0, output_tokens=0)  # a call that never got an answer


class Outcome(StrEnum):
    OK = "OK"  # a valid answer
    INVALID = "INVALID"  # an answer that broke the schema or our rules
    REFUSED = "REFUSED"  # the model declined
    TRUNCATED = "TRUNCATED"  # the model stopped before finishing
    FAILED = "FAILED"  # no answer at all: a timeout, or the API was down


@dataclass(frozen=True)
class CallRecord:
    """One call to the model: who asked, about what, with which prompt, and what it cost."""

    agent: str
    subject: str  # what the call was about, e.g. a break id
    prompt_version: str
    prompt_fingerprint: str
    model: str  # the model that actually answered
    attempt: int  # 1 for the first ask, 2 for the re-ask
    outcome: Outcome
    usage: Usage
    cost: Decimal
    at: datetime
    request_id: str | None = None  # the API's id for the call, if Anthropic support ever asks

    def __post_init__(self) -> None:
        for name in ("agent", "subject", "prompt_version", "prompt_fingerprint", "model"):
            require_text(name, getattr(self, name))
        if not isinstance(self.attempt, int) or self.attempt < 1:
            raise InvalidModel("attempt must be 1 or more.")
        require_type("outcome", self.outcome, Outcome)
        require_type("usage", self.usage, Usage)
        require_not_negative("cost", self.cost)
        require_aware("at", self.at)
