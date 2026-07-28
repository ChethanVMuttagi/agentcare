"""`CoordinatorDecision`: the ONLY shape the Coordinator agent's output is
ever trusted in as.

Mirrors `app.ai.decisions.AdministrativeDecision`'s structural
guarantees exactly (Pydantic `discriminator="kind"` union, every variant
`extra="forbid"`) — see that module's docstring for why those two
properties matter.

**The one deliberate structural difference from `AdministrativeDecision`:
there is no `tool_call`-shaped variant here at all.** The Coordinator's
only job is choosing a specialist (or asking for clarification, or
refusing) — it can never itself request a domain tool. This is not a
runtime check anyone could accidentally skip; it is impossible to
construct a `CoordinatorDecision` that carries a tool name and
arguments, because no such variant exists in the union. A provider
response shaped like `{"kind": "tool_call", ...}` fails Pydantic
validation the same way an unrecognized `kind` would — see
`parse_coordinator_decision`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.decisions import RefusalCategory
from app.models.approval import ApprovalType

_MESSAGE_MAX_LENGTH = 1000
_TASK_CATEGORY_MAX_LENGTH = 100
_REASON_MAX_LENGTH = 500


class CoordinatorDecisionKind(StrEnum):
    """The closed set of things a Coordinator decision may be. Deliberately
    does NOT include anything tool-call-shaped — see the module docstring."""

    HANDOFF = "handoff"
    CLARIFICATION_REQUIRED = "clarification_required"
    REFUSAL = "refusal"
    REQUIRES_APPROVAL = "requires_approval"
    """STORY-014: the Coordinator has determined this request needs a
    human decision before it can proceed (e.g. low confidence, an
    ambiguous policy-restricted action, or missing information it
    should not guess at) — "instead of hallucinating, request approval".
    See `CoordinatorRequiresApprovalDecision` and
    `app.services.approval.ApprovalService`."""


class TargetAgent(StrEnum):
    """The closed set of specialist agents the Coordinator may hand off
    to. Deliberately does NOT include `coordinator` itself (no
    self-handoff) — see `app.ai.agents.registry.AgentRegistry` for the
    corresponding runtime allowlist check."""

    SCHEDULING = "scheduling"
    DOCUMENT = "document"
    ROUTING = "routing"


class HandoffDecision(BaseModel):
    """The Coordinator has decided a specific specialist should handle
    this request.

    Carries only `target_agent` and an OPTIONAL, small, bounded
    `task_category` hint — never a coordinator-composed prompt, free-form
    instructions, hidden reasoning, or anything resembling
    chain-of-thought passed between agents. The specialist re-processes
    the SAME original request text the Coordinator saw (supplied
    directly by orchestration code, not by this decision) — see
    `app.ai.orchestration`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CoordinatorDecisionKind.HANDOFF] = CoordinatorDecisionKind.HANDOFF
    target_agent: TargetAgent
    task_category: str | None = Field(default=None, max_length=_TASK_CATEGORY_MAX_LENGTH)


class CoordinatorClarificationRequiredDecision(BaseModel):
    """The Coordinator needs more information before any specialist could
    be chosen. `message` is a safe question shown directly to the
    caller."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CoordinatorDecisionKind.CLARIFICATION_REQUIRED] = (
        CoordinatorDecisionKind.CLARIFICATION_REQUIRED
    )
    message: str = Field(min_length=1, max_length=_MESSAGE_MAX_LENGTH)


class CoordinatorRefusalDecision(BaseModel):
    """The Coordinator has determined this request is out of scope,
    unsafe, or otherwise should not be routed to any specialist.
    `safe_message` is a short, safe explanation shown to the caller —
    never the model's internal reasoning."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CoordinatorDecisionKind.REFUSAL] = CoordinatorDecisionKind.REFUSAL
    reason_category: RefusalCategory
    safe_message: str = Field(min_length=1, max_length=_MESSAGE_MAX_LENGTH)


class CoordinatorRequiresApprovalDecision(BaseModel):
    """The Coordinator has determined this request needs a human
    decision before it can proceed, rather than being handed to a
    specialist, answered directly, or refused outright — e.g. low
    confidence, an ambiguous or policy-restricted action, or missing
    information the Coordinator should not guess at (STORY-014:
    "instead of hallucinating, request approval").

    `reason` is a short, bounded, safe explanation of WHY approval is
    needed — never the model's full internal reasoning, and never
    free-form patient-identifying text. `approval_type` reuses
    `app.models.approval.ApprovalType` directly (the same canonical enum
    persisted on `ApprovalRequest`) rather than an independently defined
    mirror — an approval type must always mean exactly the same thing
    whether it came from the database or from the Coordinator.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[CoordinatorDecisionKind.REQUIRES_APPROVAL] = (
        CoordinatorDecisionKind.REQUIRES_APPROVAL
    )
    approval_type: ApprovalType
    reason: str = Field(min_length=1, max_length=_REASON_MAX_LENGTH)


CoordinatorDecision = Annotated[
    HandoffDecision
    | CoordinatorClarificationRequiredDecision
    | CoordinatorRefusalDecision
    | CoordinatorRequiresApprovalDecision,
    Field(discriminator="kind"),
]


class _CoordinatorDecisionEnvelope(BaseModel):
    """Internal-only wrapper, mirroring `app.ai.decisions._DecisionEnvelope`
    — validates a raw `dict` against the `CoordinatorDecision`
    discriminated union via a single field. Not exported."""

    model_config = ConfigDict(extra="forbid")

    decision: CoordinatorDecision


def coordinator_decision_tool_schema() -> dict[str, Any]:
    """The JSON Schema for `_CoordinatorDecisionEnvelope` — used by
    `app.ai.providers.anthropic_provider` as the input schema for the
    single forced tool call that carries the Coordinator's structured
    decision. A DIFFERENT schema than `app.ai.decisions.decision_tool_schema`
    — the model literally cannot express a tool_call shape through this
    schema."""
    return _CoordinatorDecisionEnvelope.model_json_schema()


def parse_coordinator_decision(raw: dict[str, Any]) -> CoordinatorDecision:
    """Validate a raw `dict` (already-parsed JSON from a provider) into a
    `CoordinatorDecision`. Raises `pydantic.ValidationError` for anything
    that doesn't match exactly one variant — including a smuggled
    `{"kind": "tool_call", ...}` shape, which has no matching variant at
    all in this union."""
    return _CoordinatorDecisionEnvelope(decision=raw).decision
