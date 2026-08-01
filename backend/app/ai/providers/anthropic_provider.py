"""`AnthropicProvider`: the one real `LLMProvider` implemented in
STORY-010, backed by the official `anthropic` Python SDK. Extended in
STORY-011 to also serve the Coordinator agent's distinct decision space.

This is the ONLY module in this codebase that imports the `anthropic`
package. Structured output is obtained via Anthropic's tool-use
feature: a single pseudo-tool whose input schema IS the target
decision type's schema, with `tool_choice` forced to it — the model has
no way to respond with free-form prose instead of a schema-conformant
decision. See docs/adr/ADR-0010-llm-and-tool-security-boundary.md
"Structured Output".

`generate_structured` (specialist decisions) and
`generate_coordinator_decision` (Coordinator decisions) share the same
HTTP-call/vendor-exception-translation logic via `_request_structured` —
they differ only in which JSON schema is forced and which parse
function validates the raw result. Every vendor exception is caught in
`_request_structured` and translated to one of
`app.ai.providers.errors`' controlled exceptions before it can reach any
caller — see that module's docstring for why.
"""

from __future__ import annotations

from typing import Any

import anthropic
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

_DECISION_TOOL_NAME = "record_administrative_decision"
_COORDINATOR_DECISION_TOOL_NAME = "record_coordinator_decision"


def _decision_tool() -> dict[str, Any]:
    return {
        "name": _DECISION_TOOL_NAME,
        "description": (
            "Record your administrative decision. Call this exactly once with "
            "your decision — do not respond with any other text."
        ),
        "input_schema": decision_tool_schema(),
    }


def _coordinator_decision_tool() -> dict[str, Any]:
    return {
        "name": _COORDINATOR_DECISION_TOOL_NAME,
        "description": (
            "Record your coordination decision — which specialist should handle "
            "this request, or that clarification/refusal is needed. Call this "
            "exactly once — do not respond with any other text. You cannot "
            "request a tool or take any direct action yourself."
        ),
        "input_schema": coordinator_decision_tool_schema(),
    }


class AnthropicProvider:
    """`LLMProvider` implementation backed by `anthropic.AsyncAnthropic`.

    Sprint 2: constructed ONCE for the process lifetime — see
    `app.ai.providers.factory.get_llm_provider` (a shared, cached
    singleton, no longer built fresh per request) and `aclose()` below,
    called exactly once at application shutdown (`app.main.lifespan`).
    `api_key` is a plain `str` here — the caller (see
    `app.ai.providers.factory`) is responsible for extracting it from a
    `SecretStr` exactly once, at the point of construction; this class
    never logs it and never includes it in any exception message.
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
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.close()

    async def _request_structured(
        self, request: StructuredCompletionRequest, *, tool: dict[str, Any], tool_name: str
    ) -> dict[str, Any]:
        """Shared vendor round-trip: call the model with `tool` forced,
        and return the raw `dict` the tool call carried — NOT yet
        validated against any specific decision schema (that happens in
        each caller, against the schema appropriate to it). Every vendor
        exception is translated here, once, for both decision shapes."""
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=request.system_prompt,
                messages=[{"role": "user", "content": request.user_content}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool_name},
            )  # type: ignore[call-overload]
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(
                "The LLM provider did not respond within the configured timeout."
            ) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ProviderConfigurationError(
                "The LLM provider rejected the configured credentials."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderUnavailableError(
                "The LLM provider could not be reached."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderUnavailableError(
                "The LLM provider returned an error response."
            ) from exc
        except anthropic.AnthropicError as exc:
            raise ProviderUnavailableError(
                "The LLM provider request failed."
            ) from exc

        tool_use_block = next(
            (block for block in response.content if block.type == "tool_use"), None
        )
        if tool_use_block is None:
            raise ProviderResponseError(
                "The LLM provider did not return the expected structured decision."
            )

        raw_input = tool_use_block.input
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
