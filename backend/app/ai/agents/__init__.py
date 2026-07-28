"""The four `AgentDefinition`s (STORY-011): `AgentDefinition`/
`AgentRegistry` (`base.py`/`registry.py`, mirroring
`app.ai.tools.base.ToolDefinition`/`app.ai.tools.registry.ToolRegistry`),
per-agent prompts (`prompts.py`), and the actual Coordinator/Scheduling/
Document/Routing definitions (`definitions.py`) — each calling into the
REAL existing tool/service layer via its own fixed tool allowlist, never
a renamed copy of a single universal agent.
"""
