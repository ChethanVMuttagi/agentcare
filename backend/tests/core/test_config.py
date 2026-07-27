import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings


def test_defaults_are_safe_for_startup() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_env is Environment.DEVELOPMENT
    assert settings.debug is False
    assert settings.database_url is None
    assert settings.llm_provider is None
    assert settings.llm_model is None
    assert settings.llm_api_key is None
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
            document_storage_backend="s3",
        )


def test_production_debug_defaults_false() -> None:
    settings = Settings(
        _env_file=None,
        app_env=Environment.PRODUCTION,
        jwt_secret_key="synthetic-test-secret-value",
        document_storage_backend="s3",
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
            _env_file=None,
            app_env=env,
            jwt_secret_key="synthetic-test-secret-value",
            document_storage_backend="s3",
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


def test_defaults_include_local_document_storage() -> None:
    settings = Settings(_env_file=None)
    assert settings.document_storage_backend == "local"
    assert settings.document_storage_path == "local_storage/documents"
    assert settings.document_max_upload_bytes == 10 * 1024 * 1024


def test_staging_or_production_with_local_document_storage_is_rejected() -> None:
    for env in (Environment.STAGING, Environment.PRODUCTION):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                app_env=env,
                jwt_secret_key="synthetic-test-secret-value",
                document_storage_backend="local",
            )


def test_staging_or_production_with_non_local_document_storage_is_accepted() -> None:
    for env in (Environment.STAGING, Environment.PRODUCTION):
        settings = Settings(
            _env_file=None,
            app_env=env,
            jwt_secret_key="synthetic-test-secret-value",
            document_storage_backend="s3",
        )
        assert settings.document_storage_backend == "s3"


def test_development_and_test_allow_local_document_storage() -> None:
    for env in (Environment.DEVELOPMENT, Environment.TEST):
        settings = Settings(_env_file=None, app_env=env)
        assert settings.document_storage_backend == "local"


def test_llm_defaults_are_safe_for_startup_with_no_key_configured() -> None:
    settings = Settings(_env_file=None)
    assert settings.llm_provider is None
    assert settings.llm_model is None
    assert settings.llm_api_key is None
    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_output_tokens == 1024


def test_llm_api_key_is_masked_in_repr() -> None:
    settings = Settings(_env_file=None, llm_api_key="synthetic-test-llm-key")
    assert "synthetic-test-llm-key" not in repr(settings)
    assert "synthetic-test-llm-key" not in str(settings)


def test_llm_timeout_must_be_positive_and_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout_seconds=-5)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout_seconds=1000)


def test_llm_max_output_tokens_must_be_positive_and_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_max_output_tokens=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_max_output_tokens=-1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_max_output_tokens=100_000)


def test_llm_provider_and_model_reject_blank_strings() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="   ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_model="   ")


def test_llm_provider_can_be_configured_without_a_database() -> None:
    # The application (and this Settings model) must start fine with an
    # LLM provider configured but no database — the two are independent.
    settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        llm_model="claude-fake-model",
        llm_api_key="synthetic-test-llm-key",
    )
    assert settings.llm_provider == "anthropic"
    assert settings.llm_model == "claude-fake-model"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "synthetic-test-llm-key"
