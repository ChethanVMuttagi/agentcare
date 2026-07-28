"""Append-only persistence/query operations for `WorkflowEvent`.

Every read here REQUIRES an `organization_id` — same discipline as every
other repository in this codebase. This module deliberately exposes
ONLY `create` and read operations — no update, no delete. `WorkflowEvent`
is the workflow audit trail (see docs/WORKFLOWS.md "Event Immutability"
and "Audit Boundary"): once written, an event is never modified or
removed by application code. `create` never commits — see
`app.services.workflow.WorkflowService`, which commits a transition and
its corresponding event together, atomically.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import WorkflowEvent, WorkflowEventType


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


async def list_since(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    after_sequence: int,
) -> Sequence[WorkflowEvent]:
    """Return every event for one workflow run with `sequence >
    after_sequence`, oldest first — the polling primitive Milestone B's
    Server-Sent Events stream (`app.api.v1.endpoints.workflows.
    stream_workflow_events`) is built on. `after_sequence=0` returns the
    full history (valid `sequence` values start at 1), matching a
    client's first connection or a `Last-Event-ID` of `0`.
    """
    result = await session.execute(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.organization_id == organization_id,
            WorkflowEvent.workflow_run_id == workflow_run_id,
            WorkflowEvent.sequence > after_sequence,
        )
        .order_by(WorkflowEvent.sequence)
    )
    return result.scalars().all()


async def count_by_event_type(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> dict[WorkflowEventType, int]:
    """Organization-wide event counts grouped by `event_type` — the
    "agent activity" aggregate for the Milestone B analytics summary
    (`app.api.v1.endpoints.analytics`). Types with zero events are
    simply absent from the returned mapping."""
    result = await session.execute(
        select(WorkflowEvent.event_type, func.count())
        .where(WorkflowEvent.organization_id == organization_id)
        .group_by(WorkflowEvent.event_type)
    )
    return {event_type: count for event_type, count in result.all()}


async def count_agent_handoffs_by_target(
    session: AsyncSession, *, organization_id: uuid.UUID
) -> dict[str, int]:
    """Organization-wide `AGENT_HANDOFF` counts grouped by the
    specialist agent handed off TO (`safe_metadata["to_agent"]` — always
    present on this event type, see
    `app.services.workflow.WorkflowService.record_agent_handoff`) — the
    "handoffs by agent" breakdown for the Milestone B analytics summary.
    """
    to_agent = WorkflowEvent.safe_metadata["to_agent"].astext
    result = await session.execute(
        select(to_agent, func.count())
        .where(
            WorkflowEvent.organization_id == organization_id,
            WorkflowEvent.event_type == WorkflowEventType.AGENT_HANDOFF,
        )
        .group_by(to_agent)
    )
    return {agent_name: count for agent_name, count in result.all() if agent_name is not None}
