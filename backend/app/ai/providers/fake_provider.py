"""`FakeLLMProvider`: a deterministic, network-free `LLMProvider` for
tests.

Every test in this codebase that exercises orchestration, tool
execution, safety, or the API layer uses this provider — never a real
Anthropic call. Tests run without internet access and without any API
key configured. See docs/AI_SAFETY.md "Testing Strategy".

This is deliberately NOT a mock of the whole application path: it only
replaces the ONE thing that would otherwise require a real network call
and a real API key (`generate_structured`'s and
`generate_coordinator_decision`'s vendor round-trips). Every call to
`app.ai.tools`, `app.services.*`, and PostgreSQL underneath it is still
the real thing.

Two INDEPENDENT configuration trios exist — `decision`/`raw_response`/
`error` (governing `generate_structured`, unchanged from STORY-010) and
`coordinator_decision`/`coordinator_raw_response`/`coordinator_error`
(governing `generate_coordinator_decision`, new in STORY-011) — so a
single `FakeLLMProvider` instance can drive a full coordinator ->
specialist handoff in one multi-agent test, while a test that only cares
about one side (e.g. a Coordinator-only refusal) need not configure the
other. Each trio independently validated for "at most one set" (not
"exactly one" — a refusal-only test never touches the specialist side).
Calling a method whose trio was never configured raises `AssertionError`,
catching test-authoring bugs rather than silently returning nothing.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai.coordinator_decisions import CoordinatorDecision, parse_coordinator_decision
from app.ai.decisions import AdministrativeDecision, parse_decision
from app.ai.providers.base import StructuredCompletionRequest
from app.ai.providers.errors import ProviderResponseError, ProviderUnavailableError


class FakeLLMProvider:
    """Returns fixed, pre-configured results on every call.

    Specialist side — exactly one of `decision`, `raw_response`, or
    `error`, or none at all if this test never exercises the specialist
    path:

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

    Coordinator side — exactly one of `coordinator_decision`,
    `coordinator_raw_response`, or `coordinator_error`, or none at all
    if this test never exercises the coordinator path. Same three shapes
    as above, applied to `CoordinatorDecision`/`generate_coordinator_decision`.

    Every call is recorded in `.calls`/`.coordinator_calls` (the
    `StructuredCompletionRequest` each call received) so tests can assert
    what was actually sent to "the provider" without needing a real one.
    """

    def __init__(
        self,
        *,
        decision: AdministrativeDecision | None = None,
        raw_response: dict[str, Any] | None = None,
        error: Exception | None = None,
        coordinator_decision: CoordinatorDecision | None = None,
        coordinator_raw_response: dict[str, Any] | None = None,
        coordinator_error: Exception | None = None,
    ) -> None:
        provided = sum(x is not None for x in (decision, raw_response, error))
        if provided > 1:
            raise ValueError(
                "FakeLLMProvider accepts at most one of decision/raw_response/error."
            )
        coordinator_provided = sum(
            x is not None
            for x in (coordinator_decision, coordinator_raw_response, coordinator_error)
        )
        if coordinator_provided > 1:
            raise ValueError(
                "FakeLLMProvider accepts at most one of coordinator_decision/"
                "coordinator_raw_response/coordinator_error."
            )
        self._decision = decision
        self._raw_response = raw_response
        self._error = error
        self._coordinator_decision = coordinator_decision
        self._coordinator_raw_response = coordinator_raw_response
        self._coordinator_error = coordinator_error
        self.calls: list[StructuredCompletionRequest] = []
        self.coordinator_calls: list[StructuredCompletionRequest] = []

    async def generate_structured(
        self, request: StructuredCompletionRequest
    ) -> AdministrativeDecision:
        self.calls.append(request)

        if self._error is not None:
            raise self._error

        if self._decision is not None:
            return self._decision

        if self._raw_response is not None:
            try:
                return parse_decision(self._raw_response)
            except ValidationError as exc:
                raise ProviderResponseError(
                    "The configured provider's response did not match the expected "
                    "decision schema."
                ) from exc

        raise AssertionError(
            "FakeLLMProvider.generate_structured() was called, but none of "
            "decision/raw_response/error was configured for this test."
        )

    async def generate_coordinator_decision(
        self, request: StructuredCompletionRequest
    ) -> CoordinatorDecision:
        self.coordinator_calls.append(request)

        if self._coordinator_error is not None:
            raise self._coordinator_error

        if self._coordinator_decision is not None:
            return self._coordinator_decision

        if self._coordinator_raw_response is not None:
            try:
                return parse_coordinator_decision(self._coordinator_raw_response)
            except ValidationError as exc:
                raise ProviderResponseError(
                    "The configured provider's response did not match the expected "
                    "coordinator decision schema."
                ) from exc

        raise AssertionError(
            "FakeLLMProvider.generate_coordinator_decision() was called, but none "
            "of coordinator_decision/coordinator_raw_response/coordinator_error was "
            "configured for this test."
        )

    async def aclose(self) -> None:
        """Nothing to release — no real HTTP client exists here."""
        return None


class AlwaysUnavailableFakeLLMProvider:
    """A `FakeLLMProvider` variant dedicated to simulating "the provider
    could not be reached at all" — kept separate from the general
    `FakeLLMProvider(error=...)` form purely for test readability at
    call sites that specifically want this scenario. Unavailable on
    BOTH the specialist and coordinator paths."""

    async def generate_structured(
        self, request: StructuredCompletionRequest
    ) -> AdministrativeDecision:
        raise ProviderUnavailableError("The configured LLM provider is unavailable.")

    async def generate_coordinator_decision(
        self, request: StructuredCompletionRequest
    ) -> CoordinatorDecision:
        raise ProviderUnavailableError("The configured LLM provider is unavailable.")

    async def aclose(self) -> None:
        """Nothing to release — no real HTTP client exists here."""
        return None
