"""Mandatory real-concurrency integration tests for the approval engine
(STORY-014). Mirrors `tests/db/test_workflow_concurrency.py`'s dedicated-
engine, genuinely-concurrent-connections pattern exactly (see that
file's module docstring for why a shared savepoint-isolated session
cannot prove this).

Two properties proven here, both against real PostgreSQL row locking
(`app.repositories.approval.get_by_id_for_update`):

1. Two workers racing to decide the SAME `PENDING` approval — exactly
   one decision wins; the loser gets a deterministic
   `InvalidApprovalTransitionError`, never a double-applied decision.
2. Two Coordinator-triggered approval requests racing for the SAME
   running step — exactly one pause wins; the loser gets a deterministic
   `WorkflowConflictError` from `mark_step_waiting`, never two approvals
   pausing the same step.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_approval_concurrency.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.approval import ApprovalRequest, ApprovalStatus, ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.user import User
from app.models.workflow import (
    ActorType,
    WorkflowEvent,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)
from app.services.approval import ApprovalService, InvalidApprovalTransitionError
from app.services.workflow import WorkflowConflictError, WorkflowService

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
            name=f"Synthetic Approval Concurrency Org {suffix}",
            slug=f"synthetic-approval-concurrency-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        user = User(
            email=f"synthetic.approval.concurrency.{suffix}@example.com",
            password_hash="not-a-real-hash",
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


async def test_concurrent_approve_of_same_pending_approval_only_one_succeeds() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        run_id, step_id = await _running_run_and_step(session_factory, scenario)
        async with session_factory() as session:
            approval = await ApprovalService(session).create_approval_request(
                organization_id=scenario.organization_id,
                workflow_run_id=run_id,
                workflow_step_id=step_id,
                approval_type=ApprovalType.HIGH_RISK_ACTION,
                reason="Needs a decision.",
                actor_identifier="coordinator",
                requested_by_agent="coordinator",
                actor_type=ActorType.AGENT,
            )
            approval_id = approval.id

        async def _approve() -> object:
            async with session_factory() as session:
                service = ApprovalService(session)
                try:
                    return await service.approve(
                        organization_id=scenario.organization_id,
                        approval_id=approval_id,
                        approved_by_user=scenario.user_id,
                        actor_identifier="synthetic-concurrency-worker",
                    )
                except InvalidApprovalTransitionError as exc:
                    return exc

        result_a, result_b = await asyncio.gather(_approve(), _approve())
        results = [result_a, result_b]
        successes = [r for r in results if not isinstance(r, InvalidApprovalTransitionError)]
        conflicts = [r for r in results if isinstance(r, InvalidApprovalTransitionError)]

        assert len(successes) == 1, f"expected exactly one approve to succeed, got: {results}"
        assert len(conflicts) == 1, (
            f"expected exactly one InvalidApprovalTransitionError, got: {results}"
        )

        async with session_factory() as session:
            approval = (
                await session.execute(
                    select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                )
            ).scalar_one()
            assert approval.status is ApprovalStatus.APPROVED

            run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run.status is WorkflowStatus.COMPLETED
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()


async def test_concurrent_approval_requests_for_same_step_only_one_succeeds() -> None:
    assert _POSTGRES_TEST_URL is not None
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    try:
        run_id, step_id = await _running_run_and_step(session_factory, scenario)

        async def _request_approval() -> object:
            async with session_factory() as session:
                service = ApprovalService(session)
                try:
                    return await service.create_approval_request(
                        organization_id=scenario.organization_id,
                        workflow_run_id=run_id,
                        workflow_step_id=step_id,
                        approval_type=ApprovalType.HIGH_RISK_ACTION,
                        reason="Needs a decision.",
                        actor_identifier="coordinator",
                        requested_by_agent="coordinator",
                        actor_type=ActorType.AGENT,
                    )
                except WorkflowConflictError as exc:
                    return exc

        result_a, result_b = await asyncio.gather(_request_approval(), _request_approval())
        results = [result_a, result_b]
        successes = [r for r in results if not isinstance(r, WorkflowConflictError)]
        conflicts = [r for r in results if isinstance(r, WorkflowConflictError)]

        assert len(successes) == 1, f"expected exactly one pause to succeed, got: {results}"
        assert len(conflicts) == 1, f"expected exactly one WorkflowConflictError, got: {results}"

        async with session_factory() as session:
            pending = (
                (
                    await session.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.workflow_step_id == step_id,
                            ApprovalRequest.status == ApprovalStatus.PENDING,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(pending) == 1

            run = (
                await session.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))
            ).scalar_one()
            assert run.status is WorkflowStatus.WAITING
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()
