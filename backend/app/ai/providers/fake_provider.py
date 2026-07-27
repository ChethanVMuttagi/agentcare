"""`FakeLLMProvider`: a deterministic, network-free `LLMProvider` for
tests.

Every test in this codebase that exercises orchestration, tool
execution, safety, or the API layer uses this provider — never a real
Anthropic call. Tests run without internet access and without any API
key configured. See docs/AI_SAFETY.md "Testing Strategy".

This is deliberately NOT a mock of the whole application path: it only
replaces the ONE thing that would otherwise require a real network call
and a real API key (`generate_structured`'s vendor round-trip). Every
call to `app.ai.tools`, `app.services.*`, and PostgreSQL underneath it
is still the real thing.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai.decisions import AdministrativeDecision, parse_decision
from app.ai.providers.base import StructuredCompletionRequest
from app.ai.providers.errors import ProviderResponseError, ProviderUnavailableError


class FakeLLMProvider:
    """Returns a fixed, pre-configured result on every call.

    Exactly one of `decision`, `raw_response`, or `error` should be
    given:

    - `decision`: an already-constructed, valid `AdministrativeDecision`
      — returned as-is.
    - `raw_response`: an arbitrary `dict`, run through the SAME
      `parse_decision` validation path a real provider's parsed JSON
      would go through — use this to simulate a malformed/unknown-kind/
      extra-field (e.g. a smuggled `chain_of_thought` field) response
      from the provider and confirm it is rejected as
      `ProviderResponseError`, not silently accepted.
    - `error`: raised directly — use an instance of one of
      `app.ai.providers.errors`' controlled exceptions to simulate a
      provider-side failure (timeout, unavailable, etc.).

    Every call is recorded in `.calls` (the `StructuredCompletionRequest`
    each call received) so tests can assert what was actually sent to
    "the provider" without needing a real one.
    """

    def __init__(
        self,
        *,
        decision: AdministrativeDecision | None = None,
        raw_response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        provided = sum(x is not None for x in (decision, raw_response, error))
        if provided != 1:
            raise ValueError(
                "FakeLLMProvider requires exactly one of decision/raw_response/error."
            )
        self._decision = decision
        self._raw_response = raw_response
        self._error = error
        self.calls: list[StructuredCompletionRequest] = []

    async def generate_structured(
        self, request: StructuredCompletionRequest
    ) -> AdministrativeDecision:
        self.calls.append(request)

        if self._error is not None:
            raise self._error

        if self._decision is not None:
            return self._decision

        assert self._raw_response is not None  # narrows type; guaranteed by __init__
        try:
            return parse_decision(self._raw_response)
        except ValidationError as exc:
            raise ProviderResponseError(
                "The configured provider's response did not match the expected "
                "decision schema."
            ) from exc


class AlwaysUnavailableFakeLLMProvider:
    """A `FakeLLMProvider` variant dedicated to simulating "the provider
    could not be reached at all" — kept separate from the general
    `FakeLLMProvider(error=...)` form purely for test readability at
    call sites that specifically want this scenario."""

    async def generate_structured(
        self, request: StructuredCompletionRequest
    ) -> AdministrativeDecision:
        raise ProviderUnavailableError("The configured LLM provider is unavailable.")
