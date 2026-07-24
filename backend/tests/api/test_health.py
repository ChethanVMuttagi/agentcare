from fastapi.testclient import TestClient

from app.core.config import get_settings


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


def test_readiness_is_ready_when_no_database_is_configured(client: TestClient) -> None:
    # The default test environment has no DATABASE_URL configured. That
    # must not fail readiness (see docs/DATABASE.md) or fake a connection
    # — it must be truthfully reported as "not_configured".
    response = client.get("/api/v1/ready")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == [{"name": "database", "status": "not_configured"}]
