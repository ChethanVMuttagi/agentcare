"""Milestone B: `GET .../analytics/summary` tests — end-to-end over real
HTTP (via `client_with_db`), against real PostgreSQL. Mirrors
`tests/api/test_workflow_endpoints_story015.py`'s established pattern.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.core.config import get_settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.approval import ApprovalRequest
from app.models.department import Department
from app.models.facility import Facility
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.patient_document import PatientDocument
from app.models.practitioner import Practitioner
from app.models.practitioner_department import PractitionerDepartment
from app.models.user import User
from app.models.workflow import ActorType, WorkflowRequestType
from app.services.workflow import WorkflowService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeFacility = Callable[..., Awaitable[Facility]]
MakeDepartment = Callable[..., Awaitable[Department]]
MakePractitioner = Callable[..., Awaitable[Practitioner]]
MakeAppointment = Callable[..., Awaitable[Appointment]]
MakePatientDocument = Callable[..., Awaitable[PatientDocument]]
MakeApprovalRequest = Callable[..., Awaitable[ApprovalRequest]]
MakePractitionerDepartment = Callable[..., Awaitable[PractitionerDepartment]]

_SYNTHETIC_JWT_SECRET = "synthetic-test-jwt-secret-do-not-use-in-production-32chars"


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JWT_SECRET_KEY", _SYNTHETIC_JWT_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _auth_header(user: User) -> dict[str, str]:
    token = create_access_token(user.id, get_settings())
    return {"Authorization": f"Bearer {token}"}


def _analytics_url(organization: Organization) -> str:
    return f"/api/v1/organizations/{organization.id}/analytics/summary"


async def _org_with_member(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
    role: Role = Role.ADMIN,
) -> tuple[Organization, User]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=role)
    return org, user


async def test_admin_can_get_analytics_summary(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_facility: MakeFacility,
    make_department: MakeDepartment,
    make_practitioner: MakePractitioner,
    make_practitioner_department: MakePractitionerDepartment,
    make_appointment: MakeAppointment,
    make_patient_document: MakePatientDocument,
    make_approval_request: MakeApprovalRequest,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-analytics-admin"
    )
    patient = await make_patient(org, "PN-ANALYTICS-1")
    facility = await make_facility(org, "ANALYTICS-1")
    department = await make_department(org, facility, "ANALYTICS-1")
    practitioner = await make_practitioner(org)
    await make_practitioner_department(org, practitioner, department)

    await make_appointment(
        org, patient, practitioner, department, status=AppointmentStatus.BOOKED
    )
    await make_appointment(
        org, patient, practitioner, department, status=AppointmentStatus.CANCELLED
    )
    await make_patient_document(org, patient, admin.id)

    service = WorkflowService(db_session)
    completed_run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        patient_id=patient.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=completed_run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    step = await service.create_step(
        organization_id=org.id,
        workflow_run_id=completed_run.id,
        sequence_number=1,
        step_type="coordination",
        agent_name="coordinator",
    )
    await service.start_step(
        organization_id=org.id,
        workflow_run_id=completed_run.id,
        step_id=step.id,
        actor_type=ActorType.AGENT,
        actor_identifier="coordinator",
    )
    await service.record_agent_handoff(
        organization_id=org.id,
        workflow_run_id=completed_run.id,
        step_id=step.id,
        actor_type=ActorType.AGENT,
        actor_identifier="coordinator",
        from_agent="coordinator",
        to_agent="scheduling",
    )
    await service.record_tool_invocation(
        organization_id=org.id,
        workflow_run_id=completed_run.id,
        step_id=step.id,
        actor_type=ActorType.AGENT,
        actor_identifier="scheduling",
        tool_name="book_appointment",
    )
    await service.complete_step(
        organization_id=org.id,
        workflow_run_id=completed_run.id,
        step_id=step.id,
        actor_type=ActorType.AGENT,
        actor_identifier="scheduling",
    )
    await service.complete_workflow(
        organization_id=org.id,
        workflow_run_id=completed_run.id,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )

    pending_run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(admin.id),
    )
    pending_step = await service.create_step(
        organization_id=org.id,
        workflow_run_id=pending_run.id,
        sequence_number=1,
        step_type="coordination",
        agent_name="coordinator",
    )
    await make_approval_request(org, pending_run, pending_step)

    response = await client_with_db.get(_analytics_url(org), headers=_auth_header(admin))
    assert response.status_code == 200
    body = response.json()

    assert body["workflows_total"] == 2
    assert body["workflows_by_status"]["completed"] == 1
    assert body["workflows_by_status"]["pending"] == 1
    assert body["workflows_by_request_type"]["appointment_booking"] == 1
    assert body["workflows_by_request_type"]["document_collection"] == 1

    assert body["appointments_total"] == 2
    assert body["appointments_by_status"]["booked"] == 1
    assert body["appointments_by_status"]["cancelled"] == 1

    assert body["approvals_total"] == 1
    assert body["approvals_by_status"]["pending"] == 1

    assert body["patients_total"] == 1

    assert body["documents_total"] == 1
    assert body["documents_by_status"]["available"] == 1

    assert body["tool_invocations_total"] == 1
    assert body["agent_handoffs_total"] == 1
    assert body["agent_handoffs_by_target"]["scheduling"] == 1

    assert body["generated_at"] is not None


async def test_analytics_summary_with_no_data_returns_zero_counts(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_member(
        make_organization, make_user, make_membership, "api-analytics-empty"
    )

    response = await client_with_db.get(_analytics_url(org), headers=_auth_header(admin))
    assert response.status_code == 200
    body = response.json()

    assert body["workflows_total"] == 0
    assert body["workflows_by_status"] == {}
    assert body["appointments_total"] == 0
    assert body["approvals_total"] == 0
    assert body["patients_total"] == 0
    assert body["documents_total"] == 0
    assert body["tool_invocations_total"] == 0
    assert body["agent_handoffs_total"] == 0
    assert body["agent_handoffs_by_target"] == {}


async def test_staff_can_get_analytics_summary(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-analytics-staff")
    staff = await make_user("api-analytics-staff")
    await make_membership(org, staff, role=Role.STAFF)

    response = await client_with_db.get(_analytics_url(org), headers=_auth_header(staff))
    assert response.status_code == 200


async def test_patient_cannot_get_analytics_summary(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-analytics-patient")
    patient_user = await make_user("api-analytics-patient")
    await make_membership(org, patient_user, role=Role.PATIENT)

    response = await client_with_db.get(_analytics_url(org), headers=_auth_header(patient_user))
    assert response.status_code == 403


async def test_supervisor_cannot_get_analytics_summary(
    client_with_db: AsyncClient,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("api-analytics-supervisor")
    supervisor = await make_user("api-analytics-supervisor")
    await make_membership(org, supervisor, role=Role.SUPERVISOR)

    response = await client_with_db.get(_analytics_url(org), headers=_auth_header(supervisor))
    assert response.status_code == 403
