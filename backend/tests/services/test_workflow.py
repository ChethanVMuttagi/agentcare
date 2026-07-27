"""app.services.workflow.WorkflowService tests against real PostgreSQL.

Sequential correctness only — the mandatory GENUINE concurrency proof
lives in tests/db/test_workflow_concurrency.py (real, independent,
concurrently-executing transactions), and the mandatory persistence/
restart proof lives in tests/db/test_workflow_persistence.py. This file
proves business-rule validation, state transitions, transition+event
atomicity, and safe failure-metadata recording.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
)
from app.services.workflow import (
    WorkflowConflictError,
    WorkflowIdempotencyKeyConflictError,
    WorkflowInitiatorNotActiveMemberError,
    WorkflowNotFoundError,
    WorkflowPatientInactiveError,
    WorkflowPatientNotFoundError,
    WorkflowService,
    WorkflowStepNotFoundError,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeWorkflowRun = Callable[..., Awaitable[WorkflowRun]]

_ACTOR_TYPE = ActorType.SYSTEM
_ACTOR_ID = "synthetic-service-test"


async def _scenario(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
) -> tuple[Organization, User]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    return org, user


# --- create_workflow ---


async def test_create_workflow_succeeds_without_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-create-nopt")
    service = WorkflowService(db_session)

    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )

    assert run.status is WorkflowStatus.PENDING
    assert run.patient_id is None
    assert len(run.correlation_id) == 32

    events = await service.list_events(organization_id=org.id, workflow_run_id=run.id)
    assert [e.event_type for e in events] == [WorkflowEventType.WORKFLOW_CREATED]


async def test_create_workflow_succeeds_with_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-create-pt")
    patient = await make_patient(org, "PN-svc-create-pt")
    service = WorkflowService(db_session)

    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        patient_id=patient.id,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    assert run.patient_id == patient.id


async def test_create_workflow_generates_distinct_correlation_ids(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-create-corr")
    service = WorkflowService(db_session)

    run_a = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.FOLLOW_UP,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    run_b = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.FOLLOW_UP,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    assert run_a.correlation_id != run_b.correlation_id


async def test_create_workflow_rejects_inactive_initiator(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org = await make_organization("svc-create-inactive-init")
    user = await make_user("svc-create-inactive-init")
    await make_membership(org, user, role=Role.STAFF, is_active=False)
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowInitiatorNotActiveMemberError):
        await service.create_workflow(
            organization_id=org.id,
            initiated_by_user_id=user.id,
            request_type=WorkflowRequestType.FOLLOW_UP,
            patient_id=None,
            actor_type=ActorType.USER,
            actor_identifier=str(user.id),
        )


async def test_create_workflow_rejects_missing_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-create-nopat")
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowPatientNotFoundError):
        await service.create_workflow(
            organization_id=org.id,
            initiated_by_user_id=user.id,
            request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
            patient_id=uuid.uuid4(),
            actor_type=ActorType.USER,
            actor_identifier=str(user.id),
        )


async def test_create_workflow_rejects_inactive_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-create-inpat")
    patient = await make_patient(org, "PN-svc-create-inpat", is_active=False)
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowPatientInactiveError):
        await service.create_workflow(
            organization_id=org.id,
            initiated_by_user_id=user.id,
            request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
            patient_id=patient.id,
            actor_type=ActorType.USER,
            actor_identifier=str(user.id),
        )


async def test_create_workflow_rejects_duplicate_idempotency_key(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-create-idem")
    service = WorkflowService(db_session)

    await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.FOLLOW_UP,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
        idempotency_key="svc-create-idem-key",
    )

    with pytest.raises(WorkflowIdempotencyKeyConflictError):
        await service.create_workflow(
            organization_id=org.id,
            initiated_by_user_id=user.id,
            request_type=WorkflowRequestType.FOLLOW_UP,
            patient_id=None,
            actor_type=ActorType.USER,
            actor_identifier=str(user.id),
            idempotency_key="svc-create-idem-key",
        )


# --- get_workflow / list_workflows ---


async def test_get_workflow_not_found(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-get-missing")
    service = WorkflowService(db_session)
    with pytest.raises(WorkflowNotFoundError):
        await service.get_workflow(organization_id=org.id, workflow_run_id=uuid.uuid4())


async def test_get_workflow_with_patient_mismatch_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-get-mismatch")
    patient_a = await make_patient(org, "PN-svc-get-mismatch-a")
    patient_b = await make_patient(org, "PN-svc-get-mismatch-b")
    run = await make_workflow_run(org, user.id, patient=patient_a)
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowNotFoundError):
        await service.get_workflow(
            organization_id=org.id, workflow_run_id=run.id, patient_id=patient_b.id
        )


async def test_list_workflows_filters_by_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-list-patient")
    patient_a = await make_patient(org, "PN-svc-list-patient-a")
    patient_b = await make_patient(org, "PN-svc-list-patient-b")
    run_a = await make_workflow_run(org, user.id, patient=patient_a)
    await make_workflow_run(org, user.id, patient=patient_b)
    service = WorkflowService(db_session)

    results = await service.list_workflows(organization_id=org.id, patient_id=patient_a.id)
    assert [r.id for r in results] == [run_a.id]


# --- run transitions ---


async def test_start_workflow_succeeds_and_sets_started_at(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-start-ok")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)

    started = await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert started.status is WorkflowStatus.RUNNING
    assert started.started_at is not None

    events = await service.list_events(organization_id=org.id, workflow_run_id=run.id)
    assert [e.event_type for e in events] == [WorkflowEventType.WORKFLOW_STARTED]


async def test_start_workflow_not_found(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("svc-start-missing")
    service = WorkflowService(db_session)
    with pytest.raises(WorkflowNotFoundError):
        await service.start_workflow(
            organization_id=org.id,
            workflow_run_id=uuid.uuid4(),
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )


async def test_start_workflow_rejects_already_running(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-start-twice")
    run = await make_workflow_run(org, user.id, status=WorkflowStatus.RUNNING)
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowConflictError):
        await service.start_workflow(
            organization_id=org.id,
            workflow_run_id=run.id,
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )


async def test_mark_waiting_and_resume_round_trip(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-wait-resume")
    run = await make_workflow_run(org, user.id, status=WorkflowStatus.RUNNING)
    service = WorkflowService(db_session)

    waiting = await service.mark_waiting(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert waiting.status is WorkflowStatus.WAITING

    resumed = await service.resume_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert resumed.status is WorkflowStatus.RUNNING

    events = await service.list_events(organization_id=org.id, workflow_run_id=run.id)
    assert [e.event_type for e in events] == [
        WorkflowEventType.WORKFLOW_WAITING,
        WorkflowEventType.WORKFLOW_RESUMED,
    ]


async def test_complete_workflow_sets_completed_at(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-complete")
    run = await make_workflow_run(org, user.id, status=WorkflowStatus.RUNNING)
    service = WorkflowService(db_session)

    completed = await service.complete_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert completed.status is WorkflowStatus.COMPLETED
    assert completed.completed_at is not None


async def test_fail_workflow_records_safe_failure_metadata(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-fail")
    run = await make_workflow_run(org, user.id, status=WorkflowStatus.RUNNING)
    service = WorkflowService(db_session)

    failed = await service.fail_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        failure_code="downstream_timeout",
        failure_message_safe="The downstream scheduling service did not respond in time.",
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert failed.status is WorkflowStatus.FAILED
    assert failed.failure_code == "downstream_timeout"
    assert failed.failure_message_safe == (
        "The downstream scheduling service did not respond in time."
    )
    assert failed.completed_at is not None


async def test_cancel_workflow_from_pending(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-cancel")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)

    cancelled = await service.cancel_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert cancelled.status is WorkflowStatus.CANCELLED


async def test_cancel_workflow_rejects_terminal_state(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-cancel-term")
    run = await make_workflow_run(org, user.id, status=WorkflowStatus.COMPLETED)
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowConflictError):
        await service.cancel_workflow(
            organization_id=org.id,
            workflow_run_id=run.id,
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )


# --- steps ---


async def test_create_step_sets_current_step(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-step-create")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)

    step = await service.create_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        sequence_number=1,
        step_type="synthetic_lookup",
    )
    assert step.status is StepStatus.PENDING

    refreshed = await service.get_workflow(organization_id=org.id, workflow_run_id=run.id)
    assert refreshed.current_step == 1


async def test_create_step_rejects_terminal_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-step-term")
    run = await make_workflow_run(org, user.id, status=WorkflowStatus.CANCELLED)
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowConflictError):
        await service.create_step(
            organization_id=org.id,
            workflow_run_id=run.id,
            sequence_number=1,
            step_type="synthetic",
        )


async def test_step_lifecycle_start_complete(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-step-life")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)
    step = await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="synthetic"
    )

    started = await service.start_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert started.status is StepStatus.RUNNING
    assert started.attempt_count == 1
    assert started.started_at is not None

    completed = await service.complete_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert completed.status is StepStatus.COMPLETED
    assert completed.completed_at is not None

    events = await service.list_events(organization_id=org.id, workflow_run_id=run.id)
    assert [e.event_type for e in events] == [
        WorkflowEventType.STEP_STARTED,
        WorkflowEventType.STEP_COMPLETED,
    ]
    assert all(e.workflow_step_id == step.id for e in events)


async def test_step_fail_records_safe_failure_metadata(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-step-fail")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)
    step = await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="synthetic"
    )
    await service.start_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )

    failed = await service.fail_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        failure_code="tool_call_failed",
        failure_message_safe="The scheduling tool returned an error.",
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert failed.status is StepStatus.FAILED
    assert failed.failure_code == "tool_call_failed"


async def test_step_skip_from_pending(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-step-skip")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)
    step = await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="synthetic"
    )

    skipped = await service.skip_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert skipped.status is StepStatus.SKIPPED


async def test_step_transition_rejects_invalid_from_completed(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-step-invalid")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)
    step = await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="synthetic"
    )
    await service.start_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    await service.complete_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )

    with pytest.raises(WorkflowConflictError):
        await service.start_step(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )


async def test_step_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "svc-step-missing")
    run = await make_workflow_run(org, user.id)
    service = WorkflowService(db_session)

    with pytest.raises(WorkflowStepNotFoundError):
        await service.start_step(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=uuid.uuid4(),
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )
