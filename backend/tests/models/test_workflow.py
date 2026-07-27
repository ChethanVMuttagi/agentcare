"""WorkflowRun/WorkflowStep/WorkflowEvent model tests against real
PostgreSQL.

See tests/conftest.py for why these require AGENTCARE_TEST_POSTGRES_URL
and how test data is guaranteed not to persist.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]
MakeWorkflowRun = Callable[..., Awaitable[WorkflowRun]]
MakeWorkflowStep = Callable[..., Awaitable[WorkflowStep]]
MakeWorkflowEvent = Callable[..., Awaitable[WorkflowEvent]]


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


async def test_run_id_is_generated_uuid(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-uuid")
    run = await make_workflow_run(org, user.id)
    assert isinstance(run.id, uuid.UUID)


async def test_run_status_defaults_to_pending(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-default")
    run = WorkflowRun(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        status=WorkflowStatus.PENDING,
        correlation_id=uuid.uuid4().hex,
    )
    db_session.add(run)
    await db_session.flush()
    assert run.status is WorkflowStatus.PENDING
    assert run.current_step is None
    assert run.patient_id is None


async def test_run_timestamps_are_set_and_timezone_aware(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-ts")
    run = await make_workflow_run(org, user.id)
    assert run.created_at is not None
    assert run.updated_at is not None
    assert run.created_at.tzinfo is not None


async def test_run_rejects_cross_tenant_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org_a, user_a = await _scenario(make_organization, make_user, make_membership, "wf-cross-a")
    org_b = await make_organization("wf-cross-b")
    patient_b = await make_patient(org_b, "PN-wf-cross-b")

    run = WorkflowRun(
        organization_id=org_a.id,
        patient_id=patient_b.id,
        initiated_by_user_id=user_a.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        status=WorkflowStatus.PENDING,
        correlation_id=uuid.uuid4().hex,
    )
    db_session.add(run)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_run_rejects_initiator_with_no_membership(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
) -> None:
    org = await make_organization("wf-no-member")
    outsider = await make_user("wf-no-member-outsider")

    run = WorkflowRun(
        organization_id=org.id,
        initiated_by_user_id=outsider.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        status=WorkflowStatus.PENDING,
        correlation_id=uuid.uuid4().hex,
    )
    db_session.add(run)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_run_correlation_id_must_be_unique(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-corr-dup")
    shared = uuid.uuid4().hex
    await make_workflow_run(org, user.id, correlation_id=shared)

    duplicate = WorkflowRun(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        status=WorkflowStatus.PENDING,
        correlation_id=shared,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError, match="uq_workflow_runs_correlation_id"):
        await db_session.flush()
    await db_session.rollback()


async def test_run_idempotency_key_unique_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-idem-dup")
    await make_workflow_run(org, user.id, idempotency_key="shared-key")

    duplicate = WorkflowRun(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        status=WorkflowStatus.PENDING,
        correlation_id=uuid.uuid4().hex,
        idempotency_key="shared-key",
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError, match="uq_workflow_runs_org_idempotency_key"):
        await db_session.flush()
    await db_session.rollback()


async def test_run_idempotency_key_allowed_across_organizations(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org_a, user_a = await _scenario(make_organization, make_user, make_membership, "wf-idem-a")
    org_b, user_b = await _scenario(make_organization, make_user, make_membership, "wf-idem-b")

    await make_workflow_run(org_a, user_a.id, idempotency_key="shared-across-orgs")
    run_b = await make_workflow_run(org_b, user_b.id, idempotency_key="shared-across-orgs")

    assert run_b.id is not None


async def test_run_request_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-raw-reqtype")
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO workflow_runs "
                "(id, organization_id, initiated_by_user_id, request_type, status, "
                "correlation_id, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, :user_id, 'bogus_type', 'pending', "
                ":corr, now(), now())"
            ),
            {"org_id": org.id, "user_id": user.id, "corr": uuid.uuid4().hex},
        )
    await db_session.rollback()


async def test_run_status_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-raw-status")
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO workflow_runs "
                "(id, organization_id, initiated_by_user_id, request_type, status, "
                "correlation_id, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org_id, :user_id, 'appointment_booking', "
                "'bogus_status', :corr, now(), now())"
            ),
            {"org_id": org.id, "user_id": user.id, "corr": uuid.uuid4().hex},
        )
    await db_session.rollback()


async def test_run_relationships(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-rel")
    run = await make_workflow_run(org, user.id)
    await db_session.refresh(run, attribute_names=["organization"])
    assert run.organization.id == org.id


# --- WorkflowStep ---


async def test_step_id_is_generated_uuid(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-step-uuid")
    run = await make_workflow_run(org, user.id)
    step = await make_workflow_step(org, run, 1)
    assert isinstance(step.id, uuid.UUID)
    assert step.status is StepStatus.PENDING
    assert step.attempt_count == 0


async def test_step_sequence_number_unique_within_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-step-seq")
    run = await make_workflow_run(org, user.id)
    await make_workflow_step(org, run, 1)

    duplicate = WorkflowStep(
        organization_id=org.id,
        workflow_run_id=run.id,
        sequence_number=1,
        step_type="synthetic_other",
        status=StepStatus.PENDING,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError, match="uq_workflow_steps_run_sequence"):
        await db_session.flush()
    await db_session.rollback()


async def test_step_rejects_cross_tenant_workflow_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org_a, user_a = await _scenario(make_organization, make_user, make_membership, "wf-step-x-a")
    org_b, user_b = await _scenario(make_organization, make_user, make_membership, "wf-step-x-b")
    run_a = await make_workflow_run(org_a, user_a.id)

    step = WorkflowStep(
        organization_id=org_b.id,
        workflow_run_id=run_a.id,
        sequence_number=1,
        step_type="synthetic",
        status=StepStatus.PENDING,
    )
    db_session.add(step)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_step_attempt_count_cannot_be_negative(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-step-neg")
    run = await make_workflow_run(org, user.id)

    step = WorkflowStep(
        organization_id=org.id,
        workflow_run_id=run.id,
        sequence_number=1,
        step_type="synthetic",
        status=StepStatus.PENDING,
        attempt_count=-1,
    )
    db_session.add(step)
    with pytest.raises(IntegrityError, match="attempt_count_non_negative"):
        await db_session.flush()
    await db_session.rollback()


# --- WorkflowEvent ---


async def test_event_id_is_generated_uuid_and_no_updated_at(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_event: MakeWorkflowEvent,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-evt-uuid")
    run = await make_workflow_run(org, user.id)
    event = await make_workflow_event(org, run)
    assert isinstance(event.id, uuid.UUID)
    assert event.created_at is not None
    assert not hasattr(event, "updated_at")


async def test_event_rejects_cross_tenant_workflow_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org_a, user_a = await _scenario(make_organization, make_user, make_membership, "wf-evt-x-a")
    org_b, _user_b = await _scenario(make_organization, make_user, make_membership, "wf-evt-x-b")
    run_a = await make_workflow_run(org_a, user_a.id)

    event = WorkflowEvent(
        organization_id=org_b.id,
        workflow_run_id=run_a.id,
        event_type=WorkflowEventType.WORKFLOW_CREATED,
        actor_type=ActorType.SYSTEM,
        actor_identifier="synthetic",
    )
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_event_rejects_step_from_a_different_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user = await _scenario(
        make_organization, make_user, make_membership, "wf-evt-run-mismatch"
    )
    run_a = await make_workflow_run(org, user.id)
    run_b = await make_workflow_run(org, user.id)
    step_on_a = await make_workflow_step(org, run_a, 1)

    event = WorkflowEvent(
        organization_id=org.id,
        workflow_run_id=run_b.id,
        workflow_step_id=step_on_a.id,
        event_type=WorkflowEventType.STEP_STARTED,
        actor_type=ActorType.AGENT,
        actor_identifier="synthetic_agent",
    )
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_event_safe_metadata_size_check_constraint(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-evt-metasize")
    run = await make_workflow_run(org, user.id)

    event = WorkflowEvent(
        organization_id=org.id,
        workflow_run_id=run.id,
        event_type=WorkflowEventType.WORKFLOW_CREATED,
        actor_type=ActorType.SYSTEM,
        actor_identifier="synthetic",
        safe_metadata={"blob": "x" * 3000},
    )
    db_session.add(event)
    with pytest.raises(IntegrityError, match="safe_metadata_size"):
        await db_session.flush()
    await db_session.rollback()


async def test_event_safe_metadata_within_bound_succeeds(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_event: MakeWorkflowEvent,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-evt-metaok")
    run = await make_workflow_run(org, user.id)
    event = await make_workflow_event(org, run, safe_metadata={"note": "small"})
    assert event.safe_metadata == {"note": "small"}


async def test_event_actor_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-evt-raw-actor")
    run = await make_workflow_run(org, user.id)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO workflow_events "
                "(id, organization_id, workflow_run_id, event_type, actor_type, "
                "actor_identifier, created_at) "
                "VALUES (gen_random_uuid(), :org_id, :run_id, 'workflow_created', "
                "'bogus_actor', 'synthetic', now())"
            ),
            {"org_id": org.id, "run_id": run.id},
        )
    await db_session.rollback()


async def test_event_type_check_constraint_rejects_invalid_value_via_raw_sql(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "wf-evt-raw-type")
    run = await make_workflow_run(org, user.id)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO workflow_events "
                "(id, organization_id, workflow_run_id, event_type, actor_type, "
                "actor_identifier, created_at) "
                "VALUES (gen_random_uuid(), :org_id, :run_id, 'bogus_event', "
                "'system', 'synthetic', now())"
            ),
            {"org_id": org.id, "run_id": run.id},
        )
    await db_session.rollback()
