"""Environment-dependent readiness semantics for the database dependency.

STORY-002 correction: an unconfigured database (`not_configured`) is
acceptable readiness in `development`/`test`, but NOT in `staging`/
`production` — those must report `not_ready`/503 rather than pretending
an unconfigured database is fine outside development/test. `ok` and
`unavailable` are unaffected by environment: `ok` is always ready,
`unavailable` is never ready.

Uses `app.dependency_overrides` on `get_database_readiness_check` to
simulate each database state (no real database connection needed) and
`monkeypatch` + `Settings` cache clearing to vary `APP_ENV`.

Every test also sets a synthetic `JWT_SECRET_KEY` (STORY-004: `Settings`
now requires one whenever `APP_ENV` is `staging`/`production`) and a
non-`local` `DOCUMENT_STORAGE_BACKEND` (STORY-008: `Settings` now
refuses `local` document storage whenever `APP_ENV` is
`staging`/`production` — see `app.core.config`) — both unrelated to what
this file actually tests, but required for `Settings` to construct at
all in those environments.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.health import get_database_readiness_check
from app.core.config import get_settings
from app.main import create_app
from app.schemas.common import ReadinessCheck

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    monkeypatch.setenv("DOCUMENT_STORAGE_BACKEND", "s3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def app_with_db_check() -> Callable[[ReadinessCheck], FastAPI]:
    def _make(check: ReadinessCheck) -> FastAPI:
        app = create_app()

        async def _override() -> ReadinessCheck:
            return check

        app.dependency_overrides[get_database_readiness_check] = _override
        return app

    return _make


@pytest.mark.parametrize("env", ["development", "test"])
def test_unconfigured_database_is_ready_in_development_and_test(
    env: str,
    monkeypatch: pytest.MonkeyPatch,
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    get_settings.cache_clear()
    app = app_with_db_check(ReadinessCheck(name="database", status="not_configured"))

    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["environment"] == env
    assert body["checks"] == [{"name": "database", "status": "not_configured"}]


@pytest.mark.parametrize("env", ["staging", "production"])
def test_unconfigured_database_is_not_ready_in_staging_and_production(
    env: str,
    monkeypatch: pytest.MonkeyPatch,
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    get_settings.cache_clear()
    app = app_with_db_check(ReadinessCheck(name="database", status="not_configured"))

    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["environment"] == env
    assert body["checks"] == [{"name": "database", "status": "not_configured"}]


@pytest.mark.parametrize("env", ["development", "test", "staging", "production"])
def test_reachable_database_is_ready_in_every_environment(
    env: str,
    monkeypatch: pytest.MonkeyPatch,
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    get_settings.cache_clear()
    app = app_with_db_check(ReadinessCheck(name="database", status="ok"))

    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize("env", ["development", "test", "staging", "production"])
def test_unreachable_database_is_never_ready_regardless_of_environment(
    env: str,
    monkeypatch: pytest.MonkeyPatch,
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    get_settings.cache_clear()
    app = app_with_db_check(ReadinessCheck(name="database", status="unavailable"))

    response = TestClient(app).get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


@pytest.mark.parametrize("env", ["development", "test", "staging", "production"])
def test_health_stays_200_regardless_of_database_readiness(
    env: str,
    monkeypatch: pytest.MonkeyPatch,
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    # /health is liveness and must remain independent of database
    # readiness, even when the database is unreachable.
    monkeypatch.setenv("APP_ENV", env)
    get_settings.cache_clear()
    app = app_with_db_check(ReadinessCheck(name="database", status="unavailable"))

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_response_never_leaks_connection_details_in_production(
    monkeypatch: pytest.MonkeyPatch,
    app_with_db_check: Callable[[ReadinessCheck], FastAPI],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    app = app_with_db_check(ReadinessCheck(name="database", status="not_configured"))

    response = TestClient(app).get("/api/v1/ready")

    body_text = response.text.lower()
    for leaked in ("password", "asyncpg", "postgresql://", "traceback", "changeme", "@localhost"):
        assert leaked not in body_text
