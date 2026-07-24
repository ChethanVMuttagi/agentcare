"""Readiness endpoint behavior with respect to the database dependency.

Uses FastAPI dependency overrides on `get_database_readiness_check` to
simulate each database state without needing a real database connection —
this specifically exercises the /api/v1/ready contract (status codes,
response shape, no leaked connection details), not SQLAlchemy itself
(that's covered in tests/db/).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.health import get_database_readiness_check
from app.main import create_app
from app.schemas.common import ReadinessCheck


@pytest.fixture()
def app_with_db_check() -> Callable[[ReadinessCheck], FastAPI]:
    def _make(check: ReadinessCheck) -> FastAPI:
        app = create_app()

        async def _override() -> ReadinessCheck:
            return check

        app.dependency_overrides[get_database_readiness_check] = _override
        return app

    return _make


def test_readiness_is_ready_when_database_not_configured(
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    app = app_with_db_check(ReadinessCheck(name="database", status="not_configured"))
    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == [{"name": "database", "status": "not_configured"}]


def test_readiness_is_ready_when_database_ok(
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    app = app_with_db_check(ReadinessCheck(name="database", status="ok"))
    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_is_not_ready_when_database_unavailable(
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    app = app_with_db_check(ReadinessCheck(name="database", status="unavailable"))
    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == [{"name": "database", "status": "unavailable"}]


def test_readiness_response_never_leaks_connection_details(
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    app = app_with_db_check(ReadinessCheck(name="database", status="unavailable"))
    response = TestClient(app).get("/api/v1/ready")

    body_text = response.text.lower()
    for leaked in ("password", "asyncpg", "postgresql://", "traceback", "changeme", "@localhost"):
        assert leaked not in body_text
