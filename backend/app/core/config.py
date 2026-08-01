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

    # --- LLM provider (STORY-010) ------------------------------------------------
    # Optional at startup: the application (and every route that doesn't
    # touch `app.ai`) must start with no LLM configured at all — only the
    # `POST .../agent/execute` route needs a real, working provider, and
    # it fails clearly (see `app.ai.providers.errors.ProviderUnavailableError`)
    # rather than the whole app refusing to boot. See docs/AI_SAFETY.md
    # "Model/Provider Configuration".
    #
    # `llm_provider` is a plain string, not an enum: `app.ai.providers`
    # validates it against the SMALL set of providers actually
    # implemented (currently just "anthropic") at the point a provider is
    # actually constructed, not at `Settings` load time — keeping
    # `Settings` itself provider-agnostic, consistent with "the rest of
    # AgentCare must not depend directly on a vendor SDK."
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 1024

    # --- Auth --------------------------------------------------------------------
    # Optional in development/test only — see `_require_jwt_secret_outside_development`.
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # --- Frontend ------------------------------------------------------------------
    frontend_url: str | None = None

    # --- Rate limiting (Sprint 2) --------------------------------------------------
    # `slowapi`/`limits` rate-limit strings (e.g. "5/minute", "100/hour") —
    # see app.core.rate_limit. Applied to the two endpoints sensitive or
    # expensive enough to need it: POST /auth/token (credential-stuffing
    # exposure) and POST .../agent/execute (each call invokes a real,
    # billed LLM provider).
    rate_limit_auth_token: str = "5/minute"
    rate_limit_agent_execute: str = "20/minute"

    # --- Document storage (STORY-008) -----------------------------------------------
    # "local" is the only backend implemented in this story — a filesystem-backed
    # `LocalDocumentStorage` intended for local development only (see
    # docs/DOCUMENTS.md). A future story may add "s3" (or similar) for a real
    # object-storage-backed production deployment; until that exists,
    # `_forbid_local_document_storage_outside_development` below refuses to start
    # in staging/production with the local backend selected, rather than silently
    # persisting documents to a single instance's non-durable local disk.
    document_storage_backend: str = "local"
    # Relative to the backend process's working directory in development (resolved
    # to an absolute path by `app.storage.local.LocalDocumentStorage`). Covered by
    # the repository's `local_storage/` .gitignore rule — never a tracked path.
    document_storage_path: str = "local_storage/documents"
    # 10 MB default — conservative for administrative documents (identity,
    # insurance, referral, consent scans); see docs/DOCUMENTS.md "File Size".
    document_max_upload_bytes: int = 10 * 1024 * 1024

    # --- Reminder engine (STORY-013) -----------------------------------------------
    # Disabled by default — the application (and every route/test that
    # doesn't touch the reminder engine) must start with no background
    # worker running at all, the same "optional at startup" posture
    # already established for the LLM provider and document storage.
    # See docs/adr/ADR-0012-reminder-engine.md "Worker Deployment".
    reminder_worker_enabled: bool = False
    reminder_worker_batch_size: int = 20
    reminder_worker_poll_interval_seconds: float = 30.0
    # A `PROCESSING` reminder whose lock is older than this is treated as
    # abandoned (its worker crashed or was killed mid-attempt) and
    # recovered by the next poll — see
    # `app.repositories.reminder.acquire_pending`.
    reminder_worker_lock_timeout_seconds: float = 300.0

    @model_validator(mode="after")
    def _validate_reminder_worker_bounds(self) -> Settings:
        if self.reminder_worker_batch_size < 1:
            raise ValueError("REMINDER_WORKER_BATCH_SIZE must be at least 1.")
        if self.reminder_worker_poll_interval_seconds <= 0:
            raise ValueError("REMINDER_WORKER_POLL_INTERVAL_SECONDS must be positive.")
        if self.reminder_worker_lock_timeout_seconds <= 0:
            raise ValueError("REMINDER_WORKER_LOCK_TIMEOUT_SECONDS must be positive.")
        if self.reminder_worker_enabled and self.database_url is None:
            raise ValueError(
                "REMINDER_WORKER_ENABLED=true requires DATABASE_URL — the reminder "
                "worker has no durable queue to poll without a real database."
            )
        return self

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

    @model_validator(mode="after")
    def _validate_llm_bounds(self) -> Settings:
        if self.llm_provider is not None and not self.llm_provider.strip():
            raise ValueError("LLM_PROVIDER must not be blank when set.")
        if self.llm_model is not None and not self.llm_model.strip():
            raise ValueError("LLM_MODEL must not be blank when set.")
        if not (0 < self.llm_timeout_seconds <= 120):
            raise ValueError("LLM_TIMEOUT_SECONDS must be between 0 (exclusive) and 120 seconds.")
        if not (0 < self.llm_max_output_tokens <= 8192):
            raise ValueError("LLM_MAX_OUTPUT_TOKENS must be between 1 and 8192.")
        return self

    @model_validator(mode="after")
    def _validate_rate_limits(self) -> Settings:
        if not self.rate_limit_auth_token.strip():
            raise ValueError("RATE_LIMIT_AUTH_TOKEN must not be blank.")
        if not self.rate_limit_agent_execute.strip():
            raise ValueError("RATE_LIMIT_AGENT_EXECUTE must not be blank.")
        return self

    @model_validator(mode="after")
    def _forbid_local_document_storage_outside_development(self) -> Settings:
        if (
            self.app_env in (Environment.STAGING, Environment.PRODUCTION)
            and self.document_storage_backend == "local"
        ):
            raise ValueError(
                "DOCUMENT_STORAGE_BACKEND=local must not be used when APP_ENV is "
                "staging or production — local filesystem storage is not durable "
                "or shared across instances. Configure a real object-storage "
                "backend before deploying document upload functionality; the "
                "application refuses to start with an inappropriate storage "
                "configuration rather than silently persisting documents "
                "somewhere unsuitable for production. See docs/DOCUMENTS.md."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance for use as a FastAPI dependency."""
    return Settings()
