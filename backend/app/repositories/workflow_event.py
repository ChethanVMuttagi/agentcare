"""Append-only persistence/query operations for `WorkflowEvent`.

Every read here REQUIRES an `organization_id` — same discipline as every
other repository in this codebase. This module deliberately exposes
ONLY `create` and `list_by_run` — no update, no delete. `WorkflowEvent`
is the workflow audit trail (see docs/WORKFLOWS.md "Event Immutability"
and "Audit Boundary"): once written, an event is never modified or
removed by application code. `create` never commits — see
`app.services.workflow.WorkflowService`, which commits a transition and
its corresponding event together, atomically.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import WorkflowEvent


async def create(session: AsyncSession, event: WorkflowEvent) -> WorkflowEvent:
    """Add and flush a new `WorkflowEvent`. Does NOT commit."""
    session.add(event)
    await session.flush()
    return event


async def list_by_run(
    session: AsyncSession, *, organization_id: uuid.UUID, workflow_run_id: uuid.UUID
) -> Sequence[WorkflowEvent]:
    """Return every event for one workflow run, oldest first (the
    natural order to read a history/audit trail in).

    Ordered by `sequence` — a server-assigned, strictly monotonically
    increasing identity column — NOT `created_at`. Two events created in
    rapid succession (e.g. `step_started` immediately followed by
    `tool_invoked`) can share the same `created_at` value at Python
    timestamp resolution; `sequence` cannot tie, and always matches
    insertion order.
    """
    result = await session.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.organization_id == organization_id,
            WorkflowEvent.workflow_run_id == workflow_run_id,
        )
        .order_by(WorkflowEvent.sequence)
    )
    return result.scalars().all()
