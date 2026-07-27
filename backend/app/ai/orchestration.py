"""`AgentOrchestrationService`: one administrative request -> one model
decision -> at most one tool execution -> a durably persisted workflow.

This is the ONLY module that wires together `app.ai.providers`,
`app.ai.safety`, `app.ai.tools`, and `app.services.workflow` — see the
`app.ai` package docstring for the trust boundary this enforces, and
docs/adr/ADR-0010-llm-and-tool-security-boundary.md "Target
Architecture" for the exact call chain this implements:

    administrative request
    -> LLM orchestration boundary   (this module)
    -> structured model decision    (app.ai.decisions)
    -> schema validation            (Pydantic, in app.ai.decisions)
    -> safety policy                (app.ai.safety)
    -> explicit tool registry       (app.ai.tools.registry)
    -> authorization context        (app.ai.tools.base.ToolExecutionContext)
    -> tool adapter                 (app.ai.tools.appointment_tools)
    -> existing AgentCare service layer
    -> PostgreSQL
    -> WorkflowStep / WorkflowEvent

No direct LLM -> repository, LLM -> database, or LLM -> arbitrary
Python function path exists anywhere in this chain.

**Scope for this story**: exactly one model decision, at most one tool
execution — never an autonomous multi-step loop. Multi-step planning is
explicitly deferred (see docs/adr/ADR-0010...).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.decisions import (
    AdministrativeDecision,
    ClarificationRequiredDecision,
    DecisionKind,
    RefusalCategory,
    RefusalDecision,
    SafeResponseDecision,
    ToolCallDecision,
)
from app.ai.prompts import SYSTEM_PROMPT
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

_ORCHESTRATOR_ACTOR_IDENTIFIER = "administrative_orchestrator"
_INTERPRET_STEP_TYPE = "interpret_administrative_request"
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
    a raw prompt, provider response, or reasoning trace."""

    workflow_run_id: uuid.UUID
    workflow_status: WorkflowStatus
    decision_kind: DecisionKind
    safe_message: str
    tool_name: str | None
    tool_result_code: str | None
    tool_result_data: dict[str, Any] | None


def _safe_failure_message(message: str) -> str:
    return message[:_FAILURE_MESSAGE_MAX_LENGTH]


class AgentOrchestrationService:
    """Scoped to one `AsyncSession`, one `LLMProvider`, and one
    `ToolRegistry`. Construct fresh per request — see
    `app.api.v1.endpoints.agent`."""

    def __init__(
        self,
        session: AsyncSession,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        safety_policy: SafetyPolicy | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._tool_registry = tool_registry
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
        anything read from `request_text` or a request body field.
        """
        actor_identifier = str(initiated_by_user_id)
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

        step = await self._workflow_service.create_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            sequence_number=1,
            step_type=_INTERPRET_STEP_TYPE,
        )
        await self._workflow_service.start_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=step.id,
            actor_type=ActorType.AGENT,
            actor_identifier=_ORCHESTRATOR_ACTOR_IDENTIFIER,
        )

        tool_context = ToolExecutionContext(
            organization_id=organization_id,
            user_id=initiated_by_user_id,
            role=role,
            patient_id=resolved_patient_id,
            workflow_run_id=run.id,
            workflow_step_id=step.id,
        )

        # Layer 1 of the safety policy: a deterministic, code-level
        # pre-screen that runs BEFORE the model is ever called. See
        # `app.ai.safety` — this is what makes the "chest pain -> no
        # autonomous department routing" guarantee independent of what
        # any provider (real or fake) would have said.
        pre_screen = self._safety_policy.screen_request_text(request_text)
        if pre_screen is not None:
            decision: AdministrativeDecision = RefusalDecision(
                reason_category=RefusalCategory.CLINICAL_CONTENT,
                safe_message=pre_screen.safe_message,
            )
        else:
            try:
                decision = await self._provider.generate_structured(
                    StructuredCompletionRequest(
                        system_prompt=SYSTEM_PROMPT, user_content=request_text
                    )
                )
            except _ProviderError as exc:
                message = _safe_failure_message(exc.message)
                await self._workflow_service.fail_step(
                    organization_id=organization_id,
                    workflow_run_id=run.id,
                    step_id=step.id,
                    actor_type=ActorType.AGENT,
                    actor_identifier=_ORCHESTRATOR_ACTOR_IDENTIFIER,
                    failure_code=exc.error_code,
                    failure_message_safe=message,
                )
                failed_run = await self._workflow_service.fail_workflow(
                    organization_id=organization_id,
                    workflow_run_id=run.id,
                    actor_type=ActorType.USER,
                    actor_identifier=actor_identifier,
                    failure_code=exc.error_code,
                    failure_message_safe=message,
                )
                return AgentExecutionResult(
                    workflow_run_id=run.id,
                    workflow_status=failed_run.status,
                    decision_kind=DecisionKind.REFUSAL,
                    safe_message="This request could not be processed right now.",
                    tool_name=None,
                    tool_result_code=exc.error_code,
                    tool_result_data=None,
                )

            # Layer 2: a post-decision screen — defense-in-depth in case
            # a non-tool-call decision's own message content drifts into
            # clinical territory even when the raw request text didn't
            # trip the pre-screen.
            post_screen = self._safety_policy.screen_decision(decision)
            if post_screen is not None:
                decision = RefusalDecision(
                    reason_category=RefusalCategory.CLINICAL_CONTENT,
                    safe_message=post_screen.safe_message,
                )

        if isinstance(decision, ToolCallDecision):
            return await self._execute_tool_call(
                decision,
                organization_id=organization_id,
                run_id=run.id,
                step_id=step.id,
                actor_identifier=actor_identifier,
                tool_context=tool_context,
            )

        return await self._complete_without_tool(
            decision,
            organization_id=organization_id,
            run_id=run.id,
            step_id=step.id,
            actor_identifier=actor_identifier,
        )

    async def _execute_tool_call(
        self,
        decision: ToolCallDecision,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_identifier: str,
        tool_context: ToolExecutionContext,
    ) -> AgentExecutionResult:
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
                actor_identifier=_ORCHESTRATOR_ACTOR_IDENTIFIER,
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
            actor_identifier=_ORCHESTRATOR_ACTOR_IDENTIFIER,
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
            safe_message=tool_result.safe_message,
            tool_name=decision.tool_name,
            tool_result_code=tool_result.code,
            tool_result_data=None,
        )

    async def _complete_without_tool(
        self,
        decision: ClarificationRequiredDecision | SafeResponseDecision | RefusalDecision,
        *,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        actor_identifier: str,
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
            actor_identifier=_ORCHESTRATOR_ACTOR_IDENTIFIER,
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
            safe_message=safe_message,
            tool_name=None,
            tool_result_code=None,
            tool_result_data=None,
        )
