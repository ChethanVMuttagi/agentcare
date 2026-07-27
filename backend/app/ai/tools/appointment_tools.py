"""The initial real tools: `check_availability` and `book_appointment`.

Both call the REAL existing service layer
(`app.services.availability_query.AvailabilityQueryService`,
`app.services.appointment.AppointmentService`) — never a fake/hardcoded
success path. A tool result may say "booked" only because
`AppointmentService.book_appointment` genuinely persisted a row; a
result may say availability exists only because
`AvailabilityQueryService.list_available_times` genuinely computed it.
See docs/TOOLS.md "No Fake Success".

Depth over breadth (deliberate STORY-010 scope decision): reschedule
and cancel tools are NOT implemented here — see docs/TOOLS.md "Initial
Tools" for the reasoning. Adding them later means adding two more
`ToolDefinition`s to `build_default_registry`, following the exact same
pattern as `book_appointment` below; no change to the contract itself.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
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
from app.models.membership import Role
from app.services.appointment import (
    AppointmentConflictError,
    AppointmentInPastError,
    AppointmentNotFoundError,
    AppointmentOutsideAvailabilityError,
    AppointmentService,
    DepartmentInactiveError,
    InvalidAppointmentDurationError,
    PatientInactiveError,
    PractitionerInactiveError,
    PractitionerNotAssignedError,
)
from app.services.availability_query import AvailabilityQueryService

_MAX_RETURNED_SLOTS = 10
_MAX_DURATION_MINUTES = 480


class CheckAvailabilityArguments(BaseModel):
    """Untrusted, model-supplied arguments for `check_availability`."""

    model_config = ConfigDict(extra="forbid")

    practitioner_id: uuid.UUID
    department_id: uuid.UUID
    on_date: date
    duration_minutes: int = Field(gt=0, le=_MAX_DURATION_MINUTES)


class BookAppointmentArguments(BaseModel):
    """Untrusted, model-supplied arguments for `book_appointment`.

    `patient_id` is used ONLY when the caller is `ADMIN`/`STAFF` (the
    same "who is this booking for" choice the human-driven booking API
    already lets `ADMIN`/`STAFF` make). For a `PATIENT`-role caller this
    field is READ BUT NEVER TRUSTED — the handler always substitutes
    `context.patient_id` instead, exactly mirroring
    `app.api.v1.endpoints.appointments._resolve_booking_patient_id`.
    """

    model_config = ConfigDict(extra="forbid")

    practitioner_id: uuid.UUID
    department_id: uuid.UUID
    start_at: datetime
    duration_minutes: int = Field(gt=0, le=_MAX_DURATION_MINUTES)
    patient_id: uuid.UUID | None = None


async def _check_availability(
    arguments: BaseModel, context: ToolExecutionContext, session: AsyncSession
) -> ToolResult:
    assert isinstance(arguments, CheckAvailabilityArguments)  # narrows type for mypy
    service = AvailabilityQueryService(session)
    slots = await service.list_available_times(
        organization_id=context.organization_id,
        practitioner_id=arguments.practitioner_id,
        department_id=arguments.department_id,
        on_date=arguments.on_date,
        duration_minutes=arguments.duration_minutes,
    )
    if not slots:
        return ToolResult(
            status=ToolResultStatus.SUCCESS,
            code="no_availability",
            safe_message="No available times were found for that request.",
            data={"available_times": []},
        )
    return ToolResult(
        status=ToolResultStatus.SUCCESS,
        code="availability_found",
        safe_message=f"Found {len(slots)} available time(s).",
        data={
            "available_times": [
                {"start_at": slot.start_at.isoformat(), "end_at": slot.end_at.isoformat()}
                for slot in slots[:_MAX_RETURNED_SLOTS]
            ]
        },
    )


async def _book_appointment(
    arguments: BaseModel, context: ToolExecutionContext, session: AsyncSession
) -> ToolResult:
    assert isinstance(arguments, BookAppointmentArguments)  # narrows type for mypy

    if context.role is Role.PATIENT:
        # Server-derived identity ALWAYS wins — a model-supplied
        # `patient_id` (whatever the caller asked the model to say) is
        # never consulted for a PATIENT-role caller. See the class
        # docstring and docs/AI_SAFETY.md "Authorization Boundary".
        if context.patient_id is None:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                code="patient_not_linked",
                safe_message="No patient record is linked to your account.",
            )
        patient_id = context.patient_id
    else:
        if arguments.patient_id is None:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                code="patient_id_required",
                safe_message="A patient must be specified for this booking.",
            )
        patient_id = arguments.patient_id

    service = AppointmentService(session)
    try:
        appointment = await service.book_appointment(
            organization_id=context.organization_id,
            patient_id=patient_id,
            practitioner_id=arguments.practitioner_id,
            department_id=arguments.department_id,
            start_at=arguments.start_at,
            duration_minutes=arguments.duration_minutes,
        )
    except AppointmentConflictError:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="appointment_conflict",
            safe_message="That time is no longer available.",
        )
    except AppointmentNotFoundError:
        # Covers a missing OR cross-tenant patient/practitioner/
        # department — deliberately the same safe, non-disclosing
        # outcome as "inactive" below, mirroring
        # `app.api.v1.endpoints.appointments`'s non-disclosure rule
        # (never revealing that a resource exists under a different
        # organization).
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="resource_not_found",
            safe_message="This booking cannot be completed right now.",
        )
    except (PatientInactiveError, PractitionerInactiveError, DepartmentInactiveError):
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="resource_inactive",
            safe_message="This booking cannot be completed right now.",
        )
    except PractitionerNotAssignedError:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="practitioner_not_assigned",
            safe_message="This practitioner is not assigned to that department.",
        )
    except InvalidAppointmentDurationError:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="invalid_duration",
            safe_message="The requested appointment duration is not valid.",
        )
    except AppointmentInPastError:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="appointment_in_past",
            safe_message="The requested time is in the past.",
        )
    except AppointmentOutsideAvailabilityError:
        return ToolResult(
            status=ToolResultStatus.FAILURE,
            code="outside_availability",
            safe_message="The requested time is outside this practitioner's availability.",
        )

    return ToolResult(
        status=ToolResultStatus.SUCCESS,
        code="appointment_booked",
        safe_message="The appointment was booked successfully.",
        data={
            "appointment_id": str(appointment.id),
            "start_at": appointment.start_at.isoformat(),
            "end_at": appointment.end_at.isoformat(),
        },
    )


CHECK_AVAILABILITY_TOOL = ToolDefinition(
    name="check_availability",
    description=(
        "Check whether a practitioner has any available appointment times, on a "
        "given date, within a department, for a given duration."
    ),
    category=ToolCategory.APPOINTMENT_AVAILABILITY,
    input_schema=CheckAvailabilityArguments,
    handler=_check_availability,
)

BOOK_APPOINTMENT_TOOL = ToolDefinition(
    name="book_appointment",
    description=(
        "Book an appointment for a patient with a practitioner, within a department, "
        "at a specific start time and duration."
    ),
    category=ToolCategory.APPOINTMENT_BOOKING,
    input_schema=BookAppointmentArguments,
    handler=_book_appointment,
)


def build_default_registry() -> ToolRegistry:
    """The `ToolRegistry` this codebase actually uses. Adding a new tool
    means adding one more `registry.register(...)` call here."""
    registry = ToolRegistry()
    registry.register(CHECK_AVAILABILITY_TOOL)
    registry.register(BOOK_APPOINTMENT_TOOL)
    return registry


@lru_cache
def get_tool_registry() -> ToolRegistry:
    """FastAPI-dependency-friendly, cached accessor — the registry is
    stateless and identical on every call, so it is built exactly once
    per process (mirrors `app.storage.factory.get_document_storage`)."""
    return build_default_registry()
