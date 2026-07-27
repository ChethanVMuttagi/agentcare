"""`app.ai.tools` tests against real PostgreSQL.

`ToolRegistry` tests need no database. `check_availability`/
`book_appointment` tests call the REAL `AvailabilityQueryService`/
`AppointmentService` against real PostgreSQL — proving a tool result
reflects genuine service/DB behavior, never a hardcoded success path.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.appointment_tools import (
    BOOK_APPOINTMENT_TOOL,
    CHECK_AVAILABILITY_TOOL,
    build_default_registry,
)
from app.ai.tools.base import ToolCategory, ToolDefinition, ToolExecutionContext, ToolResultStatus
from app.ai.tools.registry import ToolRegistry
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.practitioner import Practitioner
from app.models.practitioner_availability import DayOfWeek, PractitionerAvailability
from app.models.practitioner_department import PractitionerDepartment

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAssignment = Callable[..., Awaitable[PractitionerDepartment]]
MakePatient = Callable[..., Awaitable[Patient]]

_FUTURE = datetime.now(UTC) + timedelta(days=30)


async def _wide_open_scenario(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
    suffix: str,
) -> tuple[Organization, Department, Practitioner, Patient]:
    org = await make_organization(suffix)
    facility = await make_facility(org, suffix)
    department = await make_department(org, facility, suffix.upper())
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)
    patient = await make_patient(org, f"PN-{suffix}")

    for day in DayOfWeek:
        window = PractitionerAvailability(
            organization_id=org.id,
            practitioner_id=practitioner.id,
            department_id=department.id,
            day_of_week=day,
            start_time=time(0, 0),
            end_time=time(23, 59, 59),
            timezone="UTC",
        )
        db_session.add(window)
    await db_session.flush()

    return org, department, practitioner, patient


def _context(
    *, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role, patient_id: uuid.UUID | None
) -> ToolExecutionContext:
    return ToolExecutionContext(
        organization_id=organization_id,
        user_id=user_id,
        role=role,
        patient_id=patient_id,
        workflow_run_id=uuid.uuid4(),
        workflow_step_id=uuid.uuid4(),
    )


# --- ToolRegistry ---


def test_registry_get_returns_registered_tool() -> None:
    registry = build_default_registry()
    tool = registry.get("book_appointment")
    assert tool is not None
    assert tool.name == "book_appointment"


def test_registry_get_returns_none_for_unknown_tool() -> None:
    registry = build_default_registry()
    assert registry.get("admin.make_me_admin") is None
    assert registry.get("os.system") is None
    assert registry.get("run_sql") is None


def test_registry_list_allowed_returns_all_registered_tools() -> None:
    registry = build_default_registry()
    names = {tool.name for tool in registry.list_allowed()}
    assert names == {"check_availability", "book_appointment"}


def test_registry_rejects_duplicate_registration() -> None:
    registry = ToolRegistry()
    registry.register(CHECK_AVAILABILITY_TOOL)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(CHECK_AVAILABILITY_TOOL)


async def test_registry_execute_unknown_tool_is_controlled_rejection() -> None:
    registry = build_default_registry()
    context = _context(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )
    result = await registry.execute("admin.make_me_admin", {}, context, None)  # type: ignore[arg-type]
    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "unknown_tool"


async def test_registry_execute_invalid_arguments_is_controlled_rejection() -> None:
    registry = build_default_registry()
    context = _context(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )
    result = await registry.execute(
        "check_availability", {"practitioner_id": "not-a-uuid"}, context, None  # type: ignore[arg-type]
    )
    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "invalid_tool_arguments"


async def test_registry_execute_rejects_unexpected_extra_arguments() -> None:
    registry = build_default_registry()
    context = _context(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )
    result = await registry.execute(
        "check_availability",
        {
            "practitioner_id": str(uuid.uuid4()),
            "department_id": str(uuid.uuid4()),
            "on_date": "2026-01-01",
            "duration_minutes": 30,
            "unexpected_field": "smuggled",
        },
        context,
        None,  # type: ignore[arg-type]
    )
    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "invalid_tool_arguments"


async def test_registry_execute_catches_unexpected_handler_exception() -> None:
    """A handler that raises unexpectedly must never propagate a raw
    exception out of the registry — always a controlled `ToolResult`."""

    class _BoomArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def _boom(arguments: BaseModel, context: ToolExecutionContext, session) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated unexpected failure with a database URL inside")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="boom",
            description="test",
            category=ToolCategory.APPOINTMENT_AVAILABILITY,
            input_schema=_BoomArguments,
            handler=_boom,  # type: ignore[arg-type]
        )
    )
    context = _context(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )
    result = await registry.execute("boom", {}, context, None)  # type: ignore[arg-type]
    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "tool_execution_failed"
    assert "database URL" not in result.safe_message


# --- check_availability (real service/DB) ---


async def test_check_availability_finds_real_slots(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-avail-found",
    )
    registry = build_default_registry()
    context = _context(
        organization_id=org.id, user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )

    result = await registry.execute(
        "check_availability",
        {
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "on_date": _FUTURE.date().isoformat(),
            "duration_minutes": 30,
        },
        context,
        db_session,
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.code == "availability_found"
    assert result.data is not None
    assert len(result.data["available_times"]) > 0


async def test_check_availability_cross_tenant_practitioner_returns_no_availability(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org_a, department_a, practitioner_a, _p = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-avail-cross-a",
    )
    org_b = await make_organization("tool-avail-cross-b")
    context = _context(
        organization_id=org_b.id, user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )
    registry = build_default_registry()

    result = await registry.execute(
        "check_availability",
        {
            "practitioner_id": str(practitioner_a.id),
            "department_id": str(department_a.id),
            "on_date": _FUTURE.date().isoformat(),
            "duration_minutes": 30,
        },
        context,
        db_session,
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.code == "no_availability"
    assert result.data == {"available_times": []}


# --- book_appointment (real service/DB) ---


async def test_book_appointment_admin_succeeds_and_persists(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-book-admin",
    )
    registry = build_default_registry()
    context = _context(
        organization_id=org.id, user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )

    result = await registry.execute(
        "book_appointment",
        {
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _FUTURE.isoformat(),
            "duration_minutes": 30,
            "patient_id": str(patient.id),
        },
        context,
        db_session,
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.code == "appointment_booked"
    assert result.data is not None
    assert "appointment_id" in result.data

    from app.repositories import appointment as appointment_repository

    appointment_id = uuid.UUID(result.data["appointment_id"])
    persisted = await appointment_repository.get_by_id(
        db_session, organization_id=org.id, appointment_id=appointment_id
    )
    assert persisted is not None
    assert persisted.patient_id == patient.id


async def test_book_appointment_admin_without_patient_id_is_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-book-admin-nopat",
    )
    registry = build_default_registry()
    context = _context(
        organization_id=org.id, user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )

    result = await registry.execute(
        "book_appointment",
        {
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _FUTURE.isoformat(),
            "duration_minutes": 30,
        },
        context,
        db_session,
    )

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "patient_id_required"


async def test_book_appointment_patient_role_uses_own_patient_id_never_argument(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    """The mandatory adversarial case: even if the model supplies
    ANOTHER patient's UUID, a PATIENT-role caller's OWN linked patient
    id is what actually gets booked — never the argument."""
    org, department, practitioner, own_patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-book-patient-self",
    )
    other_patient = await make_patient(org, "PN-tool-book-patient-other")
    registry = build_default_registry()
    context = _context(
        organization_id=org.id,
        user_id=uuid.uuid4(),
        role=Role.PATIENT,
        patient_id=own_patient.id,
    )

    result = await registry.execute(
        "book_appointment",
        {
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _FUTURE.isoformat(),
            "duration_minutes": 30,
            "patient_id": str(other_patient.id),  # adversarial: model-supplied
        },
        context,
        db_session,
    )

    assert result.status is ToolResultStatus.SUCCESS
    from app.repositories import appointment as appointment_repository

    appointment_id = uuid.UUID(result.data["appointment_id"])  # type: ignore[index]
    persisted = await appointment_repository.get_by_id(
        db_session, organization_id=org.id, appointment_id=appointment_id
    )
    assert persisted is not None
    assert persisted.patient_id == own_patient.id
    assert persisted.patient_id != other_patient.id


async def test_book_appointment_patient_role_without_linked_patient_is_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, _patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-book-patient-nolink",
    )
    registry = build_default_registry()
    context = _context(
        organization_id=org.id, user_id=uuid.uuid4(), role=Role.PATIENT, patient_id=None
    )

    result = await registry.execute(
        "book_appointment",
        {
            "practitioner_id": str(practitioner.id),
            "department_id": str(department.id),
            "start_at": _FUTURE.isoformat(),
            "duration_minutes": 30,
        },
        context,
        db_session,
    )

    assert result.status is ToolResultStatus.FAILURE
    assert result.code == "patient_not_linked"


async def test_book_appointment_cross_tenant_practitioner_is_rejected(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org_a, department_a, practitioner_a, _p = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-book-cross-a",
    )
    org_b = await make_organization("tool-book-cross-b")
    patient_b = await make_patient(org_b, "PN-tool-book-cross-b")
    registry = build_default_registry()
    context = _context(
        organization_id=org_b.id, user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )

    result = await registry.execute(
        "book_appointment",
        {
            "practitioner_id": str(practitioner_a.id),  # belongs to org_a
            "department_id": str(department_a.id),
            "start_at": _FUTURE.isoformat(),
            "duration_minutes": 30,
            "patient_id": str(patient_b.id),
        },
        context,
        db_session,
    )

    assert result.status is ToolResultStatus.FAILURE
    # Existing tenant-safe service path treats a cross-tenant
    # practitioner/department the same as a missing/inactive/unassigned
    # one — no cross-tenant information is disclosed.
    assert result.code in ("resource_not_found", "resource_inactive", "practitioner_not_assigned")


async def test_book_appointment_conflict_returns_safe_failure(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakeAssignment,
    make_patient: MakePatient,
) -> None:
    org, department, practitioner, patient = await _wide_open_scenario(
        db_session,
        make_organization,
        make_facility,
        make_department,
        make_practitioner,
        make_practitioner_department,
        make_patient,
        "tool-book-conflict",
    )
    registry = build_default_registry()
    context = _context(
        organization_id=org.id, user_id=uuid.uuid4(), role=Role.ADMIN, patient_id=None
    )
    arguments = {
        "practitioner_id": str(practitioner.id),
        "department_id": str(department.id),
        "start_at": _FUTURE.isoformat(),
        "duration_minutes": 30,
        "patient_id": str(patient.id),
    }

    first = await registry.execute("book_appointment", arguments, context, db_session)
    assert first.status is ToolResultStatus.SUCCESS

    other_patient = await make_patient(org, "PN-tool-book-conflict-2")
    second_arguments = dict(arguments, patient_id=str(other_patient.id))
    second = await registry.execute(
        "book_appointment", second_arguments, context, db_session
    )
    assert second.status is ToolResultStatus.FAILURE
    assert second.code == "appointment_conflict"
    assert second.safe_message
    assert "IntegrityError" not in second.safe_message
    assert "sqlalchemy" not in second.safe_message.lower()


def test_book_appointment_tool_definition_category() -> None:
    assert BOOK_APPOINTMENT_TOOL.category is ToolCategory.APPOINTMENT_BOOKING
    assert CHECK_AVAILABILITY_TOOL.category is ToolCategory.APPOINTMENT_AVAILABILITY
