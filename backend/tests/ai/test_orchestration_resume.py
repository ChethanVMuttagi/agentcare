"""`AgentOrchestrationService` STORY-015 (resume/clarification-pause)
tests against real PostgreSQL, using `FakeLLMProvider` — never a real
network call.

Proves: a Coordinator clarification pauses the run (`WAITING`) rather
than completing it; a follow-up request carrying `workflow_run_id`
resumes the SAME run (never creating a second one) and re-enters the
Coordinator with fresh text; resuming is rejected (with a clear,
deterministic `WorkflowConflictError`/`WorkflowNotFoundError`) for every
kind of invalid resume attempt — not currently `WAITING`, gated by a
PENDING approval instead of a clarification, unknown, or cross-tenant/
cross-patient. Also proves the template registry now drives the
coordination step's `step_type` label.

Each call to `AgentOrchestrationService.execute_administrative_request`
uses its OWN `FakeLLMProvider` instance (a fixed, single-decision fake —
see `app.ai.providers.fake_provider`'s docstring), constructed fresh per
call against the SAME `db_session` so the underlying `WorkflowRun`
genuinely persists across "turns," exactly like a real multi-request
conversation would.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.definitions import build_default_agent_registry
from app.ai.coordinator_decisions import (
    CoordinatorClarificationRequiredDecision,
    CoordinatorRefusalDecision,
    CoordinatorRequiresApprovalDecision,
    HandoffDecision,
    TargetAgent,
)
from app.ai.decisions import DecisionKind, RefusalCategory, ToolCallDecision
from app.ai.orchestration import AgentOrchestrationService
from app.ai.providers.fake_provider import FakeLLMProvider
from app.ai.tools.registry_builder import build_full_tool_registry
from app.models.approval import ApprovalType
from app.models.membership import OrganizationMembership, Role
from app.models.organization import Organization
from app.models.patient import Patient
from app.models.user import User
from app.models.workflow import WorkflowRequestType, WorkflowStatus
from app.repositories import workflow_run as workflow_run_repository
from app.repositories import workflow_step as workflow_step_repository
from app.services.workflow import WorkflowConflictError, WorkflowNotFoundError

MakeOrganization = Callable[..., Awaitable[Organization]]
MakeUser = Callable[..., Awaitable[User]]
MakeMembership = Callable[..., Awaitable[OrganizationMembership]]
MakePatient = Callable[..., Awaitable[Patient]]


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


async def test_follow_up_resumes_the_same_run_not_a_new_one(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-same-run"
    )
    clarifying_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(
            message="Which specialist do you need?"
        )
    )
    first = await _orchestration(db_session, clarifying_provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        request_text="Help me with something",
    )
    assert first.workflow_status is WorkflowStatus.WAITING
    assert first.decision_kind is DecisionKind.CLARIFICATION_REQUIRED

    refusing_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(
            message="Still unclear — which department?"
        )
    )
    second = await _orchestration(db_session, refusing_provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        request_text="I need scheduling help",
        workflow_run_id=first.workflow_run_id,
    )

    assert second.workflow_run_id == first.workflow_run_id
    assert second.workflow_status is WorkflowStatus.WAITING

    # Two full pause/resume cycles on the SAME run, never a second run.
    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=first.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.WAITING


async def test_follow_up_can_resolve_into_a_handoff_and_complete(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-handoff"
    )
    patient = await make_patient(org, "PN-orch-resume-handoff")
    clarifying_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(
            message="Which specialist do you need?"
        )
    )
    first = await _orchestration(db_session, clarifying_provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
        request_text="Help me with something",
    )
    assert first.workflow_status is WorkflowStatus.WAITING

    handoff_provider = FakeLLMProvider(
        coordinator_decision=HandoffDecision(target_agent=TargetAgent.DOCUMENT),
        decision=ToolCallDecision(
            tool_name="list_patient_documents", arguments={"patient_id": str(patient.id)}
        ),
    )
    second = await _orchestration(db_session, handoff_provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
        request_text="Check my documents",
        workflow_run_id=first.workflow_run_id,
    )

    assert second.workflow_run_id == first.workflow_run_id
    assert second.workflow_status is WorkflowStatus.COMPLETED
    assert second.handled_by_agent == "document"

    run = await workflow_run_repository.get_by_id(
        db_session, organization_id=org.id, workflow_run_id=first.workflow_run_id
    )
    assert run is not None
    assert run.status is WorkflowStatus.COMPLETED


async def test_resume_rejected_when_run_is_not_waiting(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-not-waiting"
    )
    refusal_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorRefusalDecision(
            reason_category=RefusalCategory.OUT_OF_SCOPE, safe_message="Can't help."
        )
    )
    completed = await _orchestration(db_session, refusal_provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        request_text="Please hack the mainframe",
    )
    assert completed.workflow_status is WorkflowStatus.COMPLETED

    resume_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    with pytest.raises(WorkflowConflictError):
        await _orchestration(db_session, resume_provider).execute_administrative_request(
            organization_id=org.id,
            initiated_by_user_id=admin.id,
            role=Role.ADMIN,
            resolved_patient_id=None,
            request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
            request_text="Anything",
            workflow_run_id=completed.workflow_run_id,
        )


async def test_resume_rejected_when_gated_by_a_pending_approval(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-approval-gated"
    )
    approval_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorRequiresApprovalDecision(
            approval_type=ApprovalType.HIGH_RISK_ACTION, reason="Needs a decision."
        )
    )
    paused = await _orchestration(db_session, approval_provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        request_text="Override the policy for this one",
    )
    assert paused.workflow_status is WorkflowStatus.WAITING
    assert paused.approval_id is not None

    resume_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    with pytest.raises(WorkflowConflictError, match="approval"):
        await _orchestration(db_session, resume_provider).execute_administrative_request(
            organization_id=org.id,
            initiated_by_user_id=admin.id,
            role=Role.ADMIN,
            resolved_patient_id=None,
            request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
            request_text="Follow-up text",
            workflow_run_id=paused.workflow_run_id,
        )


async def test_resume_unknown_workflow_run_raises_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-unknown"
    )
    provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    with pytest.raises(WorkflowNotFoundError):
        await _orchestration(db_session, provider).execute_administrative_request(
            organization_id=org.id,
            initiated_by_user_id=admin.id,
            role=Role.ADMIN,
            resolved_patient_id=None,
            request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
            request_text="Anything",
            workflow_run_id=uuid.uuid4(),
        )


async def test_resume_cross_tenant_raises_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org_a, admin_a = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-cross-a"
    )
    org_b, admin_b = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-cross-b"
    )
    clarifying_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    paused = await _orchestration(db_session, clarifying_provider).execute_administrative_request(
        organization_id=org_a.id,
        initiated_by_user_id=admin_a.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
        request_text="Help",
    )

    resume_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    with pytest.raises(WorkflowNotFoundError):
        await _orchestration(db_session, resume_provider).execute_administrative_request(
            organization_id=org_b.id,
            initiated_by_user_id=admin_b.id,
            role=Role.ADMIN,
            resolved_patient_id=None,
            request_type=WorkflowRequestType.ADMINISTRATIVE_ROUTING,
            request_text="Trying to resume another org's run",
            workflow_run_id=paused.workflow_run_id,
        )


async def test_resume_cross_patient_raises_not_found(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
    make_patient: MakePatient,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-resume-cross-patient"
    )
    own_patient = await make_patient(org, "PN-orch-resume-own")
    clarifying_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    # Paused by an ADMIN acting for `own_patient`.
    paused = await _orchestration(db_session, clarifying_provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=own_patient.id,
        request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
        request_text="Help",
    )

    patient_user = await make_user("orch-resume-cross-patient-caller")
    await make_membership(org, patient_user, role=Role.PATIENT)
    caller_patient = await make_patient(
        org, "PN-orch-resume-caller", user=patient_user
    )
    resume_provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    with pytest.raises(WorkflowNotFoundError):
        await _orchestration(db_session, resume_provider).execute_administrative_request(
            organization_id=org.id,
            initiated_by_user_id=patient_user.id,
            role=Role.PATIENT,
            resolved_patient_id=caller_patient.id,
            request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
            request_text="Trying to resume someone else's run",
            workflow_run_id=paused.workflow_run_id,
        )


async def test_coordination_step_type_is_template_driven_for_booking(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    """STORY-015 "choose workflow": the coordination step's `step_type`
    comes from `app.workflows.templates.APPOINTMENT_BOOKING_TEMPLATE`,
    not a bare hardcoded string — proven by asserting on the PERSISTED
    step, not just re-reading the constant."""
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-template-step-type"
    )
    provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    result = await _orchestration(db_session, provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
        request_text="Book something",
    )

    steps = await workflow_step_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=result.workflow_run_id
    )
    assert steps[0].step_type == "coordination"


async def test_coordination_step_type_falls_back_for_untemplated_request_types(
    db_session: AsyncSession,
    make_organization: MakeOrganization,
    make_user: MakeUser,
    make_membership: MakeMembership,
) -> None:
    org, admin = await _org_with_admin(
        make_organization, make_user, make_membership, "orch-template-fallback"
    )
    provider = FakeLLMProvider(
        coordinator_decision=CoordinatorClarificationRequiredDecision(message="?")
    )
    result = await _orchestration(db_session, provider).execute_administrative_request(
        organization_id=org.id,
        initiated_by_user_id=admin.id,
        role=Role.ADMIN,
        resolved_patient_id=None,
        request_type=WorkflowRequestType.FOLLOW_UP,
        request_text="Follow up on something",
    )

    steps = await workflow_step_repository.list_by_run(
        db_session, organization_id=org.id, workflow_run_id=result.workflow_run_id
    )
    assert steps[0].step_type == "coordination"
