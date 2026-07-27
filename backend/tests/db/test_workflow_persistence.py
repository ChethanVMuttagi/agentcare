"""Mandatory persistence/restart proof for workflow state.

STORY-009's central durability requirement: workflow state must not
depend on in-memory dictionaries, Python process memory, or any
particular database connection/engine's lifetime — a process restart
must not destroy workflow history. This file is the proof: a workflow
run (with a step and its audit events) is created and fully committed
using ONE engine/session, that engine is genuinely DISPOSED (its
connection pool torn down — nothing is cached in Python memory that
could paper over a real reconnect), and then a BRAND NEW, independent
engine/session is created from scratch to retrieve the same workflow,
step, and events — proving they were durably persisted to PostgreSQL
itself, not merely held alive by the original connection/session object.

To run:
    export AGENTCARE_TEST_POSTGRES_URL=postgresql+asyncpg://user:pw@localhost:5432/db
    pytest tests/db/test_workflow_persistence.py
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization, OrganizationType
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
from app.services.workflow import WorkflowService

_POSTGRES_TEST_URL = os.environ.get("AGENTCARE_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not _POSTGRES_TEST_URL,
    reason="Set AGENTCARE_TEST_POSTGRES_URL to a real PostgreSQL instance to run this test.",
)


async def test_workflow_state_survives_engine_disposal_and_recreation() -> None:
    """Create a workflow (run + step + events) via one engine, fully
    dispose it, then prove a brand new engine sees exactly the same
    durable state."""
    assert _POSTGRES_TEST_URL is not None  # narrows type; guarded by skipif above
    suffix = uuid.uuid4().hex[:8]

    engine_a = create_async_engine(_POSTGRES_TEST_URL)
    session_factory_a = async_sessionmaker(engine_a, expire_on_commit=False)

    organization_id: uuid.UUID
    user_id: uuid.UUID
    workflow_run_id: uuid.UUID
    step_id: uuid.UUID
    correlation_id: str

    try:
        async with session_factory_a() as session:
            org = Organization(
                name=f"Synthetic Workflow Persistence Org {suffix}",
                slug=f"synthetic-workflow-persistence-org-{suffix}",
                organization_type=OrganizationType.HOSPITAL,
            )
            session.add(org)
            await session.flush()

            user = User(
                email=f"synthetic.workflow.persistence.{suffix}@example.com",
                password_hash="not-a-real-hash",
            )
            session.add(user)
            await session.flush()

            membership = OrganizationMembership(
                organization_id=org.id, user_id=user.id, role=Role.ADMIN
            )
            session.add(membership)
            await session.flush()
            await session.commit()

            organization_id = org.id
            user_id = user.id

            service = WorkflowService(session)
            run = await service.create_workflow(
                organization_id=organization_id,
                initiated_by_user_id=user_id,
                request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
                patient_id=None,
                actor_type=ActorType.USER,
                actor_identifier=str(user_id),
            )
            workflow_run_id = run.id
            correlation_id = run.correlation_id

            run = await service.start_workflow(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                actor_type=ActorType.SYSTEM,
                actor_identifier="synthetic-persistence-worker",
            )

            step = await service.create_step(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                sequence_number=1,
                step_type="synthetic_collect_document",
            )
            step_id = step.id

            step = await service.start_step(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                actor_type=ActorType.AGENT,
                actor_identifier="synthetic_document_agent",
            )
            step = await service.complete_step(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                step_id=step_id,
                actor_type=ActorType.AGENT,
                actor_identifier="synthetic_document_agent",
            )

            run = await service.complete_workflow(
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                actor_type=ActorType.SYSTEM,
                actor_identifier="synthetic-persistence-worker",
            )
            assert run.status is WorkflowStatus.COMPLETED
    finally:
        await engine_a.dispose()

    # `engine_a` is now fully torn down: its connection pool is closed and
    # nothing about it remains referenced. A completely independent
    # engine/session, built from nothing but the connection URL, must see
    # identical durable state.
    engine_b = create_async_engine(_POSTGRES_TEST_URL)
    session_factory_b = async_sessionmaker(engine_b, expire_on_commit=False)
    try:
        async with session_factory_b() as session:
            fetched_run = await workflow_run_repository.get_by_id(
                session, organization_id=organization_id, workflow_run_id=workflow_run_id
            )
            assert fetched_run is not None
            assert fetched_run.status is WorkflowStatus.COMPLETED
            assert fetched_run.correlation_id == correlation_id
            assert fetched_run.started_at is not None
            assert fetched_run.completed_at is not None

            steps = await workflow_step_repository.list_by_run(
                session, organization_id=organization_id, workflow_run_id=workflow_run_id
            )
            assert len(steps) == 1
            assert steps[0].id == step_id
            assert steps[0].status.value == "completed"
            assert steps[0].attempt_count == 1

            events = await workflow_event_repository.list_by_run(
                session, organization_id=organization_id, workflow_run_id=workflow_run_id
            )
            event_types = [e.event_type for e in events]
            assert event_types == [
                WorkflowEventType.WORKFLOW_CREATED,
                WorkflowEventType.WORKFLOW_STARTED,
                WorkflowEventType.STEP_STARTED,
                WorkflowEventType.STEP_COMPLETED,
                WorkflowEventType.WORKFLOW_COMPLETED,
            ]
    finally:
        async with session_factory_b() as session:
            await session.execute(
                delete(WorkflowEvent).where(WorkflowEvent.organization_id == organization_id)
            )
            await session.execute(
                delete(WorkflowStep).where(WorkflowStep.organization_id == organization_id)
            )
            await session.execute(
                delete(WorkflowRun).where(WorkflowRun.organization_id == organization_id)
            )
            await session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.organization_id == organization_id
                )
            )
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(Organization).where(Organization.id == organization_id))
            await session.commit()
        await engine_b.dispose()
