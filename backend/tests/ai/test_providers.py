"""`app.ai.providers` tests — no database, no network, no real API key.

Covers `FakeLLMProvider` (the deterministic test double every other test
in this codebase uses instead of a real network call) and
`build_llm_provider`'s configuration validation.
"""

from __future__ import annotations

import pytest

from app.ai.coordinator_decisions import CoordinatorRefusalDecision, HandoffDecision, TargetAgent
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


async def test_fake_provider_allows_zero_configured_specialist_outcomes() -> None:
    """STORY-011: a `FakeLLMProvider` that only needs to drive a
    Coordinator-side test (see `test_fake_provider_coordinator_*` below)
    may leave the specialist trio entirely unconfigured — calling
    `generate_structured` in that case is a test-authoring bug, not a
    valid outcome, so it raises `AssertionError` rather than silently
    returning something."""
    provider = FakeLLMProvider()
    with pytest.raises(AssertionError):
        await provider.generate_structured(_REQUEST)


async def test_fake_provider_rejects_more_than_one_configured_specialist_outcome() -> None:
    with pytest.raises(ValueError, match="at most one"):
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
    with pytest.raises(ProviderUnavailableError):
        await provider.generate_coordinator_decision(_REQUEST)


# --- FakeLLMProvider: coordinator side (STORY-011) ---


async def test_fake_provider_returns_configured_coordinator_decision() -> None:
    decision = HandoffDecision(target_agent=TargetAgent.SCHEDULING)
    provider = FakeLLMProvider(coordinator_decision=decision)

    result = await provider.generate_coordinator_decision(_REQUEST)

    assert result is decision
    assert provider.coordinator_calls == [_REQUEST]


async def test_fake_provider_parses_valid_coordinator_raw_response() -> None:
    provider = FakeLLMProvider(
        coordinator_raw_response={"kind": "handoff", "target_agent": "document"}
    )
    result = await provider.generate_coordinator_decision(_REQUEST)
    assert result.kind.value == "handoff"


async def test_fake_provider_rejects_coordinator_raw_response_shaped_as_tool_call() -> None:
    """The structural guarantee: a `CoordinatorDecision` has no
    `tool_call` variant at all — a provider response shaped like one
    fails validation exactly like an unrecognized `kind` would."""
    provider = FakeLLMProvider(
        coordinator_raw_response={
            "kind": "tool_call",
            "tool_name": "book_appointment",
            "arguments": {},
        }
    )
    with pytest.raises(ProviderResponseError):
        await provider.generate_coordinator_decision(_REQUEST)


async def test_fake_provider_coordinator_and_specialist_sides_are_independent() -> None:
    """A single instance can drive both halves of one handoff test."""
    provider = FakeLLMProvider(
        decision=RefusalDecision(
            reason_category=RefusalCategory.OUT_OF_SCOPE, safe_message="specialist"
        ),
        coordinator_decision=CoordinatorRefusalDecision(
            reason_category=RefusalCategory.OUT_OF_SCOPE, safe_message="coordinator"
        ),
    )
    coordinator_result = await provider.generate_coordinator_decision(_REQUEST)
    specialist_result = await provider.generate_structured(_REQUEST)
    assert coordinator_result.safe_message == "coordinator"
    assert specialist_result.safe_message == "specialist"
    assert len(provider.coordinator_calls) == 1
    assert len(provider.calls) == 1


async def test_fake_provider_rejects_more_than_one_configured_coordinator_outcome() -> None:
    with pytest.raises(ValueError, match="at most one"):
        FakeLLMProvider(
            coordinator_decision=HandoffDecision(target_agent=TargetAgent.SCHEDULING),
            coordinator_error=ProviderTimeoutError("x"),
        )


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
