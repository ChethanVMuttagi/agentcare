"""Construct the configured `LLMProvider` from `Settings`.

The one place that knows how to turn `LLM_PROVIDER`/`LLM_MODEL`/
`LLM_API_KEY`/`LLM_TIMEOUT_SECONDS`/`LLM_MAX_OUTPUT_TOKENS` into a real
provider instance. Tests override the `get_llm_provider` FastAPI
dependency entirely with `FakeLLMProvider` (see
`app.ai.providers.fake_provider`) — this factory is never exercised
by the test suite's normal path, only by the opt-in real-provider smoke
test.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import LLMProvider
from app.ai.providers.errors import ProviderConfigurationError
from app.ai.providers.groq_provider import GroqProvider
from app.core.config import Settings, get_settings

_SUPPORTED_PROVIDERS = ("anthropic", "groq")


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

    if settings.llm_provider == "groq":
        return GroqProvider(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
        )

    return AnthropicProvider(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
    )


@lru_cache
def get_llm_provider() -> LLMProvider:
    """FastAPI dependency AND the process-wide shared-provider accessor
    (Sprint 2) — returns the SAME `LLMProvider` (and its underlying HTTP
    client) for the lifetime of the process, building it on first call.
    Mirrors `app.db.session.get_engine`'s exact lazy-singleton pattern:
    a plain `@lru_cache`d, zero-argument function that reads `Settings`
    itself (`Settings` isn't hashable, so it can't be an `lru_cache`d
    function's parameter) rather than receiving it via `Depends`.

    Raises `ProviderConfigurationError` if LLM configuration is
    absent/incomplete — NOT cached, since `functools.lru_cache` never
    caches an exception, so a later call (once configuration is fixed,
    or in a differently-configured process) re-attempts construction
    rather than permanently remembering the failure. Tests override this
    dependency entirely with `app.ai.providers.fake_provider.FakeLLMProvider`
    and never call this function directly, so caching has no effect on
    the test suite's normal path — see `close_llm_provider` for the
    matching shutdown-time cleanup.
    """
    return build_llm_provider(get_settings())


async def close_llm_provider() -> None:
    """Release the shared provider's underlying HTTP client, if one was
    ever built. Safe to call even when none was (e.g. LLM not
    configured, or in tests) — it will not build one just to tear it
    down. Intended for application shutdown — see `app.main.lifespan`."""
    if get_llm_provider.cache_info().currsize > 0:
        await get_llm_provider().aclose()
    get_llm_provider.cache_clear()
