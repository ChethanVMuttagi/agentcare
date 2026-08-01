"""`app.ai.tools.base.describe_tool_arguments` — no database, no network.

Regression coverage for the bug this function fixes: a specialist's
system prompt previously named its tools only in prose (see
`app.ai.agents.prompts`), never stating a tool's actual argument field
names — so a model reliably invented plausible-but-wrong names
(`"doctor"`, `"date"`) instead of a tool's real, `extra="forbid"` field
names (`practitioner_id`, `on_date`), and every such call failed
`ToolRegistry.execute`'s validation with `invalid_tool_arguments`. See
`app.ai.orchestration.AgentOrchestrationService._specialist_system_prompt`,
the one caller that appends this text to a specialist's prompt at
handoff time.
"""

from __future__ import annotations

from app.ai.tools.appointment_tools import BOOK_APPOINTMENT_TOOL, CHECK_AVAILABILITY_TOOL
from app.ai.tools.base import describe_tool_arguments
from app.ai.tools.document_tools import LIST_PATIENT_DOCUMENTS_TOOL


def test_describe_tool_arguments_lists_every_required_field_by_its_real_name() -> None:
    text = describe_tool_arguments(CHECK_AVAILABILITY_TOOL)

    assert "TOOL: check_availability" in text
    for field_name in ("practitioner_id", "department_id", "on_date", "duration_minutes"):
        line = f"{field_name}: "
        assert line in text
        assert "required" in text.split(line)[1].splitlines()[0]


def test_describe_tool_arguments_marks_uuid_fields_with_their_format() -> None:
    text = describe_tool_arguments(CHECK_AVAILABILITY_TOOL)

    assert "practitioner_id: string (uuid), required" in text
    assert "department_id: string (uuid), required" in text


def test_describe_tool_arguments_marks_optional_fields_as_optional() -> None:
    """`BookAppointmentArguments.patient_id: uuid.UUID | None = None` —
    Pydantic renders this as an `anyOf` schema, not a plain `type` key;
    this is the case `_describe_field_type` exists to handle."""
    text = describe_tool_arguments(BOOK_APPOINTMENT_TOOL)

    assert "patient_id: string (uuid) or null, optional" in text
    assert "practitioner_id: string (uuid), required" in text


def test_describe_tool_arguments_never_forbidden_extra_field_names() -> None:
    """The old failure mode: a model supplying keys like `"doctor"` or
    `"date"` that are not fields on the tool's `input_schema` at all.
    This is a coarse guard that the rendered text only ever advertises
    real field names — not a claim about what a model will actually do."""
    text = describe_tool_arguments(LIST_PATIENT_DOCUMENTS_TOOL)

    assert "TOOL: list_patient_documents" in text
    assert "patient_id: string (uuid) or null, optional" in text
