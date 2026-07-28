"""The Routing specialist's one tool: `resolve_department`.

Calls the REAL existing repository layer
(`app.repositories.department.search_by_name`) — never a fake/hardcoded
success path. Resolves an EXPLICITLY named department only: this tool
performs a plain, case-insensitive name match, never any clinical
inference from symptoms or a described condition — that boundary is
upheld by `app.ai.agents.prompts.ROUTING_SYSTEM_PROMPT` and,
deterministically and prior to any agent ever being invoked, by
`app.ai.safety.SafetyPolicy`.

An AMBIGUOUS match (more than one active department name contains the
query) is a `ToolResult` FAILURE carrying the bounded list of candidate
names — never a guess, and never a second model round-trip (out of
scope for this story's "at most one tool execution" limit). See
docs/TOOLS.md "Routing Tool".
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.base import (
    ToolCategory,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
    ToolResultStatus,
)
from app.ai.tools.registry import ToolRegistry
from app.repositories import department as department_repository

_NAME_QUERY_MAX_LENGTH = 200
_MAX_CANDIDATES_RETURNED = 5


class ResolveDepartmentArguments(BaseModel):
    """Untrusted, model-supplied arguments for `resolve_department`.

    `department_name` must be the EXPLICIT name/phrase the caller used —
    never something this tool or the model infers from symptoms. No
    `patient_id`/identity field exists here: resolving a department name
    has no patient-scope concept at all, unlike the scheduling/document
    tools.
    """

    model_config = ConfigDict(extra="forbid")

    department_name: str = Field(min_length=1, max_length=_NAME_QUERY_MAX_LENGTH)


async def _resolve_department(
    arguments: BaseModel, context: ToolExecutionContext, session: AsyncSession
) -> ToolResult:
    assert isinstance(arguments, ResolveDepartmentArguments)  # narrows type for mypy

    matches = await department_repository.search_by_name(
        session,
        organization_id=context.organization_id,
        name_query=arguments.department_name,
        limit=_MAX_CANDIDATES_RETURNED + 1,
    )

    if not matches:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="department_not_found",
            safe_message="No department matching that name was found.",
        )

    if len(matches) > 1:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="ambiguous_department",
            safe_message="More than one department matches that name — please be more specific.",
            data={
                "candidates": [
                    {"id": str(department.id), "name": department.name}
                    for department in matches[:_MAX_CANDIDATES_RETURNED]
                ]
            },
        )

    department = matches[0]
    return ToolResult(
        status=ToolResultStatus.SUCCESS,
        code="department_resolved",
        safe_message=f"Resolved to department: {department.name}.",
        data={
            "department_id": str(department.id),
            "department_name": department.name,
            "department_code": department.code,
        },
    )


RESOLVE_DEPARTMENT_TOOL = ToolDefinition(
    name="resolve_department",
    description=(
        "Resolve an explicitly named department (by name) to its department id, "
        "for a department the caller has already named directly. Never infers a "
        "department from symptoms or a described condition."
    ),
    category=ToolCategory.ADMINISTRATIVE_ROUTING,
    input_schema=ResolveDepartmentArguments,
    handler=_resolve_department,
)


def build_routing_tool_registry() -> ToolRegistry:
    """A registry containing only the Routing specialist's tool —
    mirrors `app.ai.tools.appointment_tools.build_default_registry`'s
    shape, used by this module's own focused tests."""
    registry = ToolRegistry()
    registry.register(RESOLVE_DEPARTMENT_TOOL)
    return registry


@lru_cache
def get_routing_tool_registry() -> ToolRegistry:
    return build_routing_tool_registry()
