from fastapi import FastAPI

from app.core.config import get_settings
from app.main import create_app


def test_create_app_returns_a_fastapi_instance() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)


def test_create_app_uses_settings_for_metadata() -> None:
    settings = get_settings()
    app = create_app()
    assert app.title == settings.app_name
    assert app.version == settings.app_version
