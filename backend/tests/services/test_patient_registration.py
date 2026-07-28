"""`app.services.patient_registration.PatientRegistrationService` tests
against real PostgreSQL.

Proves the Patient Registration workflow template end-to-end at the
service layer: no-duplicate happy path, a hard `patient_number` conflict
(fails the workflow), a soft name/date-of-birth duplicate (pauses for
approval), and both approval outcomes — all through the SAME durable
`WorkflowRun`/`WorkflowStep`/`WorkflowEvent` engine every other workflow
kind uses, never a parallel one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalRequest, ApprovalStatus, ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.models.workflow import StepStatus, WorkflowStatus
from app.repositories import patient as patient_repository
from app.services.approval import ApprovalService
from app.services.patient_registration import PatientRegistrationService
from app.services.workflow import WorkflowService

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]


async def _org_with_admin(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
) -> tuple[Organization, User]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    return org, user


async def test_registration_with_no_duplicate_completes_and_creates_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "svc-reg-happy"
    )
    service = PatientRegistrationService(db_session)

    run = await service.start_registration(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        patient_number="PN-REG-HAPPY-1",
        first_name="Jordan",
        last_name="Lee",
        date_of_birth=date(1992, 3, 4),
    )

    assert run.status is WorkflowStatus.COMPLETED
    assert run.patient_id is None  # see WorkflowRequestType.PATIENT_REGISTRATION's docstring

    workflow_service = WorkflowService(db_session)
    steps = await workflow_service.list_steps(organization_id=org.id, workflow_run_id=run.id)
    assert [s.step_type for s in steps] == ["patient_duplicate_check", "patient_record_creation"]
    assert [s.status for s in steps] == [StepStatus.COMPLETED, StepStatus.COMPLETED]

    events = await workflow_service.list_events(organization_id=org.id, workflow_run_id=run.id)
    assert [e.event_type.value for e in events] == [
        "workflow_created",
        "workflow_started",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "workflow_completed",
    ]

    patient = await patient_repository.get_by_patient_number(
        db_session, organization_id=org.id, patient_number="PN-REG-HAPPY-1"
    )
    assert patient is not None
    assert patient.first_name == "Jordan"
    assert patient.last_name == "Lee"
    assert patient.date_of_birth == date(1992, 3, 4)


async def test_registration_with_hard_conflict_fails_the_workflow(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "svc-reg-hard-conflict"
    )
    await make_patient(org, "PN-REG-CONFLICT")
    service = PatientRegistrationService(db_session)

    run = await service.start_registration(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        patient_number="PN-REG-CONFLICT",
        first_name="Alex",
        last_name="Nguyen",
        date_of_birth=date(1988, 7, 20),
    )

    assert run.status is WorkflowStatus.FAILED
    assert run.failure_code == "patient_number_conflict"

    workflow_service = WorkflowService(db_session)
    steps = await workflow_service.list_steps(organization_id=org.id, workflow_run_id=run.id)
    assert len(steps) == 1, "step 2 must never be created after a hard conflict"
    assert steps[0].status is StepStatus.FAILED


async def test_registration_with_soft_duplicate_pauses_for_approval(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "svc-reg-soft-duplicate"
    )
    await make_patient(
        org,
        "PN-REG-SOFT-ORIGINAL",
        first_name="Morgan",
        last_name="Kim",
        date_of_birth=date(1995, 11, 2),
    )
    service = PatientRegistrationService(db_session)

    run = await service.start_registration(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        patient_number="PN-REG-SOFT-NEW",
        first_name="Morgan",
        last_name="Kim",
        date_of_birth=date(1995, 11, 2),
    )

    assert run.status is WorkflowStatus.WAITING

    workflow_service = WorkflowService(db_session)
    steps = await workflow_service.list_steps(organization_id=org.id, workflow_run_id=run.id)
    assert len(steps) == 1, "step 2 must never be created before the duplicate is resolved"
    assert steps[0].status is StepStatus.WAITING

    approval = (
        await db_session.execute(
            select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run.id)
        )
    ).scalar_one()
    assert approval.status is ApprovalStatus.PENDING
    assert approval.approval_type is ApprovalType.CUSTOM
    assert approval.requested_by_agent == "patient_registration_workflow"

    # The suspected-duplicate patient was never created.
    new_patient = await patient_repository.get_by_patient_number(
        db_session, organization_id=org.id, patient_number="PN-REG-SOFT-NEW"
    )
    assert new_patient is None


async def test_soft_duplicate_approved_completes_workflow_without_creating_second_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    """Per docs/adr/ADR-0014-end-to-end-administrative-workflows.md: an
    approved duplicate-check completes the ADMINISTRATIVE WORKFLOW's own
    decision — it does NOT automatically create a second patient record."""
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "svc-reg-soft-approve"
    )
    await make_patient(
        org,
        "PN-REG-APPROVE-ORIGINAL",
        first_name="Riley",
        last_name="Chen",
        date_of_birth=date(1991, 4, 9),
    )
    service = PatientRegistrationService(db_session)
    run = await service.start_registration(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        patient_number="PN-REG-APPROVE-NEW",
        first_name="Riley",
        last_name="Chen",
        date_of_birth=date(1991, 4, 9),
    )
    approval = (
        await db_session.execute(
            select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run.id)
        )
    ).scalar_one()

    approval_service = ApprovalService(db_session)
    await approval_service.approve(
        organization_id=org.id,
        approval_id=approval.id,
        approved_by_user=admin.id,
        actor_identifier=str(admin.id),
    )

    workflow_service = WorkflowService(db_session)
    completed_run = await workflow_service.get_workflow(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert completed_run.status is WorkflowStatus.COMPLETED

    still_absent = await patient_repository.get_by_patient_number(
        db_session, organization_id=org.id, patient_number="PN-REG-APPROVE-NEW"
    )
    assert still_absent is None


async def test_soft_duplicate_rejected_cancels_workflow(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "svc-reg-soft-reject"
    )
    await make_patient(
        org,
        "PN-REG-REJECT-ORIGINAL",
        first_name="Sam",
        last_name="Patel",
        date_of_birth=date(1993, 8, 15),
    )
    service = PatientRegistrationService(db_session)
    run = await service.start_registration(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        patient_number="PN-REG-REJECT-NEW",
        first_name="Sam",
        last_name="Patel",
        date_of_birth=date(1993, 8, 15),
    )
    approval = (
        await db_session.execute(
            select(ApprovalRequest).where(ApprovalRequest.workflow_run_id == run.id)
        )
    ).scalar_one()

    approval_service = ApprovalService(db_session)
    await approval_service.reject(
        organization_id=org.id,
        approval_id=approval.id,
        rejected_by_user=admin.id,
        actor_identifier=str(admin.id),
    )

    workflow_service = WorkflowService(db_session)
    cancelled_run = await workflow_service.get_workflow(
        organization_id=org.id, workflow_run_id=run.id
    )
    assert cancelled_run.status is WorkflowStatus.CANCELLED


async def test_registration_with_invalid_user_link_fails_step_two(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "svc-reg-bad-link"
    )
    # A user with no PATIENT-role membership in this org cannot be linked.
    unlinkable_user = await make_user("svc-reg-bad-link-target")
    service = PatientRegistrationService(db_session)

    run = await service.start_registration(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        patient_number="PN-REG-BAD-LINK",
        first_name="Casey",
        last_name="Wu",
        date_of_birth=date(1999, 2, 2),
        user_id=unlinkable_user.id,
    )

    assert run.status is WorkflowStatus.FAILED
    assert run.failure_code == "invalid_patient_link"

    workflow_service = WorkflowService(db_session)
    steps = await workflow_service.list_steps(organization_id=org.id, workflow_run_id=run.id)
    assert len(steps) == 2
    assert steps[0].status is StepStatus.COMPLETED  # duplicate check passed
    assert steps[1].status is StepStatus.FAILED

    not_created = await patient_repository.get_by_patient_number(
        db_session, organization_id=org.id, patient_number="PN-REG-BAD-LINK"
    )
    assert not_created is None
