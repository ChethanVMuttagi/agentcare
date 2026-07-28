"""`WorkflowService.mark_step_waiting`/`resume_step`/`record_approval_event`
tests (STORY-014) against real PostgreSQL — the step-level pause/resume
primitives `app.services.approval.ApprovalService` builds on. Mirrors
`tests/services/test_workflow.py`'s style for the equivalent run-level
`mark_waiting`/`resume_workflow` behavior.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User
from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStep,
)
from app.services.workflow import (
    WorkflowConflictError,
    WorkflowService,
    WorkflowStepNotFoundError,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]

_ACTOR_TYPE = ActorType.SYSTEM
_ACTOR_ID = "synthetic-service-test"


async def _running_step(
    service: WorkflowService,
    *,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
) -> tuple[Organization, User, WorkflowRun, WorkflowStep]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    await service.start_workflow(
        organization_id=org.id,
        workflow_run_id=run.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    step = await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="coordination"
    )
    step = await service.start_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    return org, user, run, step


async def _pending_step(
    service: WorkflowService,
    *,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
) -> tuple[Organization, User, WorkflowRun, WorkflowStep]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    run = await service.create_workflow(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        patient_id=None,
        actor_type=ActorType.USER,
        actor_identifier=str(user.id),
    )
    step = await service.create_step(
        organization_id=org.id, workflow_run_id=run.id, sequence_number=1, step_type="coordination"
    )
    return org, user, run, step


async def test_mark_step_waiting_transitions_and_records_event(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, step = await _running_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-step-wait",
    )

    waiting = await service.mark_step_waiting(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
        safe_metadata={"approval_id": "synthetic"},
    )
    assert waiting.status is StepStatus.WAITING

    events = await service.list_events(organization_id=org.id, workflow_run_id=run.id)
    assert events[-1].event_type is WorkflowEventType.STEP_WAITING
    assert events[-1].workflow_step_id == step.id


async def test_mark_step_waiting_rejects_from_pending(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, step = await _pending_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-step-wait-invalid",
    )

    with pytest.raises(WorkflowConflictError):
        await service.mark_step_waiting(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )


async def test_mark_step_waiting_unknown_step_raises_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, _step = await _running_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-step-wait-unknown",
    )

    with pytest.raises(WorkflowStepNotFoundError):
        await service.mark_step_waiting(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=uuid.uuid4(),
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )


async def test_resume_step_transitions_and_records_event(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, step = await _running_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-step-resume",
    )
    await service.mark_step_waiting(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )

    resumed = await service.resume_step(
        organization_id=org.id,
        workflow_run_id=run.id,
        step_id=step.id,
        actor_type=_ACTOR_TYPE,
        actor_identifier=_ACTOR_ID,
    )
    assert resumed.status is StepStatus.RUNNING

    events = await service.list_events(organization_id=org.id, workflow_run_id=run.id)
    assert events[-1].event_type is WorkflowEventType.STEP_RESUMED


async def test_resume_step_rejects_from_running(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, step = await _running_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-step-resume-invalid",
    )

    with pytest.raises(WorkflowConflictError):
        await service.resume_step(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
        )


async def test_record_approval_event_accepts_all_three_types(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, step = await _running_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-approval-events",
    )

    for event_type in (
        WorkflowEventType.APPROVAL_REQUESTED,
        WorkflowEventType.APPROVAL_GRANTED,
        WorkflowEventType.APPROVAL_REJECTED,
    ):
        event = await service.record_approval_event(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
            event_type=event_type,
            safe_metadata={"approval_id": "synthetic"},
        )
        assert event.event_type is event_type


async def test_record_approval_event_rejects_non_approval_event_type(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, step = await _running_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-approval-events-invalid",
    )

    with pytest.raises(ValueError, match="not an approval lifecycle event type"):
        await service.record_approval_event(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
            event_type=WorkflowEventType.WORKFLOW_WAITING,
        )


async def test_record_approval_event_unknown_step_raises_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    service = WorkflowService(db_session)
    org, _user, run, _step = await _running_step(
        service,
        make_organization=make_organization,
        make_user=make_user,
        make_membership=make_membership,
        suffix="wf-approval-events-unknown",
    )

    with pytest.raises(WorkflowStepNotFoundError):
        await service.record_approval_event(
            organization_id=org.id,
            workflow_run_id=run.id,
            step_id=uuid.uuid4(),
            actor_type=_ACTOR_TYPE,
            actor_identifier=_ACTOR_ID,
            event_type=WorkflowEventType.APPROVAL_REQUESTED,
        )
