"""`app.ai.providers` tests — no database, no network, no real API key.

Covers `FakeLLMProvider` (the deterministic test double every other test
in this codebase uses instead of a real network call) and
`build_llm_provider`'s configuration validation.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.ai.coordinator_decisions import CoordinatorRefusalDecision, HandoffDecision, TargetAgent
from app.ai.decisions import RefusalCategory, RefusalDecision
from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import StructuredCompletionRequest
from app.ai.providers.errors import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.ai.providers.factory import build_llm_provider, close_llm_provider, get_llm_provider
from app.ai.providers.fake_provider import AlwaysUnavailableFakeLLMProvider, FakeLLMProvider
from app.ai.providers.groq_provider import GroqProvider
from app.core.config import Settings, get_settings

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
        llm_provider="openai",
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
    assert isinstance(provider, AnthropicProvider)


def test_build_llm_provider_selects_groq() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="groq",
        llm_model="llama-fake-model",
        llm_api_key="synthetic-test-key",
    )
    provider = build_llm_provider(settings)
    assert isinstance(provider, GroqProvider)


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


# --- get_llm_provider / close_llm_provider (Sprint 2: shared client lifecycle) ---


@pytest.fixture(autouse=True)
def _reset_provider_caches() -> Iterator[None]:
    """`get_llm_provider` (like `get_settings`) is a process-wide
    `@lru_cache`d singleton — every test in this module that touches it
    must start and end with a clean slate, or it would leak a
    provider/HTTP client built by one test's env vars into the next."""
    get_settings.cache_clear()
    get_llm_provider.cache_clear()
    yield
    get_settings.cache_clear()
    get_llm_provider.cache_clear()


def test_get_llm_provider_returns_the_same_cached_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "llama-fake-model")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-key")

    first = get_llm_provider()
    second = get_llm_provider()

    assert first is second


def test_get_llm_provider_raises_when_unconfigured_and_does_not_cache_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`functools.lru_cache` never caches an exception — confirms a
    later, correctly-configured call still succeeds rather than
    permanently remembering the earlier failure (see
    `app.ai.providers.factory.get_llm_provider`'s docstring).

    Monkeypatches `get_settings` directly inside `app.ai.providers.factory`
    (rather than relying on no LLM env vars being set) so this test is
    correct regardless of whatever a developer's own local `backend/.env`
    happens to have configured."""
    import app.ai.providers.factory as factory_module

    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(ProviderConfigurationError):
        get_llm_provider()

    assert get_llm_provider.cache_info().currsize == 0


async def test_close_llm_provider_is_a_no_op_when_nothing_was_ever_built() -> None:
    await close_llm_provider()  # must not raise


async def test_close_llm_provider_closes_the_underlying_http_client_and_clears_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_MODEL", "llama-fake-model")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-key")

    provider = get_llm_provider()
    assert isinstance(provider, GroqProvider)
    assert provider._client.is_closed is False  # type: ignore[attr-defined]

    await close_llm_provider()

    assert provider._client.is_closed is True  # type: ignore[attr-defined]
    assert get_llm_provider.cache_info().currsize == 0
