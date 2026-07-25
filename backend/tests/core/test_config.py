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
        Settings(
            _env_file=None,
            app_env=Environment.PRODUCTION,
            debug=True,
            jwt_secret_key="synthetic-test-secret-value",
        )


def test_production_debug_defaults_false() -> None:
    settings = Settings(
        _env_file=None,
        app_env=Environment.PRODUCTION,
        jwt_secret_key="synthetic-test-secret-value",
    )
    assert settings.debug is False


def test_secret_fields_are_masked_in_repr() -> None:
    settings = Settings(_env_file=None, jwt_secret_key="synthetic-test-secret-value")
    assert "synthetic-test-secret-value" not in repr(settings)
    assert "synthetic-test-secret-value" not in str(settings)


def test_staging_or_production_without_jwt_secret_key_is_rejected() -> None:
    for env in (Environment.STAGING, Environment.PRODUCTION):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, app_env=env)


def test_staging_or_production_with_jwt_secret_key_is_accepted() -> None:
    for env in (Environment.STAGING, Environment.PRODUCTION):
        settings = Settings(
            _env_file=None, app_env=env, jwt_secret_key="synthetic-test-secret-value"
        )
        assert settings.app_env is env


def test_development_and_test_do_not_require_jwt_secret_key() -> None:
    for env in (Environment.DEVELOPMENT, Environment.TEST):
        settings = Settings(_env_file=None, app_env=env)
        assert settings.jwt_secret_key is None


def test_jwt_defaults_are_reasonable() -> None:
    settings = Settings(_env_file=None)
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_access_token_expire_minutes == 30
