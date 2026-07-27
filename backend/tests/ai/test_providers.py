"""`app.ai.providers` tests — no database, no network, no real API key.

Covers `FakeLLMProvider` (the deterministic test double every other test
in this codebase uses instead of a real network call) and
`build_llm_provider`'s configuration validation.
"""

from __future__ import annotations

import pytest

from app.ai.decisions import RefusalCategory, RefusalDecision
from app.ai.providers.base import StructuredCompletionRequest
from app.ai.providers.errors import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.ai.providers.factory import build_llm_provider
from app.ai.providers.fake_provider import AlwaysUnavailableFakeLLMProvider, FakeLLMProvider
from app.core.config import Settings

_REQUEST = StructuredCompletionRequest(system_prompt="system", user_content="book an appointment")


async def test_fake_provider_returns_configured_decision() -> None:
    decision = RefusalDecision(
        reason_category=RefusalCategory.OUT_OF_SCOPE, safe_message="Not supported."
    )
    provider = FakeLLMProvider(decision=decision)

    result = await provider.generate_structured(_REQUEST)

    assert result is decision
    assert provider.calls == [_REQUEST]


async def test_fake_provider_parses_valid_raw_response() -> None:
    provider = FakeLLMProvider(
        raw_response={"kind": "safe_response", "message": "All set."}
    )
    result = await provider.generate_structured(_REQUEST)
    assert result.kind.value == "safe_response"


async def test_fake_provider_translates_malformed_raw_response_to_provider_response_error() -> (
    None
):
    provider = FakeLLMProvider(raw_response={"kind": "run_sql", "query": "DROP TABLE users"})
    with pytest.raises(ProviderResponseError):
        await provider.generate_structured(_REQUEST)


async def test_fake_provider_rejects_raw_response_with_chain_of_thought_field() -> None:
    provider = FakeLLMProvider(
        raw_response={
            "kind": "safe_response",
            "message": "Done.",
            "chain_of_thought": "step by step reasoning",
        }
    )
    with pytest.raises(ProviderResponseError):
        await provider.generate_structured(_REQUEST)


async def test_fake_provider_raises_configured_error() -> None:
    provider = FakeLLMProvider(error=ProviderTimeoutError("timed out"))
    with pytest.raises(ProviderTimeoutError):
        await provider.generate_structured(_REQUEST)


async def test_fake_provider_requires_exactly_one_configured_outcome() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        FakeLLMProvider()
    with pytest.raises(ValueError, match="exactly one"):
        FakeLLMProvider(
            decision=RefusalDecision(
                reason_category=RefusalCategory.OUT_OF_SCOPE, safe_message="No."
            ),
            error=ProviderTimeoutError("x"),
        )


async def test_always_unavailable_fake_provider_raises_provider_unavailable() -> None:
    provider = AlwaysUnavailableFakeLLMProvider()
    with pytest.raises(ProviderUnavailableError):
        await provider.generate_structured(_REQUEST)


# --- build_llm_provider (configuration validation) ---


def test_build_llm_provider_requires_provider_configured() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ProviderConfigurationError):
        build_llm_provider(settings)


def test_build_llm_provider_rejects_unsupported_provider() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="groq",
        llm_model="some-model",
        llm_api_key="synthetic-test-key",
    )
    with pytest.raises(ProviderConfigurationError, match="not a supported provider"):
        build_llm_provider(settings)


def test_build_llm_provider_requires_model() -> None:
    settings = Settings(
        _env_file=None, llm_provider="anthropic", llm_api_key="synthetic-test-key"
    )
    with pytest.raises(ProviderConfigurationError):
        build_llm_provider(settings)


def test_build_llm_provider_requires_api_key() -> None:
    settings = Settings(
        _env_file=None, llm_provider="anthropic", llm_model="claude-fake-model"
    )
    with pytest.raises(ProviderConfigurationError):
        build_llm_provider(settings)


def test_build_llm_provider_succeeds_with_full_configuration() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        llm_model="claude-fake-model",
        llm_api_key="synthetic-test-key",
    )
    provider = build_llm_provider(settings)
    assert provider is not None


def test_provider_configuration_error_never_contains_the_api_key() -> None:
    settings = Settings(
        _env_file=None, llm_provider="anthropic", llm_api_key="synthetic-super-secret-key-value"
    )
    try:
        build_llm_provider(settings)
    except ProviderConfigurationError as exc:
        assert "synthetic-super-secret-key-value" not in exc.message
    else:
        pytest.fail("expected ProviderConfigurationError")
