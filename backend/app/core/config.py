"""Typed application configuration.

Settings are sourced from environment variables (and a local `.env` file in
development). Secret-bearing fields use ``SecretStr`` so their values are
masked in ``repr()``/``str()`` output and cannot be casually leaked through
logs. See SECURITY.md for the full secret-management policy.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment. Used instead of scattered string comparisons."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "AgentCare"
    app_version: str = "0.1.0"
    app_env: Environment = Environment.DEVELOPMENT
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Database ------------------------------------------------------------
    # Optional at startup: no database is wired up until a later story.
    database_url: SecretStr | None = None

    # --- LLM provider ----------------------------------------------------------
    # Optional at startup: no LLM integration exists until a later story.
    llm_provider: str | None = None
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # --- Auth --------------------------------------------------------------------
    # Optional in development/test only — see `_require_jwt_secret_outside_development`.
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # --- Frontend ------------------------------------------------------------------
    frontend_url: str | None = None

    @model_validator(mode="after")
    def _forbid_debug_in_production(self) -> Settings:
        if self.app_env is Environment.PRODUCTION and self.debug:
            raise ValueError("DEBUG must not be enabled when APP_ENV=production.")
        return self

    @model_validator(mode="after")
    def _require_jwt_secret_outside_development(self) -> Settings:
        if (
            self.app_env in (Environment.STAGING, Environment.PRODUCTION)
            and self.jwt_secret_key is None
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be set when APP_ENV is staging or production — "
                "the application must not silently sign/verify tokens without a real secret."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance for use as a FastAPI dependency."""
    return Settings()
