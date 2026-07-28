"""`AgentRegistry`: the explicit, allowlisted set of agents the
Coordinator may hand off to.

Directly mirrors `app.ai.tools.registry.ToolRegistry`. Lookup is a plain
`dict` access by exact string name — never `getattr`, `eval`, `exec`, a
dynamic import, or arbitrary class instantiation. An unrecognized agent
name is a controlled rejection (`get` returns `None`), never an
exception that could propagate internal detail outward.
"""

from __future__ import annotations

from app.ai.agents.base import AgentDefinition


class AgentRegistry:
    """A plain, explicit allowlist. `register()` is called exactly once
    per agent, at startup (see
    `app.ai.agents.definitions.build_default_agent_registry`) — never at
    request time, and never based on anything a model supplies."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}

    def register(self, agent: AgentDefinition) -> None:
        if agent.name in self._agents:
            raise ValueError(f"Agent {agent.name!r} is already registered.")
        self._agents[agent.name] = agent

    def get(self, name: str) -> AgentDefinition | None:
        """Plain dict lookup — returns `None` for anything not
        explicitly registered. No fallback, no fuzzy matching, no
        dynamic resolution of any kind."""
        return self._agents.get(name)

    def list_agents(self) -> list[AgentDefinition]:
        return list(self._agents.values())
