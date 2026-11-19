import pytest

from settlement.agents.errors import (
    AgentError,
    AgentRefused,
    AgentTimeout,
    AgentTruncated,
    AgentUnavailable,
    InvalidAgentOutput,
    PricingError,
    PromptError,
)
from settlement.domain.errors import DomainError


@pytest.mark.parametrize(
    "error_class",
    [InvalidAgentOutput, AgentRefused, AgentTruncated, AgentTimeout, AgentUnavailable],
)
def test_agent_errors_are_domain_errors_that_name_the_agent(error_class: type[AgentError]) -> None:
    error = error_class("classifier", "something went wrong")

    assert isinstance(error, DomainError)
    assert error.agent == "classifier"
    assert str(error) == "classifier: something went wrong"


def test_prompt_and_pricing_errors_name_the_file() -> None:
    assert (
        str(PromptError("classifier/v1.md", "file is empty")) == "classifier/v1.md: file is empty"
    )
    assert str(PricingError("v1.yaml", "no price")) == "v1.yaml: no price"
    assert isinstance(PromptError("a", "b"), DomainError)
    assert isinstance(PricingError("a", "b"), DomainError)
