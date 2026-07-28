"""Tenant-scoped persistence/query operations for `WorkflowStep`.

Every read here REQUIRES an `organization_id` (and its owning
`workflow_run_id`) — same discipline as every other repository in this
codebase (see `app.repositories.workflow_run`). This module only adds,
flushes, locks, and queries. It never commits, and it performs no
RBAC/authorization decisions and no lifecycle/state-transition
validation — see `app.services.workflow.WorkflowService`. It does not
implement a scheduler/queue query (e.g. "next pending step") — see
docs/WORKFLOWS.md for why that is deliberately out of scope for this
story.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import WorkflowStep


async def get_by_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    step_id: uuid.UUID,
) -> WorkflowStep | None:
    """Return the step with `step_id` IF it belongs to `workflow_run_id`
    within `organization_id`."""
    result = await session.execute(
        select(WorkflowStep).where(
            WorkflowStep.organization_id == organization_id,
            WorkflowStep.workflow_run_id == workflow_run_id,
            WorkflowStep.id == step_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_id_for_update(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    step_id: uuid.UUID,
) -> WorkflowStep | None:
    """Same as `get_by_id`, but locks the row (`SELECT ... FOR UPDATE`) —
    see `app.repositories.workflow_run.get_by_id_for_update` for the
    concurrency rationale, applied here at step granularity.

    `populate_existing=True` is required, not cosmetic: if this session
    already loaded this exact `WorkflowStep` earlier (e.g. an unlocked
    precondition read before this locked one), SQLAlchemy's identity map
    returns that SAME Python object by default and does NOT overwrite
    its already-populated attributes from this query's result — so
    `.status` would silently keep reflecting the value seen before this
    call blocked on the lock, even though the ROW itself was correctly
    locked and the query genuinely waited. `populate_existing=True`
    forces this query's result to overwrite the object's attributes,
    which is the entire point of a "for update" read: the caller must
    see the CURRENT, contention-safe state, never a stale cached one."""
    result = await session.execute(
        select(WorkflowStep)
        .where(
            WorkflowStep.organization_id == organization_id,
            WorkflowStep.workflow_run_id == workflow_run_id,
            WorkflowStep.id == step_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_by_run(
    session: AsyncSession, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
) -> Sequence[WorkflowStep]:
    """Return every step of one workflow run, in execution order."""
    result = await session.execute(
        select(WorkflowStep)
        .where(
            WorkflowStep.organization_id == organization_id,
            WorkflowStep.workflow_run_id == workflow_run_id,
        )
        .order_by(WorkflowStep.sequence_number)
    )
    return result.scalars().all()


async def create(session: AsyncSession, step: WorkflowStep) -> WorkflowStep:
    """Add and flush a new `WorkflowStep`. Does NOT commit — see
    `app.services.workflow.WorkflowService.create_step`."""
    session.add(step)
    await session.flush()
    return step
