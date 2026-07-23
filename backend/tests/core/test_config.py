import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings


def test_defaults_are_safe_for_startup() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_env is Environment.DEVELOPMENT
    assert settings.debug is False
    assert settings.database_url is None
    assert settings.groq_api_key is None
    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.jwt_secret_key is None


def test_app_starts_without_any_llm_or_database_credentials() -> None:
    # No env vars set: this must not raise, since STORY-001 has no DB/LLM
    # integration and the app must be able to start for health checks/tests.
    settings = Settings(_env_file=None)
    assert settings.app_name == "AgentCare"


def test_production_cannot_enable_debug() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env=Environment.PRODUCTION, debug=True)


def test_production_debug_defaults_false() -> None:
    settings = Settings(_env_file=None, app_env=Environment.PRODUCTION)
    assert settings.debug is False


def test_secret_fields_are_masked_in_repr() -> None:
    settings = Settings(_env_file=None, jwt_secret_key="synthetic-test-secret-value")
    assert "synthetic-test-secret-value" not in repr(settings)
    assert "synthetic-test-secret-value" not in str(settings)
