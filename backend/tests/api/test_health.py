from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    body = response.json()
    settings = get_settings()
    assert body == {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env.value,
        "version": settings.app_version,
    }


def test_readiness_is_ready_when_no_database_is_configured(app: FastAPI) -> None:
    # An unconfigured DATABASE_URL must not fail readiness (see
    # docs/DATABASE.md) or fake a connection — it must be truthfully
    # reported as "not_configured".
    #
    # `get_settings` is overridden with a genuinely-unconfigured Settings
    # rather than trusting the ambient environment to lack DATABASE_URL:
    # CI exports it (for `alembic upgrade head`) and most developers have
    # it in `backend/.env`, either of which would otherwise fail this test
    # for a reason that has nothing to do with the behavior it checks.
    # Overriding the dependency is the mechanism `get_database_readiness_check`
    # itself documents for exactly this (see its docstring).
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, database_url=None)
    try:
        response = TestClient(app).get("/api/v1/ready")
        assert response.status_code == 200

        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"] == [{"name": "database", "status": "not_configured"}]
    finally:
        app.dependency_overrides.pop(get_settings, None)
