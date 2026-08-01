"""`app.ai.providers.groq_provider.GroqProvider` tests — real request/
response mapping and error translation, against a mocked HTTP transport
(`httpx.MockTransport`). No real network call, no real API key. Mirrors
the level of scrutiny `app.ai.providers.errors` expects of every
provider: a vendor-shaped failure in, one of the controlled exceptions
out, never a raw vendor exception or its text.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai.providers.base import StructuredCompletionRequest
from app.ai.providers.errors import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.ai.providers.groq_provider import GroqProvider

_REQUEST = StructuredCompletionRequest(
    system_prompt="You are a coordinator.", user_content="Book me an appointment."
)


def _provider() -> GroqProvider:
    return GroqProvider(
        api_key="test-key", model="test-model", timeout_seconds=5, max_output_tokens=100
    )


def _install_transport(provider: GroqProvider, handler) -> None:
    provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer test-key"}
    )


def _tool_call_response(arguments: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"arguments": json.dumps(arguments)}},
                        ]
                    }
                }
            ]
        },
    )


async def test_generate_structured_success() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response(
            {"decision": {"kind": "safe_response", "message": "Here is your summary."}}
        )

    _install_transport(provider, handler)

    decision = await provider.generate_structured(_REQUEST)

    assert decision.kind.value == "safe_response"
    assert decision.message == "Here is your summary."


async def test_generate_coordinator_decision_success() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response(
            {"decision": {"kind": "handoff", "target_agent": "scheduling"}}
        )

    _install_transport(provider, handler)

    decision = await provider.generate_coordinator_decision(_REQUEST)

    assert decision.kind.value == "handoff"


async def test_request_body_shape() -> None:
    """Confirms the OpenAI-compatible tool-calling shape actually sent —
    forced tool_choice, system+user messages, tools array."""
    provider = _provider()
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tool_call_response({"decision": {"kind": "safe_response", "message": "ok"}})

    _install_transport(provider, handler)
    await provider.generate_structured(_REQUEST)

    body = captured["body"]
    assert body["model"] == "test-model"
    assert body["messages"][0] == {"role": "system", "content": "You are a coordinator."}
    assert body["messages"][1] == {"role": "user", "content": "Book me an appointment."}
    assert body["tool_choice"]["function"]["name"] == "record_administrative_decision"
    assert body["tools"][0]["function"]["name"] == "record_administrative_decision"


async def test_unauthorized_maps_to_configuration_error() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    _install_transport(provider, handler)

    with pytest.raises(ProviderConfigurationError):
        await provider.generate_structured(_REQUEST)


async def test_server_error_maps_to_unavailable() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "internal error"}})

    _install_transport(provider, handler)

    with pytest.raises(ProviderUnavailableError):
        await provider.generate_structured(_REQUEST)


async def test_timeout_maps_to_timeout_error() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("synthetic timeout", request=request)

    _install_transport(provider, handler)

    with pytest.raises(ProviderTimeoutError):
        await provider.generate_structured(_REQUEST)


async def test_missing_tool_call_maps_to_response_error() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {}}]})

    _install_transport(provider, handler)

    with pytest.raises(ProviderResponseError):
        await provider.generate_structured(_REQUEST)


async def test_malformed_decision_maps_to_response_error() -> None:
    provider = _provider()

    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response({"decision": {"kind": "not_a_real_kind"}})

    _install_transport(provider, handler)

    with pytest.raises(ProviderResponseError):
        await provider.generate_structured(_REQUEST)


def test_construction_requires_api_key() -> None:
    with pytest.raises(ProviderConfigurationError):
        GroqProvider(api_key="", model="test-model", timeout_seconds=5, max_output_tokens=100)


def test_construction_requires_model() -> None:
    with pytest.raises(ProviderConfigurationError):
        GroqProvider(api_key="test-key", model="", timeout_seconds=5, max_output_tokens=100)
