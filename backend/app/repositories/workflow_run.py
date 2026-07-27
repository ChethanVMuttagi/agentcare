"""Tenant-scoped persistence/query operations for `WorkflowRun`.

Every read here REQUIRES an `organization_id` — same discipline as every
other repository in this codebase (see `app.repositories.appointment`).
This module only adds, flushes, locks, and queries. It never commits,
and it performs no RBAC/authorization decisions and no lifecycle/state-
transition validation — see `app.services.workflow.WorkflowService`.

`get_by_id_for_update` is the concurrency primitive `WorkflowService`
builds its race-safe transitions on (`SELECT ... FOR UPDATE`) — see
docs/WORKFLOWS.md "Concurrency Strategy".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import WorkflowRun


async def get_by_id(
    session: AsyncSession, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
) -> WorkflowRun | None:
    """Return the workflow run with `workflow_run_id` IF it belongs to `organization_id`."""
    result = await session.execute(
        select(WorkflowRun).where(
            WorkflowRun.organization_id == organization_id,
            WorkflowRun.id == workflow_run_id,
        )
    )
    return result.scalar_one_or_none()


async def get_by_id_for_update(
    session: AsyncSession, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
) -> WorkflowRun | None:
    """Same as `get_by_id`, but locks the row (`SELECT ... FOR UPDATE`).

    A second concurrent caller locking the SAME row blocks until the
    first transaction commits or rolls back, then observes the
    now-current (possibly already-transitioned) status — this is what
    lets `WorkflowService` detect and reject a lost transition race
    deterministically rather than silently double-applying it.
    """
    result = await session.execute(
        select(WorkflowRun)
        .where(
            WorkflowRun.organization_id == organization_id,
            WorkflowRun.id == workflow_run_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def list_by_organization(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    patient_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[WorkflowRun]:
    """Return workflow runs belonging to `organization_id`, newest first.

    `patient_id`, when given, additionally restricts results to that
    patient's own runs — used for `PATIENT`-role self-service listing.
    """
    stmt = select(WorkflowRun).where(WorkflowRun.organization_id == organization_id)
    if patient_id is not None:
        stmt = stmt.where(WorkflowRun.patient_id == patient_id)
    result = await session.execute(
        stmt.order_by(WorkflowRun.created_at.desc(), WorkflowRun.id).limit(limit).offset(offset)
    )
    return result.scalars().all()


async def create(session: AsyncSession, run: WorkflowRun) -> WorkflowRun:
    """Add and flush a new `WorkflowRun`. Does NOT commit — see
    `app.services.workflow.WorkflowService.create_workflow`."""
    session.add(run)
    await session.flush()
    return run


async def set_current_step(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    current_step: int,
) -> None:
    """Update `current_step` via a single atomic `UPDATE` statement.

    Deliberately NOT a Python-level read-modify-write: `current_step` is
    an informational pointer (not a field the state machine gates
    correctness on), so a single-statement `UPDATE` is sufficient to
    avoid a lost-update race without needing `SELECT ... FOR UPDATE`
    here too. Does NOT commit — the caller decides the transaction
    boundary.
    """
    await session.execute(
        update(WorkflowRun)
        .where(
            WorkflowRun.organization_id == organization_id,
            WorkflowRun.id == workflow_run_id,
        )
        .values(current_step=current_step)
    )
