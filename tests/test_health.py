import os

import pytest
from fastapi.testclient import TestClient

from settlement.api.app import app

client = TestClient(app)


def test_health_ok_when_database_reachable() -> None:
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set; start Postgres with docker compose to run this")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_unavailable_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 1 refuses connections, so this fails fast without a real database.
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/none")

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "unreachable"}


def test_health_unavailable_when_database_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = client.get("/health")

    assert response.status_code == 503
