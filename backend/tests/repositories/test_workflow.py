"""app.repositories.workflow_run / workflow_step / workflow_event tests
against real PostgreSQL."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.models.workflow import (
    ActorType,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from app.repositories import workflow_event as workflow_event_repository
from app.repositories import workflow_run as workflow_run_repository
from app.repositories import workflow_step as workflow_step_repository

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


# --- workflow_run repository ---


async def test_get_by_id_returns_run_within_organization(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-run-get")
    run = await make_workflow_run(org, user.id)

    result = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert result is not None
    assert result.id == run.id


async def test_get_by_id_returns_none_for_cross_tenant_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org_a = await make_organization("repo-run-cross-a")
    org_b, user_b = await _scenario(
        make_organization, make_user, make_membership, "repo-run-cross-b"
    )
    run_b = await make_workflow_run(org_b, user_b.id)

    result = await workflow_run_repository.get_by_id(
        db_session, organization_id=org_a.id, workflow_run_id=run_b.id
    )
    assert result is None


async def test_get_by_id_returns_none_for_unknown_id(
    db_session: AsyncSession, make_organization: MakeOrganization
) -> None:
    org = await make_organization("repo-run-unknown")
    result = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=uuid.uuid4()
    )
    assert result is None


async def test_get_by_id_for_update_returns_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-run-lock")
    run = await make_workflow_run(org, user.id)

    result = await workflow_run_repository.get_by_id_for_update(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert result is not None
    assert result.id == run.id


async def test_list_by_organization_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org_a, user_a = await _scenario(
        make_organization, make_user, make_membership, "repo-run-list-a"
    )
    org_b, user_b = await _scenario(
        make_organization, make_user, make_membership, "repo-run-list-b"
    )
    run_a = await make_workflow_run(org_a, user_a.id)
    await make_workflow_run(org_b, user_b.id)

    results = await workflow_run_repository.list_by_organization(
        db_session, organization_id=org_a.id
    )
    assert [r.id for r in results] == [run_a.id]


async def test_list_by_organization_orders_newest_first(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-run-order")
    # `created_at` is Python-generated (see `app.db.mixins.TimestampMixin`)
    # and two rows created back-to-back can otherwise share the same
    # value at timestamp resolution, making "newest first" ambiguous —
    # force a real gap so this test is deterministic rather than
    # occasionally flaky. `WorkflowEvent` has a database-assigned
    # `sequence` column immune to this (see
    # tests/repositories/test_workflow.py::test_event_list_by_run_orders_oldest_first);
    # `WorkflowRun` listing does not need that guarantee for its "best
    # effort newest first" semantics, so only the test is adjusted here.
    first = await make_workflow_run(org, user.id)
    first.created_at = datetime.now(UTC) - timedelta(seconds=5)
    await db_session.flush()
    second = await make_workflow_run(org, user.id)

    results = await workflow_run_repository.list_by_organization(
        db_session, organization_id=org.id
    )
    assert [r.id for r in results] == [second.id, first.id]


async def test_list_by_organization_filters_by_patient(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-run-patient")
    patient_a = await make_patient(org, "PN-repo-run-patient-a")
    patient_b = await make_patient(org, "PN-repo-run-patient-b")
    run_a = await make_workflow_run(org, user.id, patient=patient_a)
    await make_workflow_run(org, user.id, patient=patient_b)

    results = await workflow_run_repository.list_by_organization(
        db_session, organization_id=org.id, patient_id=patient_a.id
    )
    assert [r.id for r in results] == [run_a.id]


async def test_create_adds_and_flushes_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-run-create")

    run = WorkflowRun(
        organization_id=org.id,
        initiated_by_user_id=user.id,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        status=WorkflowStatus.PENDING,
        correlation_id=uuid.uuid4().hex,
    )
    created = await workflow_run_repository.create(db_session, run)
    assert created.id is not None

    await db_session.rollback()

    result = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=created.id
    )
    assert result is None


async def test_set_current_step_updates_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-run-curstep")
    run = await make_workflow_run(org, user.id)
    assert run.current_step is None

    await workflow_run_repository.set_current_step(
        db_session, organization_id=org.id, workflow_run_id=run.id, current_step=3
    )
    await db_session.refresh(run)
    assert run.current_step == 3


# --- workflow_step repository ---


async def test_step_get_by_id_returns_step_within_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-step-get")
    run = await make_workflow_run(org, user.id)
    step = await make_workflow_step(org, run, 1)

    result = await workflow_step_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=run.id, step_id=step.id
    )
    assert result is not None
    assert result.id == step.id


async def test_step_get_by_id_returns_none_for_wrong_run(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-step-wrongrun")
    run_a = await make_workflow_run(org, user.id)
    run_b = await make_workflow_run(org, user.id)
    step = await make_workflow_step(org, run_a, 1)

    result = await workflow_step_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=run_b.id, step_id=step.id
    )
    assert result is None


async def test_step_get_by_id_for_update_returns_step(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-step-lock")
    run = await make_workflow_run(org, user.id)
    step = await make_workflow_step(org, run, 1)

    result = await workflow_step_repository.get_by_id_for_update(
        db_session, organization_id=org.id, workflow_run_id=run.id, step_id=step.id
    )
    assert result is not None
    assert result.id == step.id


async def test_step_list_by_run_orders_by_sequence_number(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_step: MakeWorkflowStep,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-step-order")
    run = await make_workflow_run(org, user.id)
    third = await make_workflow_step(org, run, 3)
    first = await make_workflow_step(org, run, 1)
    second = await make_workflow_step(org, run, 2)

    results = await workflow_step_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert [s.id for s in results] == [first.id, second.id, third.id]


async def test_step_create_adds_and_flushes_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-step-create")
    run = await make_workflow_run(org, user.id)

    step = WorkflowStep(
        organization_id=org.id,
        workflow_run_id=run.id,
        sequence_number=1,
        step_type="synthetic",
    )
    created = await workflow_step_repository.create(db_session, step)
    assert created.id is not None

    await db_session.rollback()

    results = await workflow_step_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert results == []


# --- workflow_event repository (append-only) ---


async def test_event_list_by_run_orders_oldest_first(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_event: MakeWorkflowEvent,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-evt-order")
    run = await make_workflow_run(org, user.id)
    first = await make_workflow_event(org, run, event_type=WorkflowEventType.WORKFLOW_CREATED)
    second = await make_workflow_event(org, run, event_type=WorkflowEventType.WORKFLOW_STARTED)

    results = await workflow_event_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert [e.id for e in results] == [first.id, second.id]


async def test_event_list_by_run_is_tenant_scoped(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
    make_workflow_event: MakeWorkflowEvent,
) -> None:
    org_a, user_a = await _scenario(
        make_organization, make_user, make_membership, "repo-evt-tenant-a"
    )
    org_b, user_b = await _scenario(
        make_organization, make_user, make_membership, "repo-evt-tenant-b"
    )
    run_a = await make_workflow_run(org_a, user_a.id)
    run_b = await make_workflow_run(org_b, user_b.id)
    await make_workflow_event(org_a, run_a)
    await make_workflow_event(org_b, run_b)

    results = await workflow_event_repository.list_by_run(
        db_session, organization_id=org_a.id, workflow_run_id=run_a.id
    )
    assert len(results) == 1


async def test_event_create_adds_and_flushes_without_committing(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_workflow_run: MakeWorkflowRun,
) -> None:
    org, user = await _scenario(make_organization, make_user, make_membership, "repo-evt-create")
    run = await make_workflow_run(org, user.id)

    event = WorkflowEvent(
        organization_id=org.id,
        workflow_run_id=run.id,
        event_type=WorkflowEventType.WORKFLOW_CREATED,
        actor_type=ActorType.SYSTEM,
        actor_identifier="synthetic",
    )
    created = await workflow_event_repository.create(db_session, event)
    assert created.id is not None

    await db_session.rollback()

    results = await workflow_event_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=run.id
    )
    assert results == []


def test_event_repository_exposes_no_update_or_delete_function() -> None:
    """Structural proof of append-only immutability (see
    docs/WORKFLOWS.md "Event Immutability"): the repository module has no
    `update`/`delete` function an application caller could reach for."""
    assert not hasattr(workflow_event_repository, "update")
    assert not hasattr(workflow_event_repository, "delete")
