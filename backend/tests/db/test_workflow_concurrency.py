"""Mandatory real-concurrency integration test for workflow transitions.

STORY-009's central concurrency requirement: two workers must not both
incorrectly advance the same workflow's state. This file is the proof —
two independent, genuinely concurrent connections race to `start` the
SAME `PENDING` `WorkflowRun`. Exactly one must succeed (`PENDING` ->
`RUNNING`); the other must receive a deterministic `WorkflowConflictError`
(409), proven against a real PostgreSQL instance with `SELECT ... FOR
UPDATE` row locking (`app.repositories.workflow_run.get_by_id_for_update`)
as the only thing arbitrating the race.

Unlike every other test in this codebase (which uses the single shared,
savepoint-isolated `db_session` fixture from `tests/conftest.py`), this
test deliberately opens TWO INDEPENDENT database connections/transactions
via its own dedicated engine — a shared savepoint fixture provides only
ONE underlying connection, which would defeat the entire point of racing
two independent transactions against each other. Setup data is genuinely
COMMITTED, not flushed-then-rolled-back; synthetic rows are explicitly
deleted in a `finally` block (see `_teardown`), mirroring
`tests/db/test_appointment_concurrency.py`.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_workflow_concurrency.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
from app.models.user import User
from app.models.workflow import (
    ActorType,
    WorkflowEvent,
    WorkflowRequestType,
    WorkflowRun,
    WorkflowStatus,
)
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
    workflow_run_id: uuid.UUID


async def _setup(session_factory: async_sessionmaker[AsyncSession], suffix: str) -> _Scenario:
    """Create and COMMIT a genuinely persisted scenario: one org, one
    active admin member, and one `PENDING` workflow run initiated by
    that member."""
    async with session_factory() as session:
        org = Organization(
            name=f"Synthetic Workflow Concurrency Org {suffix}",
            slug=f"synthetic-workflow-concurrency-org-{suffix}",
            organization_type=OrganizationType.HOSPITAL,
        )
        session.add(org)
        await session.flush()

        user = User(
            email=f"synthetic.workflow.concurrency.{suffix}@example.com",
            password_hash="not-a-real-hash",
        )
        session.add(user)
        await session.flush()

        membership = OrganizationMembership(
            organization_id=org.id, user_id=user.id, role=Role.ADMIN
        )
        session.add(membership)
        await session.flush()

        run = WorkflowRun(
            organization_id=org.id,
            initiated_by_user_id=user.id,
            request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
            status=WorkflowStatus.PENDING,
            correlation_id=uuid.uuid4().hex,
        )
        session.add(run)
        await session.flush()

        await session.commit()

        return _Scenario(organization_id=org.id, user_id=user.id, workflow_run_id=run.id)


async def _teardown(session_factory: async_sessionmaker[AsyncSession], scenario: _Scenario) -> None:
    """Explicitly delete every synthetic row this test created, in
    FK-safe (child-before-parent) order — required because this test's
    data is genuinely committed, not rolled back."""
    async with session_factory() as session:
        await session.execute(
            delete(WorkflowEvent).where(WorkflowEvent.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(WorkflowRun).where(WorkflowRun.organization_id == scenario.organization_id)
        )
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == scenario.organization_id
            )
        )
        await session.execute(delete(User).where(User.id == scenario.user_id))
        await session.execute(
            delete(Organization).where(Organization.id == scenario.organization_id)
        )
        await session.commit()


async def test_concurrent_start_of_same_pending_workflow_only_one_succeeds() -> None:
    """Two independent connections race to `start` the SAME `PENDING`
    workflow run. Exactly one must succeed (ending `RUNNING`); the other
    must fail with `WorkflowConflictError` — proven via real,
    concurrently-executing transactions, not sequential calls."""
    assert _POSTGRES_TEST_URL is not None  # narrows type; guarded by skipif above
    engine = create_async_engine(_POSTGRES_TEST_URL, pool_size=5, max_overflow=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scenario = await _setup(session_factory, uuid.uuid4().hex[:8])

    async def _start() -> object:
        async with session_factory() as session:
            service = WorkflowService(session)
            try:
                return await service.start_workflow(
                    organization_id=scenario.organization_id,
                    workflow_run_id=scenario.workflow_run_id,
                    actor_type=ActorType.SYSTEM,
                    actor_identifier="synthetic-concurrency-worker",
                )
            except WorkflowConflictError as exc:
                return exc

    try:
        result_a, result_b = await asyncio.gather(_start(), _start())

        results = [result_a, result_b]
        successes = [r for r in results if isinstance(r, WorkflowConflictError) is False]
        conflicts = [r for r in results if isinstance(r, WorkflowConflictError)]

        assert len(successes) == 1, (
            f"expected exactly one start to succeed under real concurrency, got: {results}"
        )
        assert len(conflicts) == 1, (
            f"expected exactly one WorkflowConflictError under real concurrency, got: {results}"
        )

        # Confirm the database itself only ever holds ONE `RUNNING` row —
        # not an application-level count — and exactly one
        # `workflow_started` event was recorded (transition+event
        # atomicity: the loser's rejected attempt left no partial trace).
        async with session_factory() as session:
            run = (
                await session.execute(
                    select(WorkflowRun).where(WorkflowRun.id == scenario.workflow_run_id)
                )
            ).scalar_one()
            assert run.status is WorkflowStatus.RUNNING

            events = (
                (
                    await session.execute(
                        select(WorkflowEvent).where(
                            WorkflowEvent.workflow_run_id == scenario.workflow_run_id,
                            WorkflowEvent.event_type == "workflow_started",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 1
    finally:
        await _teardown(session_factory, scenario)
        await engine.dispose()
