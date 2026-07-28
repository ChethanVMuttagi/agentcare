"""The four `AgentDefinition`s this story implements, and the
`AgentRegistry` built from them.

Exactly four agents — no more — per docs/AGENTS.md "Why Exactly Four
Agents": one Coordinator (chooses a specialist; cannot execute domain
tools — see `app.ai.coordinator_decisions`) and three specialists, each
with its own small, fixed tool allowlist:

- Scheduling: `check_availability`, `book_appointment`.
- Document: `list_patient_documents`.
- Routing: `resolve_department`.

Tool names are referenced here as plain strings, not imported from
`app.ai.tools.*` — this module deliberately has no dependency on tool
handler implementations, only on the STABLE NAMES those tools are
registered under (see `app.ai.tools.registry_builder.build_full_tool_registry`,
which is what actually wires a name to a handler). A mismatch between a
name listed here and a name actually registered fails safe: the
orchestration layer's `unknown_tool` rejection in `ToolRegistry.execute`
still applies even if the per-agent allowlist check somehow let it
through.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.agents.base import AgentDefinition, AgentRole
from app.ai.agents.prompts import (
    COORDINATOR_SYSTEM_PROMPT,
    DOCUMENT_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT,
    SCHEDULING_SYSTEM_PROMPT,
)
from app.ai.agents.registry import AgentRegistry

COORDINATOR_AGENT = AgentDefinition(
    name="coordinator",
    role=AgentRole.COORDINATOR,
    description="Chooses which specialist agent, if any, should handle a request.",
    system_prompt=COORDINATOR_SYSTEM_PROMPT,
    allowed_tools=frozenset(),
)

SCHEDULING_AGENT = AgentDefinition(
    name="scheduling",
    role=AgentRole.SCHEDULING,
    description="Checks appointment availability and books appointments.",
    system_prompt=SCHEDULING_SYSTEM_PROMPT,
    allowed_tools=frozenset({"check_availability", "book_appointment"}),
)

DOCUMENT_AGENT = AgentDefinition(
    name="document",
    role=AgentRole.DOCUMENT,
    description="Checks the status of a patient's administrative documents on file.",
    system_prompt=DOCUMENT_SYSTEM_PROMPT,
    allowed_tools=frozenset({"list_patient_documents"}),
)

ROUTING_AGENT = AgentDefinition(
    name="routing",
    role=AgentRole.ROUTING,
    description="Resolves an explicitly named department for an administrative request.",
    system_prompt=ROUTING_SYSTEM_PROMPT,
    allowed_tools=frozenset({"resolve_department"}),
)


def build_default_agent_registry() -> AgentRegistry:
    """The `AgentRegistry` this codebase actually uses. Adding a new
    agent means adding one more `registry.register(...)` call here."""
    registry = AgentRegistry()
    registry.register(COORDINATOR_AGENT)
    registry.register(SCHEDULING_AGENT)
    registry.register(DOCUMENT_AGENT)
    registry.register(ROUTING_AGENT)
    return registry


@lru_cache
def get_agent_registry() -> AgentRegistry:
    """FastAPI-dependency-friendly, cached accessor — the registry is
    stateless and identical on every call, so it is built exactly once
    per process (mirrors `app.ai.tools.appointment_tools.get_tool_registry`)."""
    return build_default_agent_registry()
