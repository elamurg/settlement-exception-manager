import pytest

from settlement.agents.client import DEFAULT_MODEL, Message, ModelClient, ModelRequest
from settlement.agents.errors import AgentTimeout
from settlement.agents.fake import FakeModelClient, reply

REQUEST = ModelRequest(
    agent="test",
    model=DEFAULT_MODEL,
    system="Be brief.",
    messages=(Message(role="user", text="Hi"),),
    schema={"type": "object"},
    effort="low",
    max_tokens=100,
)


def test_the_fake_answers_in_order_and_remembers_what_it_was_asked() -> None:
    fake = FakeModelClient(reply("first"), reply("second"))

    assert fake.complete(REQUEST).text == "first"
    assert fake.complete(REQUEST).text == "second"
    assert fake.requests == [REQUEST, REQUEST]


def test_the_fake_raises_a_scripted_error() -> None:
    fake = FakeModelClient(AgentTimeout("test", "too slow"))

    with pytest.raises(AgentTimeout):
        fake.complete(REQUEST)


def test_the_fake_fails_loudly_when_called_too_often() -> None:
    fake = FakeModelClient()

    with pytest.raises(AssertionError, match="more times"):
        fake.complete(REQUEST)


def test_the_fake_fits_the_model_client_interface() -> None:
    client: ModelClient = FakeModelClient(reply("ok"))  # mypy checks this line

    assert client.complete(REQUEST).text == "ok"
