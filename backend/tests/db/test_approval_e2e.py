"""Mandatory real-PostgreSQL end-to-end proofs for the human-in-the-loop
approval engine (STORY-014) — the full chain, never trusting an
intermediate layer's return value alone: every assertion re-queries the
database directly.

    Coordinator requests approval -> Workflow paused (WAITING)
                                   -> Admin approves -> Workflow resumed
                                   and completed -> Database verified

    Coordinator requests approval -> Workflow paused
                                   -> Admin rejects -> Workflow resumed
                                   and cancelled -> Database verified

    Coordinator requests approval -> Workflow paused
                                   -> deadline passes, no decision
                                   -> lazily expires on the next
                                   approve/reject attempt -> Workflow
                                   failed -> Database verified

Uses its own dedicated engine/sessionmaker (see
`tests/db/test_workflow_concurrency.py`'s module docstring for why) —
genuinely committed, independently-queryable state across multiple real
sessions, mirroring `tests/db/test_reminder_e2e.py`'s established
pattern.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_approval_e2e.py
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.approval import ApprovalRequest, ApprovalStatus, ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.user import User
from app.models.workflow import (
    ActorType,
    StepStatus,
    WorkflowEvent,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from app.services.approval import ApprovalExpiredError, ApprovalService
from app.services.workflow import WorkflowService

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)


@dataclass(frozen=True)
class _Scenario:
    organization_id: uuid.UUID
    user_id: uuid.UUID


async def _setup(session_factory: async_sessionmaker[AsyncSession], suffix: str) -> _Scenario:
    async with session_factory() as session:
        org = Organization(
            name=f"Synthetic Approval E2E Org {suffix}",
            slug=f"synthetic-approval-e2e-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        user = User(
            email=f"synthetic.approval.e2e.{suffix}@example.com", password_hash="not-a-real-hash"
        )
        session.add(user)
        await session.flush()

        session.add(
            OrganizationMembership(organization_id=org.id, user_id=user.id, role=Role.ADMIN)
        )
        await session.flush()

        await session.commit()
        return _Scenario(organization_id=org.id, user_id=user.id)


async def _teardown(session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario) -> None:
    async with session_factory() as session:
        for model in (
            ApprovalRequest,
            WorkflowEvent,
            WorkflowStep,
            WorkflowRun,
            OrganizationMembership,
        ):
            await session.execute(
                delete(model).where(model.organization_id == scenario.organization_id)
            )
        await session.execute(delete(User).where(User.id == scenario.user_id))
        await session.execute(
            delete(Organization).where(Organization.id == scenario.organization_id)
        )
        await session.commit()


async def _running_run_and_step(
    session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario
) -> tuple[uuid.UUID, uuid.UUID]:
    async with session_factory() as session:
        workflow_service = WorkflowService(session)
        run = await workflow_service.create_workflow(
            organization_id=scenario.organization_id,
            initiated_by_user_id=scenario.user_id,
            request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
            patient_id=None,
            actor_type=ActorType.USER,
            actor_identifier=str(scenario.user_id),
        )
        await workflow_service.start_workflow(
            organization_id=scenario.organization_id,
            workflow_run_id=run.id,
            actor_type=ActorType.USER,
            actor_identifier=str(scenario.user_id),
        )
        step = await workflow_service.create_step(
            organization_id=scenario.organization_id,
            workflow_run_id=run.id,
            sequence_number=1,
            step_type="coordination",
        )
        step = await workflow_service.start_step(
            organization_id=scenario.organization_id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=ActorType.AGENT,
            actor_identifier="coordinator",
        )
        return run.id, step.id


async def test_coordinator_pause_then_admin_approve_resumes_and_completes() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        run_id, step_id = await _running_run_and_step(session_factory, scenario)

        # 1. Coordinator requests approval -> workflow genuinely paused.
        async with session_factory() as session:
            approval_service = ApprovalService(session)
            approval = await approval_service.create_approval_request(
                organization_id=scenario.organization_id,
                workflow_run_id=run_id,
                workflow_step_id=step_id,
                approval_type=ApprovalType.HIGH_RISK_ACTION,
                reason="Needs sign-off.",
                actor_identifier="coordinator",
                requested_by_agent="coordinator",
                actor_type=ActorType.AGENT,
            )
            approval_id = approval.id

        async with session_factory() as session:
            run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run.status is WorkflowStatus.WAITING
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.id == step_id))
            ).scalar_one()
            assert step.status is StepStatus.WAITING

        # 2. Admin approves -> resumed and completed.
        async with session_factory() as session:
            approval_service = ApprovalService(session)
            await approval_service.approve(
                organization_id=scenario.organization_id,
                approval_id=approval_id,
                approved_by_user=scenario.user_id,
                actor_identifier=str(scenario.user_id),
            )

        # 3. Re-queried directly, not trusting the service call's return value.
        async with session_factory() as session:
            run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run.status is WorkflowStatus.COMPLETED
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.id == step_id))
            ).scalar_one()
            assert step.status is StepStatus.COMPLETED

            approval = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                )
            ).scalar_one()
            assert approval.status is ApprovalStatus.APPROVED
            assert approval.approved_by_user == scenario.user_id

            events = (
                (
                    await session.execute(
                        select(WorkflowEvent)
                        .where(WorkflowEvent.workflow_run_id == run_id)
                        .order_by(WorkflowEvent.sequence)
                    )
                )
                .scalars()
                .all()
            )
            assert [e.event_type.value for e in events] == [
                "workflow_created",
                "workflow_started",
                "step_started",
                "step_waiting",
                "workflow_waiting",
                "approval_requested",
                "approval_granted",
                "step_resumed",
                "workflow_resumed",
                "step_completed",
                "workflow_completed",
            ]
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_coordinator_pause_then_admin_reject_resumes_and_cancels() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        run_id, step_id = await _running_run_and_step(session_factory, scenario)

        async with session_factory() as session:
            approval_service = ApprovalService(session)
            approval = await approval_service.create_approval_request(
                organization_id=scenario.organization_id,
                workflow_run_id=run_id,
                workflow_step_id=step_id,
                approval_type=ApprovalType.APPOINTMENT_OVERRIDE,
                reason="Needs a decision.",
                actor_identifier="coordinator",
                requested_by_agent="coordinator",
                actor_type=ActorType.AGENT,
            )
            approval_id = approval.id

        async with session_factory() as session:
            approval_service = ApprovalService(session)
            await approval_service.reject(
                organization_id=scenario.organization_id,
                approval_id=approval_id,
                rejected_by_user=scenario.user_id,
                actor_identifier=str(scenario.user_id),
            )

        async with session_factory() as session:
            run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run.status is WorkflowStatus.CANCELLED
            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.id == step_id))
            ).scalar_one()
            assert step.status is StepStatus.FAILED
            assert step.failure_code == "approval_rejected"

            approval = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                )
            ).scalar_one()
            assert approval.status is ApprovalStatus.REJECTED
            assert approval.approved_by_user == scenario.user_id
            assert approval.rejected_at is not None
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_expired_approval_fails_workflow_on_lazy_check() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        run_id, step_id = await _running_run_and_step(session_factory, scenario)

        async with session_factory() as session:
            approval_service = ApprovalService(session)
            approval = await approval_service.create_approval_request(
                organization_id=scenario.organization_id,
                workflow_run_id=run_id,
                workflow_step_id=step_id,
                approval_type=ApprovalType.DOCUMENT_EXCEPTION,
                reason="Needs a decision before the deadline.",
                actor_identifier="coordinator",
                requested_by_agent="coordinator",
                actor_type=ActorType.AGENT,
                expiry=timedelta(seconds=-1),
            )
            approval_id = approval.id

        # No one ever decides — the NEXT attempt to approve lazily
        # expires it instead of silently applying a late decision.
        async with session_factory() as session:
            approval_service = ApprovalService(session)
            with pytest.raises(ApprovalExpiredError):
                await approval_service.approve(
                    organization_id=scenario.organization_id,
                    approval_id=approval_id,
                    approved_by_user=scenario.user_id,
                    actor_identifier=str(scenario.user_id),
                )

        async with session_factory() as session:
            approval = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                )
            ).scalar_one()
            assert approval.status is ApprovalStatus.EXPIRED
            assert approval.approved_by_user is None

            run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run.status is WorkflowStatus.FAILED
            assert run.failure_code == "approval_expired"

            step = (
                await session.execute(select(WorkflowStep).where(WorkflowStep.id == step_id))
            ).scalar_one()
            assert step.status is StepStatus.FAILED
            assert step.failure_code == "approval_expired"
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()
