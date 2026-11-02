"""Shared fixtures: where the databases are, for the tests that need a real one.

Locally, a test whose database isn't configured is skipped, so the rest of the suite runs
without docker compose. CI sets REQUIRE_SERVICES, which turns that skip into a failure:
there, a missing URL means the pipeline is broken, and a skip would hide it.
"""

import os

import pytest


def _url(name: str, service: str) -> str:
    url = os.environ.get(name)
    if url:
        return url
    message = f"{name} not set; start {service} with docker compose to run this"
    if os.environ.get("REQUIRE_SERVICES"):
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture
def database_url() -> str:
    return _url("DATABASE_URL", "Postgres")


@pytest.fixture
def mongo_url() -> str:
    return _url("MONGO_URL", "MongoDB")
