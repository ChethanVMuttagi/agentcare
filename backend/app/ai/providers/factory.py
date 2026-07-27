"""Construct the configured `LLMProvider` from `Settings`.

The one place that knows how to turn `LLM_PROVIDER`/`LLM_MODEL`/
`LLM_API_KEY`/`LLM_TIMEOUT_SECONDS`/`LLM_MAX_OUTPUT_TOKENS` into a real
provider instance. A FastAPI dependency (see
`app.api.v1.endpoints.agent`) calls this per-request; tests override the
dependency entirely with `FakeLLMProvider` (see
`app.ai.providers.fake_provider`) — this factory is never exercised
by the test suite's normal path, only by the opt-in real-provider smoke
test.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import LLMProvider
from app.ai.providers.errors import ProviderConfigurationError
from app.core.config import Settings, get_settings

_SUPPORTED_PROVIDERS = ("anthropic",)


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Build the configured `LLMProvider`.

    Raises `ProviderConfigurationError` (500) if `LLM_PROVIDER`,
    `LLM_MODEL`, or `LLM_API_KEY` is missing, or `LLM_PROVIDER` names a
    provider this codebase does not implement — a clear, immediate
    failure, never a silent fallback to some default provider/model.
    """
    if not settings.llm_provider:
        raise ProviderConfigurationError(
            "LLM_PROVIDER is not configured. Set LLM_PROVIDER, LLM_MODEL, and "
            "LLM_API_KEY to enable AI-assisted administrative requests."
        )
    if settings.llm_provider not in _SUPPORTED_PROVIDERS:
        raise ProviderConfigurationError(
            f"LLM_PROVIDER={settings.llm_provider!r} is not a supported provider. "
            f"Supported providers: {', '.join(_SUPPORTED_PROVIDERS)}."
        )
    if not settings.llm_model:
        raise ProviderConfigurationError("LLM_MODEL is not configured.")
    if settings.llm_api_key is None:
        raise ProviderConfigurationError("LLM_API_KEY is not configured.")

    return AnthropicProvider(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
    )


def get_llm_provider(settings: Annotated[Settings, Depends(get_settings)]) -> LLMProvider:
    """FastAPI dependency — constructs a fresh provider per request (not
    cached: cheap to construct, and tests override this dependency
    entirely with `app.ai.providers.fake_provider.FakeLLMProvider`, so
    caching would only risk pinning a stale/misconfigured instance
    across requests in a long-running process)."""
    return build_llm_provider(settings)
