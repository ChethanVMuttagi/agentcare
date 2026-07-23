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


def test_readiness_returns_ok_with_no_faked_dependencies(client: TestClient) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    # No database or external dependency is wired up yet in STORY-001, so
    # readiness must not claim one is healthy.
    assert body["checks"] == []
