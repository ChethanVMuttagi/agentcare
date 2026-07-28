"""The FULL `ToolRegistry` the running application actually uses:
every real tool across every specialist agent, in one registry.

`ToolRegistry` itself is deliberately agent-agnostic (see
`app.ai.tools.registry`'s docstring) — any registered tool is
technically callable by anyone who reaches `.execute()`. Per-agent
restriction (e.g. "Document may not call `book_appointment`") is a
SEPARATE, application-code check performed by
`app.ai.orchestration.AgentOrchestrationService` against each
specialist's own `AgentDefinition.allowed_tools` — BEFORE this registry
is ever consulted. See docs/AGENTS.md "Two-Layer Tool Enforcement".

Kept separate from `app.ai.tools.appointment_tools.build_default_registry`,
which intentionally stays scoped to appointment tools only — several
STORY-010 tests assert that narrower registry's exact contents
(`{"check_availability", "book_appointment"}`), and this module must not
change that. This is the registry `app.api.v1.endpoints.agent` and
`app.ai.orchestration` are wired to instead.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.tools.appointment_tools import BOOK_APPOINTMENT_TOOL, CHECK_AVAILABILITY_TOOL
from app.ai.tools.document_tools import LIST_PATIENT_DOCUMENTS_TOOL
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.routing_tools import RESOLVE_DEPARTMENT_TOOL


def build_full_tool_registry() -> ToolRegistry:
    """Every real tool this codebase implements, in one registry. Adding
    a new tool anywhere means adding one more `registry.register(...)`
    call here, in addition to whichever specialist's `allowed_tools`
    should be able to reach it."""
    registry = ToolRegistry()
    registry.register(CHECK_AVAILABILITY_TOOL)
    registry.register(BOOK_APPOINTMENT_TOOL)
    registry.register(LIST_PATIENT_DOCUMENTS_TOOL)
    registry.register(RESOLVE_DEPARTMENT_TOOL)
    return registry


@lru_cache
def get_full_tool_registry() -> ToolRegistry:
    """FastAPI-dependency-friendly, cached accessor — mirrors
    `app.ai.tools.appointment_tools.get_tool_registry`."""
    return build_full_tool_registry()
