"""`AgentOrchestrationService` STORY-014 (human-in-the-loop) tests
against real PostgreSQL, using `FakeLLMProvider` — never a real network
call.

Proves: a `CoordinatorRequiresApprovalDecision` pauses the coordination
step/run (rather than completing it), persists an `APPROVAL_REQUESTED`
event and a real `ApprovalRequest` row, and reports
`DecisionKind.REQUIRES_APPROVAL`/`approval_id` back to the caller — the
same "durably persisted before the response returns" discipline
STORY-011's handoff/refusal/clarification paths already established (see
tests/ai/test_orchestration.py). Also proves Layer-2 safety screening
applies to the Coordinator's `reason` text exactly like every other
Coordinator-authored message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.definitions import build_default_agent_registry
from app.ai.coordinator_decisions import CoordinatorRequiresApprovalDecision
from app.ai.decisions import DecisionKind
from app.ai.orchestration import AgentOrchestrationService
from app.ai.providers.fake_provider import FakeLLMProvider
from app.ai.tools.registry_builder import build_full_tool_registry
from app.models.approval import ApprovalStatus, ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.user import User
from app.models.workflow import StepStatus, WorkflowEventType, WorkflowRequestType, WorkflowStatus
from app.repositories import approval as approval_repository
from app.repositories import workflow_event as workflow_event_repository
from app.repositories import workflow_step as workflow_step_repository

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]


async def _org_with_admin(
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    suffix: str,
) -> tuple[Organization, User]:
    org = await make_organization(suffix)
    user = await make_user(suffix)
    await make_membership(org, user, role=Role.ADMIN)
    return org, user


def _orchestration(
    db_session: AsyncSession, provider: FakeLLMProvider
) -> AgentOrchestrationService:
    return AgentOrchestrationService(
        db_session, provider, build_full_tool_registry(), build_default_agent_registry()
    )


async def test_requires_approval_decision_pauses_workflow_and_creates_approval(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-appr-pause"
    )
    provider = FakeLLMProvider(
        coordinator_decision=CoordinatorRequiresApprovalDecision(
            approval_type=ApprovalType.HIGH_RISK_ACTION,
            reason="This exceeds the automatic threshold and needs sign-off.",
        )
    )
    orchestration = _orchestration(db_session, provider)

    result = await orchestration.execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        request_text="Please override the booking policy for this one.",
    )

    assert result.decision_kind is DecisionKind.REQUIRES_APPROVAL
    assert result.workflow_status is WorkflowStatus.WAITING
    assert result.handled_by_agent == "coordinator"
    assert result.approval_id is not None

    approval = await approval_repository.get_by_id(
        db_session, organization_id=org.id, approval_id=result.approval_id
    )
    assert approval is not None
    assert approval.status is ApprovalStatus.PENDING
    assert approval.approval_type is ApprovalType.HIGH_RISK_ACTION
    assert approval.requested_by_agent == "coordinator"

    step = await workflow_step_repository.get_by_id(
        db_session,
        organization_id=org.id,
        workflow_run_id=result.workflow_run_id,
        step_id=approval.workflow_step_id,
    )
    assert step is not None
    assert step.status is StepStatus.WAITING

    events = list(
        await workflow_event_repository.list_by_run(
            db_session, organization_id=org.id, workflow_run_id=result.workflow_run_id
        )
    )
    event_types = [e.event_type for e in events]
    assert WorkflowEventType.APPROVAL_REQUESTED in event_types
    assert WorkflowEventType.STEP_WAITING in event_types
    assert WorkflowEventType.WORKFLOW_WAITING in event_types
    # No specialist ever ran, and no handoff was ever recorded.
    assert WorkflowEventType.AGENT_HANDOFF not in event_types


async def test_requires_approval_reason_is_layer_two_screened(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    """Defense-in-depth: even though the raw request text didn't trip
    the pre-screen, a Coordinator-composed `reason` that drifts into
    clinical territory must still be screened out — mirrors
    `SafetyPolicy.screen_decision`'s existing coverage of clarification/
    refusal messages, applied to `CoordinatorRequiresApprovalDecision`."""
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-appr-screened"
    )
    provider = FakeLLMProvider(
        coordinator_decision=CoordinatorRequiresApprovalDecision(
            approval_type=ApprovalType.CUSTOM,
            reason="The patient says they have chest pain and I am not sure how to route it.",
        )
    )
    orchestration = _orchestration(db_session, provider)

    result = await orchestration.execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        request_text="Route this request somewhere reasonable.",
    )

    assert result.decision_kind is DecisionKind.REFUSAL
    assert result.workflow_status is WorkflowStatus.COMPLETED
    assert result.approval_id is None


async def test_requires_approval_never_invokes_a_specialist(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    """The Coordinator's `generate_coordinator_decision` is the only
    provider call made — no specialist decision call happens for a
    `requires_approval` outcome."""
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-appr-no-specialist"
    )
    provider = FakeLLMProvider(
        coordinator_decision=CoordinatorRequiresApprovalDecision(
            approval_type=ApprovalType.MANUAL_RESCHEDULE,
            reason="Ambiguous which practitioner the caller means.",
        )
    )
    orchestration = _orchestration(db_session, provider)

    await orchestration.execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.APPOINTMENT_RESCHEDULING,
        request_text="Move my appointment.",
    )

    assert len(provider.coordinator_calls) == 1
    assert len(provider.calls) == 0
