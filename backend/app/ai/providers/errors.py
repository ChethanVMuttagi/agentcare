"""Controlled provider-failure exceptions.

Every failure mode an `LLMProvider` can hit — misconfiguration,
timeout, a malformed/unparseable response, or an unexpected vendor
exception — is translated into exactly one of these before it ever
leaves `app.ai.providers`. Nothing upstream (the orchestration service,
the API route) ever sees a raw vendor SDK exception, its `repr()`, or
any text that could contain connection details or request internals —
see docs/AI_SAFETY.md "Failure Behavior".
"""

from __future__ import annotations

from app.core.exceptions import AppException


class ProviderConfigurationError(AppException):
    """500: the configured provider is missing required configuration
    (no API key, no model, unknown provider name) — a deployment/
    configuration problem, not a runtime one. Raised at provider
    construction time, not per-request, wherever practical."""

    status_code = 500
    error_code = "llm_provider_not_configured"


class ProviderUnavailableError(AppException):
    """503: the provider could not be reached, or rejected the request
    for a transient/infrastructure reason (connection error, rate
    limit, server-side 5xx). The vendor's own error text is never
    included in `message` — only a safe, generic description."""

    status_code = 503
    error_code = "llm_provider_unavailable"


class ProviderTimeoutError(AppException):
    """504: the provider did not respond within `LLM_TIMEOUT_SECONDS`."""

    status_code = 504
    error_code = "llm_provider_timeout"


class ProviderResponseError(AppException):
    """502: the provider responded, but its output could not be parsed
    into a valid `AdministrativeDecision` (malformed JSON, a decision
    `kind` this codebase doesn't recognize, or a schema violation).
    Never includes the raw provider response text in `message`."""

    status_code = 502
    error_code = "llm_provider_invalid_response"
