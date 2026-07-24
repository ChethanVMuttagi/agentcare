import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_engine
from app.main import create_app


def test_create_app_returns_a_fastapi_instance() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)


def test_create_app_uses_settings_for_metadata() -> None:
    settings = get_settings()
    app = create_app()
    assert app.title == settings.app_name
    assert app.version == settings.app_version


def test_lifespan_disposes_database_engine_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            # Exercise readiness so the database engine actually gets created.
            client.get("/api/v1/ready")
            assert get_engine.cache_info().currsize == 1
        # Exiting the `with` block runs lifespan shutdown, which must
        # dispose the engine rather than leaving it dangling.
        assert get_engine.cache_info().currsize == 0
    finally:
        get_settings.cache_clear()
        get_engine.cache_clear()
