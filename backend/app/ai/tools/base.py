"""The AgentCare tool contract: `ToolDefinition`, `ToolExecutionContext`,
`ToolResult`.

This is the ONLY interface through which a model decision can cause any
real effect. See docs/TOOLS.md for the full contract and
docs/adr/ADR-0010-llm-and-tool-security-boundary.md "Explicit
Allowlisted Registry" for why this exists instead of any form of
dynamic dispatch.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import Role


class ToolCategory(StrEnum):
    """The administrative capability category a tool belongs to —
    mirrors `app.models.workflow.WorkflowRequestType`'s "small, closed,
    administrative-only" design. Every value here is administrative;
    none may ever represent a clinical capability (diagnosis, treatment,
    medication/dosage) — see docs/AI_SAFETY.md "Healthcare Safety
    Policy"."""

    APPOINTMENT_AVAILABILITY = "appointment_availability"
    APPOINTMENT_BOOKING = "appointment_booking"
    DOCUMENT_STATUS = "document_status"
    ADMINISTRATIVE_ROUTING = "administrative_routing"


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True)
class ToolResult:
    """The outcome of a tool execution — structured, bounded, and safe
    to both persist (in `WorkflowEvent.safe_metadata`) and return to an
    API caller.

    `data`, when present, is bounded, already-safe structured detail
    (e.g. `{"appointment_id": "..."}`) — NEVER a raw SQL error, stack
    trace, connection string, storage key, password hash, JWT, secret,
    or an ORM object's `repr()`. Every tool handler in this codebase is
    responsible for only ever putting safe, deliberately-chosen keys
    here — see `app.ai.tools.appointment_tools` for the pattern.
    """

    status: ToolResultStatus
    code: str
    safe_message: str
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolExecutionContext:
    """SERVER-CREATED trusted identity/authorization context. The LLM
    never constructs this, never sees its fields as something it could
    influence, and has no way to change any value here — see
    `app.ai.orchestration.AgentOrchestrationService._build_tool_context`,
    the ONLY place one is ever created.

    `patient_id` is the caller's OWN linked patient id when `role` is
    `Role.PATIENT` (server-derived, the same
    `PatientService.get_own_patient_record` resolution every other
    patient-self-service route in this codebase uses) — `None` for
    `ADMIN`/`STAFF`, who are not restricted to a single patient. A tool
    handler MUST use `context.patient_id` (never a model-supplied
    argument) whenever `context.role is Role.PATIENT` — see
    docs/TOOLS.md "Trusted vs Untrusted Data".
    """

    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    patient_id: uuid.UUID | None
    workflow_run_id: uuid.UUID
    workflow_step_id: uuid.UUID


ToolHandler = Callable[[BaseModel, ToolExecutionContext, AsyncSession], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDefinition:
    """One explicitly-registered, allowlisted tool.

    `input_schema` is a Pydantic model class (with `extra="forbid"`,
    consistently, so an unexpected argument is a validation error, not a
    silently-ignored field) that UNTRUSTED model-supplied arguments are
    validated against before `handler` is ever called. `handler` must
    call into the REAL existing AgentCare service layer — see
    docs/TOOLS.md "No Fake Success".
    """

    name: str
    description: str
    category: ToolCategory
    input_schema: type[BaseModel]
    handler: ToolHandler


def _describe_field_type(field_schema: dict[str, Any]) -> str:
    """Render one JSON Schema property's type as short plain text —
    handles the plain `{"type": ...}` case and the `anyOf` shape Pydantic
    emits for `X | None` fields (e.g. `BookAppointmentArguments.patient_id`)."""
    if "type" in field_schema:
        json_type = field_schema["type"]
        format_ = field_schema.get("format")
        return f"{json_type} ({format_})" if format_ else str(json_type)
    if "anyOf" in field_schema:
        return " or ".join(_describe_field_type(option) for option in field_schema["anyOf"])
    return "any"


def describe_tool_arguments(tool: ToolDefinition) -> str:
    """Render `tool`'s name, description, and exact argument field names/
    types as plain text, for inclusion in a specialist's system prompt.

    A `ToolCallDecision.arguments` is produced by the model as free-form
    JSON (see `app.ai.decisions` — only the OUTER decision envelope is a
    forced structured tool call; a specific tool's OWN argument shape is
    never itself presented to the model as a schema). Without this text,
    a model has only `tool.description`'s prose to guess argument names
    from, and reliably guesses plausible-sounding names
    (`"doctor"`/`"department"`/`"date"`) instead of the tool's actual,
    `extra="forbid"` field names (`practitioner_id`/`department_id`/
    `on_date`) — every such guess fails `input_schema` validation once
    `ToolRegistry.execute` re-validates it. See docs/TOOLS.md "Scheduling
    Tool Argument Schema"."""
    schema = tool.input_schema.model_json_schema()
    required = set(schema.get("required", []))
    lines = [
        f"TOOL: {tool.name}",
        f"  {tool.description}",
        "  Arguments — use exactly these field names, spelled exactly as shown:",
    ]
    for field_name, field_schema in schema.get("properties", {}).items():
        type_desc = _describe_field_type(field_schema)
        requiredness = "required" if field_name in required else "optional"
        lines.append(f"    - {field_name}: {type_desc}, {requiredness}")
    return "\n".join(lines)
