"""`AnthropicProvider`: the one real `LLMProvider` implemented in
STORY-010, backed by the official `anthropic` Python SDK.

This is the ONLY module in this codebase that imports the `anthropic`
package. Structured output is obtained via Anthropic's tool-use
feature: a single pseudo-tool (`_DECISION_TOOL_NAME`) whose input
schema IS `AdministrativeDecision`'s schema
(`app.ai.decisions.decision_tool_schema`), with `tool_choice` forced to
it — the model has no way to respond with free-form prose instead of a
schema-conformant decision. See
docs/adr/ADR-0010-llm-and-tool-security-boundary.md "Structured Output".

Every vendor exception is caught here and translated to one of
`app.ai.providers.errors`' controlled exceptions before it can reach
any caller — see that module's docstring for why.
"""

from __future__ import annotations

from typing import Any

import anthropic
from pydantic import ValidationError

from app.ai.decisions import AdministrativeDecision, decision_tool_schema, parse_decision
from app.ai.providers.base import StructuredCompletionRequest
from app.ai.providers.errors import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

_DECISION_TOOL_NAME = "record_administrative_decision"


def _decision_tool() -> dict[str, Any]:
    return {
        "name": _DECISION_TOOL_NAME,
        "description": (
            "Record your administrative decision. Call this exactly once with "
            "your decision — do not respond with any other text."
        ),
        "input_schema": decision_tool_schema(),
    }


class AnthropicProvider:
    """`LLMProvider` implementation backed by `anthropic.AsyncAnthropic`.

    Constructed once per request in this story (no connection pooling
    concerns beyond what the SDK's own `httpx` client already handles).
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

    async def generate_structured(
        self, request: StructuredCompletionRequest
    ) -> AdministrativeDecision:
        try:
            # The Anthropic SDK types `tools`/`tool_choice` as specific
            # TypedDicts; the plain dicts built above are structurally
            # identical but mypy's overload resolution doesn't unify
            # them cleanly with `model: str` (a non-literal, since the
            # model name is configuration, not hard-coded — see the
            # module docstring). Verified correct at runtime.
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=request.system_prompt,
                messages=[{"role": "user", "content": request.user_content}],
                tools=[_decision_tool()],
                tool_choice={"type": "tool", "name": _DECISION_TOOL_NAME},
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

        try:
            return parse_decision(raw_input["decision"])
        except ValidationError as exc:
            raise ProviderResponseError(
                "The LLM provider's response did not match the expected decision schema."
            ) from exc
