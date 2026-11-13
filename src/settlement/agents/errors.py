"""Errors raised by the agent base. The rest of the app never sees an SDK exception, only these."""

from settlement.domain.errors import DomainError


class AgentError(DomainError):
    """Base class for every agent failure. Says which agent failed and why."""

    def __init__(self, agent: str, message: str) -> None:
        super().__init__(f"{agent}: {message}")
        self.agent = agent


class InvalidAgentOutput(AgentError):
    """The answer broke the schema or our rules, even after one re-ask."""


class AgentRefused(AgentError):
    """The model declined to answer (stop_reason "refusal")."""


class AgentTruncated(AgentError):
    """The model stopped before finishing, usually because it hit max_tokens."""


class AgentTimeout(AgentError):
    """No answer arrived within the timeout, retries included."""


class AgentUnavailable(AgentError):
    """The API was unreachable, rate-limited or failing, and the retries ran out."""


class PromptError(DomainError):
    """A prompt file couldn't be loaded. Says which file and why."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")


class PricingError(DomainError):
    """A price list couldn't be loaded, or a model has no price."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
