"""`AgentOrchestrationService`: one administrative request -> one
Coordinator decision -> at most one specialist handoff -> at most one
specialist model decision -> at most one tool execution -> a durably
persisted workflow.

STORY-011 REPLACES STORY-010's single-decision flow with genuine
multi-agent coordination, while keeping every STORY-010 security
guarantee fully intact:

    administrative request
    -> deterministic pre-screen        (app.ai.safety, unchanged)
    -> Coordinator decision            (app.ai.coordinator_decisions —
                                         structurally cannot express a
                                         tool call: no such variant
                                         exists in this union)
    -> [refusal | clarification]       -> workflow completes, no
                                           specialist ever runs
    -> [handoff to one specialist]     -> persisted AGENT_HANDOFF event
    -> specialist's OWN system prompt  (app.ai.agents.prompts)
    -> SAME original request_text      (never a coordinator-composed
                                         prompt — see
                                         `app.ai.coordinator_decisions.HandoffDecision`)
    -> specialist decision             (app.ai.decisions.AdministrativeDecision
                                         — reused, but validated against
                                         THIS specialist's OWN
                                         `AgentDefinition.allowed_tools`)
    -> per-agent tool allowlist check  (application code, in THIS
                                         module — BEFORE the tool
                                         registry is ever consulted)
    -> explicit tool registry          (app.ai.tools.registry)
    -> authorization context           (app.ai.tools.base.ToolExecutionContext
                                         — server-constructed, identical
                                         for coordinator and specialist,
                                         never influenced by either)
    -> tool adapter                    (app.ai.tools.*)
    -> existing AgentCare service layer
    -> PostgreSQL
    -> WorkflowStep / WorkflowEvent (coordination step, specialist
       execution step, handoff event, tool_invoked event)

Hard caps for this story (see docs/AGENTS.md "Execution Limits"):
Coordinator decision <= 1, specialist handoff <= 1, specialist model
decision <= 1, tool execution <= 1. No specialist-to-specialist
handoff, no recursion, no agent loop, no autonomous retry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.base import AgentDefinition
from app.ai.agents.definitions import build_default_agent_registry
from app.ai.agents.registry import AgentRegistry
from app.ai.coordinator_decisions import (
    CoordinatorClarificationRequiredDecision,
    CoordinatorDecision,
    CoordinatorRefusalDecision,
    HandoffDecision,
)
from app.ai.decisions import (
    ClarificationRequiredDecision,
    DecisionKind,
    RefusalCategory,
    RefusalDecision,
    SafeResponseDecision,
    ToolCallDecision,
)
from app.ai.providers.base import LLMProvider, StructuredCompletionRequest
from app.ai.providers.errors import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.ai.safety import SafetyPolicy
from app.ai.tools.base import ToolExecutionContext, ToolResultStatus
from app.ai.tools.registry import ToolRegistry
from app.models.membership import Role
from app.models.workflow import ActorType, WorkflowRequestType, WorkflowStatus
from app.services.workflow import WorkflowService

_COORDINATION_STEP_TYPE = "coordination"
_SPECIALIST_STEP_TYPE = "specialist_execution"
_FAILURE_MESSAGE_MAX_LENGTH = 500

_ProviderError = (
    ProviderConfigurationError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    ProviderResponseError,
)


@dataclass(frozen=True)
class AgentExecutionResult:
    """Everything the API layer needs to build a safe response — never
    a raw prompt, provider response, or reasoning trace.

    `handled_by_agent` is the stable agent name (`"coordinator"`,
    `"scheduling"`, `"document"`, or `"routing"`) that produced the
    FINAL outcome — `"coordinator"` when the request never left the
    Coordinator (refusal, clarification, or a Coordinator-stage
    failure), otherwise the specialist that was handed off to. Never a
    provider or model name.
    """

    workflow_run_id: uuid.UUID
    workflow_status: WorkflowStatus
    decision_kind: DecisionKind
    handled_by_agent: str
    safe_message: str
    tool_name: str | None
    tool_result_code: str | None
    tool_result_data: dict[str, Any] | None


def _safe_failure_message(message: str) -> str:
    return message[:_FAILURE_MESSAGE_MAX_LENGTH]


class AgentOrchestrationService:
    """Scoped to one `AsyncSession`, one `LLMProvider`, one
    `ToolRegistry`, and one `AgentRegistry`. Construct fresh per request
    — see `app.api.v1.endpoints.agent`."""

    def __init__(
        self,
        session: AsyncSession,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        agent_registry: AgentRegistry | None = None,
        safety_policy: SafetyPolicy | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._tool_registry = tool_registry
        self._agent_registry = agent_registry or build_default_agent_registry()
        self._safety_policy = safety_policy or SafetyPolicy()
        self._workflow_service = WorkflowService(session)

    async def execute_administrative_request(
        self,
        *,
        organization_id: uuid.UUID,
        initiated_by_user_id: uuid.UUID,
        role: Role,
        resolved_patient_id: uuid.UUID | None,
        request_type: WorkflowRequestType,
        request_text: str,
    ) -> AgentExecutionResult:
        """`resolved_patient_id` must already be SERVER-DERIVED and
        trusted by the caller (see
        `app.api.v1.endpoints.agent._resolve_request_patient_id`,
        mirroring `app.api.v1.endpoints.workflows`) — for a `PATIENT`
        caller this is ALWAYS their own linked patient id, never
        anything read from `request_text`, a request body field, or
        anything either the Coordinator or a specialist might say.
        """
        actor_identifier = str(initiated_by_user_id)
        coordinator = self._agent_registry.get("coordinator")
        assert coordinator is not None  # registered by build_default_agent_registry()

        run = await self._workflow_service.create_workflow(
            organization_id=organization_id,
            initiated_by_user_id=initiated_by_user_id,
            request_type=request_type,
            patient_id=resolved_patient_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        await self._workflow_service.start_workflow(
            organization_id=organization_id,
            workflow_run_id=run.id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )

        coordination_step = await self._workflow_service.create_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            sequence_number=1,
            step_type=_COORDINATION_STEP_TYPE,
            agent_name=coordinator.name,
        )
        await self._workflow_service.start_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=coordination_step.id,
            actor_type=ActorType.AGENT,
            actor_identifier=coordinator.name,
        )

        # Layer 1 of the safety policy: a deterministic, code-level
        # pre-screen that runs BEFORE the Coordinator is ever called.
        # See `app.ai.safety` — this is what makes the "chest pain -> no
        # autonomous department routing" guarantee independent of what
        # any provider (real or fake) would have said, for EVERY agent
        # this request could possibly reach.
        pre_screen = self._safety_policy.screen_request_text(request_text)
        if pre_screen is not None:
            return await self._complete_coordinator_only(
                RefusalDecision(
                    reason_category=RefusalCategory.CLINICAL_CONTENT,
                    safe_message=pre_screen.safe_message,
                ),
                organization_id=organization_id,
                run_id=run.id,
                step_id=coordination_step.id,
                actor_identifier=actor_identifier,
                coordinator_name=coordinator.name,
            )

        try:
            coordinator_decision: CoordinatorDecision = (
                await self._provider.generate_coordinator_decision(
                    StructuredCompletionRequest(
                        system_prompt=coordinator.system_prompt, user_content=request_text
                    )
                )
            )
        except _ProviderError as exc:
            return await self._fail_at_coordinator(
                exc,
                organization_id=organization_id,
                run_id=run.id,
                step_id=coordination_step.id,
                actor_identifier=actor_identifier,
                coordinator_name=coordinator.name,
            )

        # Layer 2: a post-decision screen — defense-in-depth in case the
        # Coordinator's own clarification/refusal message content drifts
        # into clinical territory even when the raw request text didn't
        # trip the pre-screen. Mirrors `SafetyPolicy.screen_decision`,
        # applied directly to the Coordinator's message (a
        # `CoordinatorDecision` is a different type than
        # `AdministrativeDecision`, so that typed method doesn't apply
        # here — the underlying text scan is the same). A `HandoffDecision`
        # carries no free-form text at all, so it is never screened here.
        screened_refusal: RefusalDecision | None = None
        coordinator_message = _coordinator_decision_message(coordinator_decision)
        if coordinator_message is not None:
            post_screen = self._safety_policy.screen_request_text(coordinator_message)
            if post_screen is not None:
                screened_refusal = RefusalDecision(
                    reason_category=RefusalCategory.CLINICAL_CONTENT,
                    safe_message=post_screen.safe_message,
                )

        if screened_refusal is None and isinstance(coordinator_decision, HandoffDecision):
            return await self._execute_handoff(
                coordinator_decision,
                organization_id=organization_id,
                run_id=run.id,
                coordination_step_id=coordination_step.id,
                actor_identifier=actor_identifier,
                coordinator_name=coordinator.name,
                role=role,
                initiated_by_user_id=initiated_by_user_id,
                resolved_patient_id=resolved_patient_id,
                request_text=request_text,
            )

        # `CoordinatorClarificationRequiredDecision`/`CoordinatorRefusalDecision`,
        # or a Layer-2-screened `RefusalDecision` substitution above —
        # either way, the request never leaves the Coordinator.
        final_decision: ClarificationRequiredDecision | RefusalDecision
        if screened_refusal is not None:
            final_decision = screened_refusal
        elif isinstance(coordinator_decision, CoordinatorRefusalDecision):
            final_decision = RefusalDecision(
                reason_category=coordinator_decision.reason_category,
                safe_message=coordinator_decision.safe_message,
            )
        else:
            assert isinstance(coordinator_decision, CoordinatorClarificationRequiredDecision)
            final_decision = ClarificationRequiredDecision(message=coordinator_decision.message)

        return await self._complete_coordinator_only(
            final_decision,
            organization_id=organization_id,
            run_id=run.id,
            step_id=coordination_step.id,
            actor_identifier=actor_identifier,
            coordinator_name=coordinator.name,
        )

    async def _fail_at_coordinator(
        self,
        exc: Exception,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_identifier: str,
        coordinator_name: str,
    ) -> AgentExecutionResult:
        error_code = getattr(exc, "error_code", "provider_error")
        message = _safe_failure_message(getattr(exc, "message", str(exc)))
        await self._workflow_service.fail_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=step_id,
            actor_type=ActorType.AGENT,
            actor_identifier=coordinator_name,
            failure_code=error_code,
            failure_message_safe=message,
        )
        failed_run = await self._workflow_service.fail_workflow(
            organization_id=organization_id,
            workflow_run_id=run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
            failure_code=error_code,
            failure_message_safe=message,
        )
        return AgentExecutionResult(
            workflow_run_id=run_id,
            workflow_status=failed_run.status,
            decision_kind=DecisionKind.REFUSAL,
            handled_by_agent=coordinator_name,
            safe_message="This request could not be processed right now.",
            tool_name=None,
            tool_result_code=error_code,
            tool_result_data=None,
        )

    async def _complete_coordinator_only(
        self,
        decision: ClarificationRequiredDecision | RefusalDecision,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_identifier: str,
        coordinator_name: str,
    ) -> AgentExecutionResult:
        """The request never leaves the Coordinator — no handoff, no
        specialist step. Mirrors STORY-010's `_complete_without_tool`
        shape, applied to the coordination step only."""
        if isinstance(decision, RefusalDecision):
            safe_message = decision.safe_message
            metadata = {
                "decision_kind": decision.kind.value,
                "reason": decision.reason_category.value,
            }
        else:
            safe_message = decision.message
            metadata = {"decision_kind": decision.kind.value}

        await self._workflow_service.complete_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=step_id,
            actor_type=ActorType.AGENT,
            actor_identifier=coordinator_name,
            safe_metadata=metadata,
        )
        completed_run = await self._workflow_service.complete_workflow(
            organization_id=organization_id,
            workflow_run_id=run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        return AgentExecutionResult(
            workflow_run_id=run_id,
            workflow_status=completed_run.status,
            decision_kind=decision.kind,
            handled_by_agent=coordinator_name,
            safe_message=safe_message,
            tool_name=None,
            tool_result_code=None,
            tool_result_data=None,
        )

    async def _execute_handoff(
        self,
        decision: HandoffDecision,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        coordination_step_id: uuid.UUID,
        actor_identifier: str,
        coordinator_name: str,
        role: Role,
        initiated_by_user_id: uuid.UUID,
        resolved_patient_id: uuid.UUID | None,
        request_text: str,
    ) -> AgentExecutionResult:
        specialist = self._agent_registry.get(decision.target_agent.value)
        if specialist is None:
            # Defense-in-depth: `HandoffDecision.target_agent` is a
            # closed enum whose members are already exactly the
            # registered specialist names (a malformed/invented agent
            # name fails Pydantic validation upstream, in
            # `app.ai.coordinator_decisions`, long before this method is
            # reached — see docs/AGENTS.md "Unknown Agent Handling").
            # This branch exists so a future registry/enum drift fails
            # safe instead of raising.
            return await self._fail_at_coordinator(
                ProviderResponseError("The chosen agent is not available."),
                organization_id=organization_id,
                run_id=run_id,
                step_id=coordination_step_id,
                actor_identifier=actor_identifier,
                coordinator_name=coordinator_name,
            )

        # Persist the handoff (audit event) and complete the
        # coordination step BEFORE any specialist code runs — the
        # Coordinator's own participation is durably recorded regardless
        # of what happens next.
        await self._workflow_service.record_agent_handoff(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=coordination_step_id,
            actor_type=ActorType.AGENT,
            actor_identifier=coordinator_name,
            from_agent=coordinator_name,
            to_agent=specialist.name,
        )
        await self._workflow_service.complete_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=coordination_step_id,
            actor_type=ActorType.AGENT,
            actor_identifier=coordinator_name,
            safe_metadata={"decision_kind": "handoff", "target_agent": specialist.name},
        )

        specialist_step = await self._workflow_service.create_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            sequence_number=2,
            step_type=_SPECIALIST_STEP_TYPE,
            agent_name=specialist.name,
        )
        await self._workflow_service.start_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=specialist_step.id,
            actor_type=ActorType.AGENT,
            actor_identifier=specialist.name,
        )

        # SAME trusted context the coordination step would have used —
        # a handoff can never elevate or otherwise change organization,
        # user, role, or patient self-scope. Only `workflow_step_id`
        # differs (it now points at the specialist's own step).
        tool_context = ToolExecutionContext(
            organization_id=organization_id,
            user_id=initiated_by_user_id,
            role=role,
            patient_id=resolved_patient_id,
            workflow_run_id=run_id,
            workflow_step_id=specialist_step.id,
        )

        try:
            specialist_decision = await self._provider.generate_structured(
                StructuredCompletionRequest(
                    system_prompt=specialist.system_prompt, user_content=request_text
                )
            )
        except _ProviderError as exc:
            return await self._fail_specialist_step(
                exc,
                organization_id=organization_id,
                run_id=run_id,
                step_id=specialist_step.id,
                actor_identifier=actor_identifier,
                specialist_name=specialist.name,
            )

        # Layer 2 for the specialist's own decision — unchanged from
        # STORY-010's `SafetyPolicy.screen_decision`.
        post_screen = self._safety_policy.screen_decision(specialist_decision)
        if post_screen is not None:
            specialist_decision = RefusalDecision(
                reason_category=RefusalCategory.CLINICAL_CONTENT,
                safe_message=post_screen.safe_message,
            )

        if isinstance(specialist_decision, ToolCallDecision):
            return await self._execute_specialist_tool_call(
                specialist_decision,
                specialist=specialist,
                organization_id=organization_id,
                run_id=run_id,
                step_id=specialist_step.id,
                actor_identifier=actor_identifier,
                tool_context=tool_context,
            )

        return await self._complete_specialist_without_tool(
            specialist_decision,
            organization_id=organization_id,
            run_id=run_id,
            step_id=specialist_step.id,
            actor_identifier=actor_identifier,
            specialist_name=specialist.name,
        )

    async def _fail_specialist_step(
        self,
        exc: Exception,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_identifier: str,
        specialist_name: str,
    ) -> AgentExecutionResult:
        error_code = getattr(exc, "error_code", "provider_error")
        message = _safe_failure_message(getattr(exc, "message", str(exc)))
        await self._workflow_service.fail_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=step_id,
            actor_type=ActorType.AGENT,
            actor_identifier=specialist_name,
            failure_code=error_code,
            failure_message_safe=message,
        )
        failed_run = await self._workflow_service.fail_workflow(
            organization_id=organization_id,
            workflow_run_id=run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
            failure_code=error_code,
            failure_message_safe=message,
        )
        return AgentExecutionResult(
            workflow_run_id=run_id,
            workflow_status=failed_run.status,
            decision_kind=DecisionKind.REFUSAL,
            handled_by_agent=specialist_name,
            safe_message="This request could not be processed right now.",
            tool_name=None,
            tool_result_code=error_code,
            tool_result_data=None,
        )

    async def _execute_specialist_tool_call(
        self,
        decision: ToolCallDecision,
        *,
        specialist: AgentDefinition,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_identifier: str,
        tool_context: ToolExecutionContext,
    ) -> AgentExecutionResult:
        # THE per-agent enforcement point: `ToolRegistry` itself does
        # not know or care which agent is calling it (see
        # `app.ai.tools.registry`'s docstring) — this explicit
        # allowlist check is what makes "Document cannot call
        # book_appointment" true, even though `book_appointment` is a
        # perfectly valid, registered tool. Checked BEFORE the tool
        # registry is ever consulted — an unlisted tool never reaches
        # `ToolRegistry.execute`, regardless of whether that name would
        # also fail there as unknown.
        if decision.tool_name not in specialist.allowed_tools:
            await self._workflow_service.fail_step(
                organization_id=organization_id,
                workflow_run_id=run_id,
                step_id=step_id,
                actor_type=ActorType.AGENT,
                actor_identifier=specialist.name,
                failure_code="forbidden_tool",
                failure_message_safe="This agent is not permitted to use that tool.",
            )
            failed_run = await self._workflow_service.fail_workflow(
                organization_id=organization_id,
                workflow_run_id=run_id,
                actor_type=ActorType.USER,
                actor_identifier=actor_identifier,
                failure_code="forbidden_tool",
                failure_message_safe="This agent is not permitted to use that tool.",
            )
            return AgentExecutionResult(
                workflow_run_id=run_id,
                workflow_status=failed_run.status,
                decision_kind=DecisionKind.TOOL_CALL,
                handled_by_agent=specialist.name,
                safe_message="This agent is not permitted to use that tool.",
                tool_name=decision.tool_name,
                tool_result_code="forbidden_tool",
                tool_result_data=None,
            )

        await self._workflow_service.record_tool_invocation(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=step_id,
            actor_type=ActorType.TOOL,
            actor_identifier=decision.tool_name[:100],
            tool_name=decision.tool_name,
        )

        tool_result = await self._tool_registry.execute(
            decision.tool_name, decision.arguments, tool_context, self._session
        )

        if tool_result.status is ToolResultStatus.SUCCESS:
            await self._workflow_service.complete_step(
                organization_id=organization_id,
                workflow_run_id=run_id,
                step_id=step_id,
                actor_type=ActorType.AGENT,
                actor_identifier=specialist.name,
                safe_metadata={"tool_name": decision.tool_name, "result_code": tool_result.code},
            )
            completed_run = await self._workflow_service.complete_workflow(
                organization_id=organization_id,
                workflow_run_id=run_id,
                actor_type=ActorType.USER,
                actor_identifier=actor_identifier,
            )
            return AgentExecutionResult(
                workflow_run_id=run_id,
                workflow_status=completed_run.status,
                decision_kind=DecisionKind.TOOL_CALL,
                handled_by_agent=specialist.name,
                safe_message=tool_result.safe_message,
                tool_name=decision.tool_name,
                tool_result_code=tool_result.code,
                tool_result_data=tool_result.data,
            )

        await self._workflow_service.fail_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=step_id,
            actor_type=ActorType.AGENT,
            actor_identifier=specialist.name,
            failure_code=tool_result.code,
            failure_message_safe=tool_result.safe_message,
        )
        failed_run = await self._workflow_service.fail_workflow(
            organization_id=organization_id,
            workflow_run_id=run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
            failure_code=tool_result.code,
            failure_message_safe=tool_result.safe_message,
        )
        return AgentExecutionResult(
            workflow_run_id=run_id,
            workflow_status=failed_run.status,
            decision_kind=DecisionKind.TOOL_CALL,
            handled_by_agent=specialist.name,
            safe_message=tool_result.safe_message,
            tool_name=decision.tool_name,
            tool_result_code=tool_result.code,
            tool_result_data=None,
        )

    async def _complete_specialist_without_tool(
        self,
        decision: ClarificationRequiredDecision | SafeResponseDecision | RefusalDecision,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_identifier: str,
        specialist_name: str,
    ) -> AgentExecutionResult:
        if isinstance(decision, RefusalDecision):
            safe_message = decision.safe_message
            metadata = {
                "decision_kind": decision.kind.value,
                "reason": decision.reason_category.value,
            }
        else:
            safe_message = decision.message
            metadata = {"decision_kind": decision.kind.value}

        await self._workflow_service.complete_step(
            organization_id=organization_id,
            workflow_run_id=run_id,
            step_id=step_id,
            actor_type=ActorType.AGENT,
            actor_identifier=specialist_name,
            safe_metadata=metadata,
        )
        completed_run = await self._workflow_service.complete_workflow(
            organization_id=organization_id,
            workflow_run_id=run_id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        return AgentExecutionResult(
            workflow_run_id=run_id,
            workflow_status=completed_run.status,
            decision_kind=decision.kind,
            handled_by_agent=specialist_name,
            safe_message=safe_message,
            tool_name=None,
            tool_result_code=None,
            tool_result_data=None,
        )


def _coordinator_decision_message(decision: CoordinatorDecision) -> str | None:
    """The safe, caller-facing text a `CoordinatorDecision` carries, if
    any — `None` for `HandoffDecision` (nothing textual to screen; the
    handoff itself carries no free-form content)."""
    if isinstance(decision, CoordinatorClarificationRequiredDecision):
        return decision.message
    if isinstance(decision, CoordinatorRefusalDecision):
        return decision.safe_message
    return None
