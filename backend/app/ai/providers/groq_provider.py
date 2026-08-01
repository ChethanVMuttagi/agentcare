"""`GroqProvider`: a second `LLMProvider` implementation, backed by
Groq's OpenAI-compatible chat completions API.

Mirrors `app.ai.providers.anthropic_provider.AnthropicProvider` exactly
in shape and responsibilities — same constructor signature, same two
methods, same controlled-exception boundary (see
`app.ai.providers.errors`) — so `app.ai.providers.factory` can select
between them purely on `LLM_PROVIDER`, with zero change anywhere else
in the codebase (orchestration, tools, API layer all depend only on the
`LLMProvider` protocol in `app.ai.providers.base`). See
docs/adr/ADR-0010-llm-and-tool-security-boundary.md "Provider
Abstraction".

Uses `httpx` directly (already a transitive dependency of the
`anthropic` SDK, and now declared as a direct one — see
`pyproject.toml`) rather than adding a `groq`/`openai` SDK dependency:
Groq's OpenAI-compatible endpoint accepts a plain JSON POST, so no
vendor SDK is needed for the one call shape this provider makes.
Structured output is obtained via OpenAI-style forced tool-calling: a
single function whose parameters schema IS the target decision type's
schema, with `tool_choice` forced to it — same technique
`AnthropicProvider` uses, same reason (the model has no way to respond
with free-form prose instead of a schema-conformant decision).

This is the ONLY module in this codebase that talks to Groq's API.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.ai.coordinator_decisions import (
    CoordinatorDecision,
    coordinator_decision_tool_schema,
    parse_coordinator_decision,
)
from app.ai.decisions import AdministrativeDecision, decision_tool_schema, parse_decision
from app.ai.providers.base import StructuredCompletionRequest
from app.ai.providers.errors import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

_GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
_DECISION_TOOL_NAME = "record_administrative_decision"
_COORDINATOR_DECISION_TOOL_NAME = "record_coordinator_decision"


def _decision_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _DECISION_TOOL_NAME,
            "description": (
                "Record your administrative decision. Call this exactly once with "
                "your decision — do not respond with any other text."
            ),
            "parameters": decision_tool_schema(),
        },
    }


def _coordinator_decision_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _COORDINATOR_DECISION_TOOL_NAME,
            "description": (
                "Record your coordination decision — which specialist should handle "
                "this request, or that clarification/refusal is needed. Call this "
                "exactly once — do not respond with any other text. You cannot "
                "request a tool or take any direct action yourself."
            ),
            "parameters": coordinator_decision_tool_schema(),
        },
    }


class GroqProvider:
    """`LLMProvider` implementation backed by Groq's OpenAI-compatible
    REST API, called directly via `httpx` (no vendor SDK).

    Sprint 2: constructed ONCE for the process lifetime — see
    `app.ai.providers.factory.get_llm_provider` (a shared, cached
    singleton, no longer built fresh per request — previously this
    class's own `httpx.AsyncClient` was silently leaked on every
    request, since nothing ever called `aclose()` on it) and `aclose()`
    below, called exactly once at application shutdown
    (`app.main.lifespan`). `api_key` is a plain `str` here — the caller
    (see `app.ai.providers.factory`) is responsible for extracting it
    from a `SecretStr` exactly once, at the point of construction; this
    class never logs it and never includes it in any exception message.
    """

    def __init__(
        self, *, api_key: str, model: str, timeout_seconds: float, max_output_tokens: int
    ) -> None:
        if not api_key:
            raise ProviderConfigurationError("LLM_API_KEY is not configured.")
        if not model:
            raise ProviderConfigurationError("LLM_MODEL is not configured.")
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout_seconds
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request_structured(
        self, request: StructuredCompletionRequest, *, tool: dict[str, Any], tool_name: str
    ) -> dict[str, Any]:
        """Shared vendor round-trip: call the model with `tool` forced,
        and return the raw `dict` the tool call carried — NOT yet
        validated against any specific decision schema (that happens in
        each caller, against the schema appropriate to it). Every vendor
        exception is translated here, once, for both decision shapes —
        mirrors `AnthropicProvider._request_structured` exactly."""
        try:
            response = await self._client.post(
                _GROQ_BASE_URL,
                json={
                    "model": self._model,
                    "max_tokens": self._max_output_tokens,
                    "messages": [
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_content},
                    ],
                    "tools": [tool],
                    "tool_choice": {"type": "function", "function": {"name": tool_name}},
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "The LLM provider did not respond within the configured timeout."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("The LLM provider could not be reached.") from exc

        if response.status_code in (401, 403):
            raise ProviderConfigurationError(
                "The LLM provider rejected the configured credentials."
            )
        if response.status_code >= 400:
            raise ProviderUnavailableError("The LLM provider returned an error response.")

        try:
            payload = response.json()
            tool_calls = payload["choices"][0]["message"]["tool_calls"]
            raw_arguments = tool_calls[0]["function"]["arguments"]
            raw_input = json.loads(raw_arguments)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderResponseError(
                "The LLM provider did not return the expected structured decision."
            ) from exc

        if not isinstance(raw_input, dict) or "decision" not in raw_input:
            raise ProviderResponseError(
                "The LLM provider's structured decision did not match the expected shape."
            )
        return raw_input

    async def generate_structured(
        self, request: StructuredCompletionRequest
    ) -> AdministrativeDecision:
        raw_input = await self._request_structured(
            request, tool=_decision_tool(), tool_name=_DECISION_TOOL_NAME
        )
        try:
            return parse_decision(raw_input["decision"])
        except ValidationError as exc:
            raise ProviderResponseError(
                "The LLM provider's response did not match the expected decision schema."
            ) from exc

    async def generate_coordinator_decision(
        self, request: StructuredCompletionRequest
    ) -> CoordinatorDecision:
        raw_input = await self._request_structured(
            request, tool=_coordinator_decision_tool(), tool_name=_COORDINATOR_DECISION_TOOL_NAME
        )
        try:
            return parse_coordinator_decision(raw_input["decision"])
        except ValidationError as exc:
            raise ProviderResponseError(
                "The LLM provider's response did not match the expected coordinator "
                "decision schema."
            ) from exc
