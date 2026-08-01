"""`LLMProvider`: the provider-independent interface every vendor
adapter implements.

Nothing outside `app.ai.providers` may import a vendor SDK — the rest
of AgentCare depends on this interface only (see the `app.ai` package
docstring and docs/adr/ADR-0010-llm-and-tool-security-boundary.md
"Provider Abstraction"). A provider's ONLY job is turning
(system prompt + untrusted user text) into a validated
`AdministrativeDecision` (`app.ai.decisions`) — it never sees trusted
authorization context, and it never executes anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.ai.coordinator_decisions import CoordinatorDecision
from app.ai.decisions import AdministrativeDecision


@dataclass(frozen=True)
class StructuredCompletionRequest:
    """Everything a provider needs to produce one `AdministrativeDecision`.

    `system_prompt` establishes scope/tool-limitations/safety boundary
    (see `app.ai.prompts`) — it is developer-authored, never
    user-influenced. `user_content` is the caller's UNTRUSTED
    administrative request text, already length-bounded by the API
    layer before it ever reaches a provider (see
    `app.schemas.agent.AgentExecuteRequest`).
    """

    system_prompt: str
    user_content: str


class LLMProvider(Protocol):
    """Provider-independent structured-decision interface.

    Implementations MUST raise only the controlled exceptions in
    `app.ai.providers.errors` — never let a vendor SDK exception (or its
    `repr()`/message text, which can include request/connection detail)
    propagate past this boundary.
    """

    async def generate_structured(
        self, request: StructuredCompletionRequest
    ) -> AdministrativeDecision:
        """Return a validated `AdministrativeDecision`.

        Raises `app.ai.providers.errors.ProviderConfigurationError`,
        `ProviderUnavailableError`, `ProviderTimeoutError`, or
        `ProviderResponseError` — never a raw vendor exception.
        """
        ...

    async def generate_coordinator_decision(
        self, request: StructuredCompletionRequest
    ) -> CoordinatorDecision:
        """Return a validated `CoordinatorDecision` — the Coordinator
        agent's distinct, narrower decision space (see
        `app.ai.coordinator_decisions`), which structurally cannot
        express a tool call. A SEPARATE method from `generate_structured`
        (not a schema-parametrized single method) so STORY-010's existing
        specialist-decision contract and tests are entirely unaffected by
        the Coordinator's addition in STORY-011.

        Raises the same controlled exceptions as `generate_structured`.
        """
        ...

    async def aclose(self) -> None:
        """Release this provider's underlying HTTP client/connection
        pool. Called exactly once, at application shutdown, on the one
        shared instance `app.ai.providers.factory.get_llm_provider`
        returns (Sprint 2) — see `app.main.lifespan`. Implementations
        with nothing to release (e.g. a test fake) may no-op."""
        ...
