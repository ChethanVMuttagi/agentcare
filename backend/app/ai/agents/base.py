"""`AgentDefinition`: one explicitly-registered specialist agent.

Directly mirrors `app.ai.tools.base.ToolDefinition` — a plain,
in-memory, developer-authored description of an agent's identity, scope,
and (for specialists) tool boundary. Deliberately NOT a database table:
this is static application configuration, exactly the same reasoning
`ToolDefinition`/`ToolRegistry` already established for tools — see
`app.ai.agents.registry.AgentRegistry`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AgentRole(StrEnum):
    """The closed set of agent roles this story implements. Exactly four
    — see docs/AGENTS.md "Why Exactly Four Agents"."""

    COORDINATOR = "coordinator"
    SCHEDULING = "scheduling"
    DOCUMENT = "document"
    ROUTING = "routing"


@dataclass(frozen=True)
class AgentDefinition:
    """One agent's identity, scope, and capability boundary.

    `name` is the stable, logical identifier persisted in
    `WorkflowStep.agent_name` (e.g. `"scheduling"`) — never a provider or
    model name. `system_prompt` is developer-authored (see
    `app.ai.agents.prompts`), never user-influenced. `allowed_tools` is
    the SAME kind of explicit allowlist `ToolRegistry` already uses for
    tools: the Coordinator's is always empty (it cannot execute domain
    tools — see `app.ai.coordinator_decisions` for the stronger,
    schema-level guarantee of the same fact); each specialist's is a
    small, fixed set of exact tool names. This allowlist is enforced in
    `app.ai.orchestration.AgentOrchestrationService` — BEFORE a
    specialist's requested tool is ever passed to `ToolRegistry.execute`
    — even though `ToolRegistry` itself does not distinguish which agent
    is calling it.
    """

    name: str
    role: AgentRole
    description: str
    system_prompt: str
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
