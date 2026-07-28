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

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import WorkflowRequestType, WorkflowRun, WorkflowStatus


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

    `populate_existing=True` is required, not cosmetic: if this session
    already loaded this exact `WorkflowRun` earlier (e.g. an unlocked
    precondition read before this locked one), SQLAlchemy's identity map
    returns that SAME Python object by default and does NOT overwrite
    its already-populated attributes from this query's result — so
    `.status` would silently keep reflecting the value seen before this
    call blocked on the lock, even though the ROW itself was correctly
    locked and the query genuinely waited. `populate_existing=True`
    forces this query's result to overwrite the object's attributes,
    which is the entire point of a "for update" read: the caller must
    see the CURRENT, contention-safe state, never a stale cached one.
    """
    result = await session.execute(
        select(WorkflowRun)
        .where(
            WorkflowRun.organization_id == organization_id,
            WorkflowRun.id == workflow_run_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
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


async def count_by_status(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> dict[WorkflowStatus, int]:
    """Organization-wide run counts grouped by `status` — the workflows
    breakdown for the Milestone B analytics summary
    (`app.api.v1.endpoints.analytics`). Statuses with zero runs are
    simply absent from the returned mapping."""
    result = await session.execute(
        select(WorkflowRun.status, func.count())
        .where(WorkflowRun.organization_id == organization_id)
        .group_by(WorkflowRun.status)
    )
    return {status: count for status, count in result.all()}


async def count_by_request_type(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> dict[WorkflowRequestType, int]:
    """Organization-wide run counts grouped by `request_type` — the
    analytics summary's request-type breakdown."""
    result = await session.execute(
        select(WorkflowRun.request_type, func.count())
        .where(WorkflowRun.organization_id == organization_id)
        .group_by(WorkflowRun.request_type)
    )
    return {request_type: count for request_type, count in result.all()}


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
