"""`app.ai.agents` and `app.ai.coordinator_decisions` tests — no
database, no network.

Proves the structural guarantees STORY-011 depends on:

- `AgentRegistry` is a plain, explicit allowlist (mirrors
  `app.ai.tools.registry.ToolRegistry` exactly) — duplicate registration
  rejected, unknown names return `None`, no dynamic resolution.
- Exactly four agents are registered by
  `build_default_agent_registry`, each with the expected, distinct tool
  allowlist — the Coordinator's is EMPTY.
- `CoordinatorDecision` has no `tool_call` variant at all, forbids extra
  fields on every variant, and rejects an unknown `target_agent`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.agents.base import AgentDefinition, AgentRole
from app.ai.agents.definitions import (
    COORDINATOR_AGENT,
    DOCUMENT_AGENT,
    ROUTING_AGENT,
    SCHEDULING_AGENT,
    build_default_agent_registry,
)
from app.ai.agents.registry import AgentRegistry
from app.ai.coordinator_decisions import (
    CoordinatorClarificationRequiredDecision,
    CoordinatorDecisionKind,
    CoordinatorRefusalDecision,
    HandoffDecision,
    TargetAgent,
    parse_coordinator_decision,
)

# --- AgentRegistry ---


def test_registry_get_returns_registered_agent() -> None:
    registry = AgentRegistry()
    registry.register(COORDINATOR_AGENT)
    assert registry.get("coordinator") is COORDINATOR_AGENT


def test_registry_get_returns_none_for_unknown_agent() -> None:
    registry = AgentRegistry()
    assert registry.get("hidden_super_admin_agent") is None
    assert registry.get("") is None


def test_registry_rejects_duplicate_registration() -> None:
    registry = AgentRegistry()
    registry.register(SCHEDULING_AGENT)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(SCHEDULING_AGENT)


def test_registry_list_agents_returns_every_registered_agent() -> None:
    registry = build_default_agent_registry()
    names = {agent.name for agent in registry.list_agents()}
    assert names == {"coordinator", "scheduling", "document", "routing"}


# --- build_default_agent_registry: exactly four agents, distinct tool boundaries ---


def test_default_registry_registers_exactly_four_agents() -> None:
    registry = build_default_agent_registry()
    assert len(registry.list_agents()) == 4


def test_coordinator_has_no_tool_allowlist_at_all() -> None:
    """The Coordinator cannot execute domain tools — this is the
    application-code mirror of the schema-level guarantee proven below
    (`CoordinatorDecision` has no `tool_call` variant)."""
    assert COORDINATOR_AGENT.allowed_tools == frozenset()
    assert COORDINATOR_AGENT.role is AgentRole.COORDINATOR


def test_scheduling_agent_tool_allowlist_is_exactly_the_two_appointment_tools() -> None:
    assert SCHEDULING_AGENT.allowed_tools == frozenset({"check_availability", "book_appointment"})


def test_document_agent_tool_allowlist_is_exactly_one_read_only_tool() -> None:
    assert DOCUMENT_AGENT.allowed_tools == frozenset({"list_patient_documents"})


def test_routing_agent_tool_allowlist_is_exactly_one_tool() -> None:
    assert ROUTING_AGENT.allowed_tools == frozenset({"resolve_department"})


def test_every_specialist_allowlist_is_disjoint_from_every_other() -> None:
    """The core "genuine distinctness" property, at the configuration
    level: no two specialists share a tool, and no specialist's
    allowlist is a superset of another's — each has a SEPARATE
    capability boundary, not a renamed copy of the same one."""
    allowlists = {
        "scheduling": SCHEDULING_AGENT.allowed_tools,
        "document": DOCUMENT_AGENT.allowed_tools,
        "routing": ROUTING_AGENT.allowed_tools,
    }
    names = list(allowlists)
    for i, name_a in enumerate(names):
        for name_b in names[i + 1 :]:
            assert allowlists[name_a].isdisjoint(allowlists[name_b]), (
                f"{name_a} and {name_b} must not share any tool"
            )


def test_agent_names_are_stable_lowercase_strings_not_model_names() -> None:
    for agent in build_default_agent_registry().list_agents():
        assert agent.name == agent.name.lower()
        assert "claude" not in agent.name
        assert "gpt" not in agent.name
        assert "anthropic" not in agent.name


def test_agent_definition_is_a_plain_dataclass_no_dynamic_behavior() -> None:
    custom = AgentDefinition(
        name="custom_test_agent",
        role=AgentRole.ROUTING,
        description="test",
        system_prompt="test",
        allowed_tools=frozenset({"resolve_department"}),
    )
    registry = AgentRegistry()
    registry.register(custom)
    assert registry.get("custom_test_agent") is custom


# --- CoordinatorDecision: structural guarantees ---


def test_parses_valid_handoff_decision() -> None:
    decision = parse_coordinator_decision({"kind": "handoff", "target_agent": "scheduling"})
    assert isinstance(decision, HandoffDecision)
    assert decision.target_agent is TargetAgent.SCHEDULING
    assert decision.task_category is None


def test_parses_valid_handoff_decision_with_task_category() -> None:
    decision = parse_coordinator_decision(
        {"kind": "handoff", "target_agent": "document", "task_category": "document_status"}
    )
    assert isinstance(decision, HandoffDecision)
    assert decision.task_category == "document_status"


def test_parses_valid_coordinator_clarification_decision() -> None:
    decision = parse_coordinator_decision(
        {"kind": "clarification_required", "message": "Which specialist do you need?"}
    )
    assert isinstance(decision, CoordinatorClarificationRequiredDecision)


def test_parses_valid_coordinator_refusal_decision() -> None:
    decision = parse_coordinator_decision(
        {"kind": "refusal", "reason_category": "out_of_scope", "safe_message": "No."}
    )
    assert isinstance(decision, CoordinatorRefusalDecision)


def test_rejects_tool_call_shaped_decision_no_such_variant_exists() -> None:
    """The core structural guarantee: the Coordinator cannot express a
    tool call at all — this is a schema rejection, not a runtime
    permission check."""
    with pytest.raises(ValidationError):
        parse_coordinator_decision(
            {"kind": "tool_call", "tool_name": "book_appointment", "arguments": {}}
        )


def test_rejects_unknown_target_agent() -> None:
    with pytest.raises(ValidationError):
        parse_coordinator_decision({"kind": "handoff", "target_agent": "hidden_super_admin_agent"})


def test_rejects_handoff_to_coordinator_itself() -> None:
    """No self-handoff: `"coordinator"` is not a member of the closed
    `TargetAgent` enum."""
    with pytest.raises(ValidationError):
        parse_coordinator_decision({"kind": "handoff", "target_agent": "coordinator"})


def test_rejects_unknown_decision_kind() -> None:
    with pytest.raises(ValidationError):
        parse_coordinator_decision({"kind": "safe_response", "message": "Done."})


def test_rejects_chain_of_thought_field_on_handoff() -> None:
    with pytest.raises(ValidationError):
        parse_coordinator_decision(
            {
                "kind": "handoff",
                "target_agent": "scheduling",
                "chain_of_thought": "First I considered...",
            }
        )


def test_rejects_reasoning_field_on_clarification() -> None:
    with pytest.raises(ValidationError):
        parse_coordinator_decision(
            {
                "kind": "clarification_required",
                "message": "Which one?",
                "reasoning": "I inferred this from context.",
            }
        )


def test_rejects_scratchpad_field_on_refusal() -> None:
    with pytest.raises(ValidationError):
        parse_coordinator_decision(
            {
                "kind": "refusal",
                "reason_category": "out_of_scope",
                "safe_message": "No.",
                "scratchpad": "internal notes",
            }
        )


def test_coordinator_decision_kind_values_are_stable_strings() -> None:
    assert CoordinatorDecisionKind.HANDOFF.value == "handoff"
    assert CoordinatorDecisionKind.CLARIFICATION_REQUIRED.value == "clarification_required"
    assert CoordinatorDecisionKind.REFUSAL.value == "refusal"


def test_target_agent_values_are_stable_strings() -> None:
    assert TargetAgent.SCHEDULING.value == "scheduling"
    assert TargetAgent.DOCUMENT.value == "document"
    assert TargetAgent.ROUTING.value == "routing"


def test_target_agent_names_match_registered_agent_names() -> None:
    """Closes the loop: every `TargetAgent` enum member names an agent
    that is ACTUALLY registered in `build_default_agent_registry` — a
    handoff can never target a name the registry doesn't recognize."""
    registry = build_default_agent_registry()
    for target in TargetAgent:
        assert registry.get(target.value) is not None
