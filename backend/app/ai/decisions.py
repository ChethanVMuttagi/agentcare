"""`AdministrativeDecision`: the ONLY shape an LLM's output is ever
trusted in as, anywhere in this codebase.

A provider's raw response is parsed into one of the four variants below
via a Pydantic `discriminator="kind"` union — nothing else is a valid
decision. Two properties this buys, both load-bearing for the "model
output is DATA, not trusted executable instruction" boundary
(docs/AI_SAFETY.md):

1. **Unknown decision kinds are rejected, not ignored.** A provider
   emitting `{"kind": "run_sql", ...}` (or anything not in
   `DecisionKind`) fails Pydantic validation immediately —
   `app.ai.providers` turns that into `ProviderResponseError` (502)
   before it ever reaches orchestration logic.
2. **Every variant forbids extra fields (`extra="forbid"`).** This is
   the STRUCTURAL enforcement of "no chain-of-thought": if a provider
   (or a future SDK feature) adds a `reasoning`/`chain_of_thought`/
   `internal_reasoning`/`scratchpad` field to its output, the ENTIRE
   decision fails validation rather than silently passing through with
   that field dropped or, worse, persisted. This is deliberately
   fail-closed, not fail-open.

`ToolCallDecision.arguments` is intentionally `dict[str, Any]` at THIS
layer — it is UNTRUSTED, model-supplied data, not yet validated against
any specific tool's argument schema (that happens per-tool, downstream,
in `app.ai.tools`, once the tool NAME has been checked against the
`ToolRegistry` allowlist). Bounded in size here only to prevent a
grossly oversized payload from ever reaching that point.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_MESSAGE_MAX_LENGTH = 1000
_TOOL_NAME_MAX_LENGTH = 100
_ARGUMENTS_MAX_KEYS = 20


class DecisionKind(StrEnum):
    """The closed set of things a model decision may be. Nothing else is
    valid — see the module docstring."""

    TOOL_CALL = "tool_call"
    CLARIFICATION_REQUIRED = "clarification_required"
    SAFE_RESPONSE = "safe_response"
    REFUSAL = "refusal"


class RefusalCategory(StrEnum):
    """Why a request was refused — a safe, bounded category, never a
    free-form explanation of the model's reasoning."""

    OUT_OF_SCOPE = "out_of_scope"
    CLINICAL_CONTENT = "clinical_content"
    SAFETY_POLICY = "safety_policy"
    UNSUPPORTED_REQUEST = "unsupported_request"


class ToolCallDecision(BaseModel):
    """The model wants to invoke exactly one administrative tool.

    `tool_name` is checked against `ToolRegistry`'s allowlist
    downstream — this schema does not itself know what tools exist.
    `arguments` is untrusted and re-validated against that specific
    tool's own input schema before execution (see
    `app.ai.tools.registry`); it is NEVER merged with trusted
    `ToolExecutionContext` data, and it never determines
    organization/user/role identity.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[DecisionKind.TOOL_CALL] = DecisionKind.TOOL_CALL
    tool_name: str = Field(min_length=1, max_length=_TOOL_NAME_MAX_LENGTH)
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=_ARGUMENTS_MAX_KEYS)


class ClarificationRequiredDecision(BaseModel):
    """The model needs more information before any tool could be
    considered — e.g. an ambiguous or incomplete administrative request.
    `message` is a safe question shown directly to the caller; it is
    never a hidden reasoning trace."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[DecisionKind.CLARIFICATION_REQUIRED] = DecisionKind.CLARIFICATION_REQUIRED
    message: str = Field(min_length=1, max_length=_MESSAGE_MAX_LENGTH)


class SafeResponseDecision(BaseModel):
    """The model has a safe, purely informational administrative answer
    that requires no tool call (e.g. summarizing something already
    established in this exchange). `message` is shown directly to the
    caller."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[DecisionKind.SAFE_RESPONSE] = DecisionKind.SAFE_RESPONSE
    message: str = Field(min_length=1, max_length=_MESSAGE_MAX_LENGTH)


class RefusalDecision(BaseModel):
    """The model has determined this request is out of scope, unsafe, or
    otherwise should not proceed — e.g. a symptom-based clinical
    question. `safe_message` is a short, safe explanation shown to the
    caller (e.g. directing them to appropriate human clinical
    assistance) — never the model's internal reasoning for the
    refusal."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[DecisionKind.REFUSAL] = DecisionKind.REFUSAL
    reason_category: RefusalCategory
    safe_message: str = Field(min_length=1, max_length=_MESSAGE_MAX_LENGTH)


AdministrativeDecision = Annotated[
    ToolCallDecision | ClarificationRequiredDecision | SafeResponseDecision | RefusalDecision,
    Field(discriminator="kind"),
]


class _DecisionEnvelope(BaseModel):
    """Internal-only wrapper used purely to validate a raw `dict` (e.g.
    parsed provider JSON) against the `AdministrativeDecision`
    discriminated union via a single field. Not exported — callers use
    `parse_decision`."""

    model_config = ConfigDict(extra="forbid")

    decision: AdministrativeDecision


def decision_tool_schema() -> dict[str, Any]:
    """The JSON Schema for `_DecisionEnvelope` (`{"decision": {...}}`) —
    used by `app.ai.providers.anthropic_provider` as the input schema
    for the single forced tool call that carries a provider's structured
    decision. Kept here, next to the schema it describes, rather than
    duplicated in the provider module."""
    return _DecisionEnvelope.model_json_schema()


def parse_decision(raw: dict[str, Any]) -> AdministrativeDecision:
    """Validate a raw `dict` (already-parsed JSON from a provider) into
    an `AdministrativeDecision`. Raises `pydantic.ValidationError` for
    anything that doesn't match exactly one variant — an unknown `kind`,
    a missing required field, or an unexpected extra field (including a
    reasoning/chain-of-thought-shaped one). Callers in
    `app.ai.providers` catch `ValidationError` and translate it to
    `ProviderResponseError`; nothing else in this codebase should need
    to catch it directly."""
    return _DecisionEnvelope(decision=raw).decision
