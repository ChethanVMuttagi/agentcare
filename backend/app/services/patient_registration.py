"""PatientRegistrationService: the Patient Registration workflow
template (STORY-015), executed entirely through the Workflow Engine and
Approval Engine.

Unlike Appointment Booking/Rescheduling/Document Collection, Patient
Registration has NO natural-language step at all — "register a patient
with these exact fields" is structured data, not something requiring
model interpretation, so this service never touches
`app.ai.orchestration.AgentOrchestrationService`, the Coordinator, or
any specialist. It is the ONE workflow template driven directly by a
deterministic service, exactly mirroring how `app.services.reminder.ReminderService`
drives the reminder-delivery workflow kind without any model involvement
either.

`app.workflows.templates.PATIENT_REGISTRATION_TEMPLATE`'s two steps:

1. `patient_duplicate_check` — a HARD conflict (the same `patient_number`
   already exists) fails the workflow outright; a SOFT match (same name
   + date of birth, different patient number) pauses the workflow via
   `app.services.approval.ApprovalService.create_approval_request` for a
   human to decide whether this is genuinely a new patient.
2. `patient_record_creation` — only reached once the duplicate check is
   clear; calls `app.services.patient.PatientService.create_patient`
   directly (never duplicates its validation).

## Scope Boundary: What Approval Actually Resumes

Per docs/adr/ADR-0013-human-in-the-loop-approvals.md, approving a paused
`ApprovalRequest` resumes and COMPLETES the workflow run it gates — it
does not itself trigger further business logic. Approving the
duplicate-check step therefore completes the REGISTRATION WORKFLOW's
own administrative decision ("yes, register this patient despite the
apparent duplicate") — it does NOT automatically create a second patient
record. Actually creating that record, once approved, is a deliberate,
separate, explicit action (e.g. the approving staff member submits
`POST .../patients` normally) — never an automatic tool-call replay.
This is the same scope boundary STORY-014 already established, applied
honestly here rather than fought.

`WorkflowRun.patient_id` is always `NULL` for this workflow kind — see
`WorkflowRequestType.PATIENT_REGISTRATION`'s docstring.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalType
from app.models.workflow import ActorType, WorkflowRequestType, WorkflowRun
from app.repositories import patient as patient_repository
from app.services.approval import ApprovalService
from app.services.patient import InvalidPatientLinkError, PatientNumberConflictError, PatientService
from app.services.workflow import WorkflowService
from app.workflows.templates import PATIENT_REGISTRATION_TEMPLATE

_ACTOR_IDENTIFIER = "patient_registration_workflow"
_FAILURE_MESSAGE_MAX_LENGTH = 500
_DUPLICATE_NUMBER_FAILURE_CODE = "patient_number_conflict"
_DUPLICATE_REASON_MAX_LENGTH = 500


def _safe_failure_message(message: str) -> str:
    return message[:_FAILURE_MESSAGE_MAX_LENGTH]


class PatientRegistrationService:
    """Drives the Patient Registration workflow template, scoped to one
    `AsyncSession`. Never raises for a business-rule outcome (a hard
    conflict, or a soft duplicate pausing for approval) — `start_registration`
    ALWAYS returns the `WorkflowRun`; its `.status`/`.failure_code`
    describe what happened, exactly like
    `app.ai.orchestration.AgentOrchestrationService.execute_administrative_request`
    never raises for a Coordinator-decided outcome either."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._workflow_service = WorkflowService(session)
        self._approval_service = ApprovalService(session)
        self._patient_service = PatientService(session)

    async def start_registration(
        self,
        *,
        organization_id: uuid.UUID,
        initiated_by_user_id: uuid.UUID,
        patient_number: str,
        first_name: str,
        last_name: str,
        date_of_birth: date,
        user_id: uuid.UUID | None = None,
    ) -> WorkflowRun:
        actor_identifier = str(initiated_by_user_id)
        run = await self._workflow_service.create_workflow(
            organization_id=organization_id,
            initiated_by_user_id=initiated_by_user_id,
            request_type=WorkflowRequestType.PATIENT_REGISTRATION,
            patient_id=None,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )
        await self._workflow_service.start_workflow(
            organization_id=organization_id,
            workflow_run_id=run.id,
            actor_type=ActorType.USER,
            actor_identifier=actor_identifier,
        )

        duplicate_check_template = PATIENT_REGISTRATION_TEMPLATE.steps[0]
        step1 = await self._workflow_service.create_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            sequence_number=duplicate_check_template.sequence_number,
            step_type=duplicate_check_template.step_type,
        )
        await self._workflow_service.start_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=step1.id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_ACTOR_IDENTIFIER,
        )

        hard_conflict = await patient_repository.get_by_patient_number(
            self._session, organization_id=organization_id, patient_number=patient_number
        )
        if hard_conflict is not None:
            message = "A patient with this patient number already exists in this organization."
            await self._workflow_service.fail_step(
                organization_id=organization_id,
                workflow_run_id=run.id,
                step_id=step1.id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=_ACTOR_IDENTIFIER,
                failure_code=_DUPLICATE_NUMBER_FAILURE_CODE,
                failure_message_safe=message,
            )
            await self._workflow_service.fail_workflow(
                organization_id=organization_id,
                workflow_run_id=run.id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=_ACTOR_IDENTIFIER,
                failure_code=_DUPLICATE_NUMBER_FAILURE_CODE,
                failure_message_safe=message,
            )
            return run

        soft_duplicate = await patient_repository.find_by_name_and_dob(
            self._session,
            organization_id=organization_id,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=date_of_birth,
        )
        if soft_duplicate is not None:
            reason = _safe_failure_message(
                "A patient matching this name and date of birth may already be "
                f"registered (existing patient number {soft_duplicate.patient_number})."
            )[:_DUPLICATE_REASON_MAX_LENGTH]
            await self._approval_service.create_approval_request(
                organization_id=organization_id,
                workflow_run_id=run.id,
                workflow_step_id=step1.id,
                approval_type=ApprovalType.CUSTOM,
                reason=reason,
                actor_identifier=actor_identifier,
                requested_by_agent=_ACTOR_IDENTIFIER,
                actor_type=ActorType.SYSTEM,
            )
            # WAITING — see ApprovalService.approve/reject for how this
            # resolves; the class docstring for the scope boundary on
            # what approving this actually does.
            return run

        await self._workflow_service.complete_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=step1.id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_ACTOR_IDENTIFIER,
            safe_metadata={"duplicate_check": "clear"},
        )

        creation_template = PATIENT_REGISTRATION_TEMPLATE.steps[1]
        step2 = await self._workflow_service.create_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            sequence_number=creation_template.sequence_number,
            step_type=creation_template.step_type,
        )
        await self._workflow_service.start_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=step2.id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_ACTOR_IDENTIFIER,
        )

        try:
            patient = await self._patient_service.create_patient(
                organization_id=organization_id,
                patient_number=patient_number,
                first_name=first_name,
                last_name=last_name,
                date_of_birth=date_of_birth,
                user_id=user_id,
            )
        except (PatientNumberConflictError, InvalidPatientLinkError) as exc:
            message = _safe_failure_message(exc.message)
            await self._workflow_service.fail_step(
                organization_id=organization_id,
                workflow_run_id=run.id,
                step_id=step2.id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=_ACTOR_IDENTIFIER,
                failure_code=exc.error_code,
                failure_message_safe=message,
            )
            await self._workflow_service.fail_workflow(
                organization_id=organization_id,
                workflow_run_id=run.id,
                actor_type=ActorType.SYSTEM,
                actor_identifier=_ACTOR_IDENTIFIER,
                failure_code=exc.error_code,
                failure_message_safe=message,
            )
            return run

        await self._workflow_service.complete_step(
            organization_id=organization_id,
            workflow_run_id=run.id,
            step_id=step2.id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_ACTOR_IDENTIFIER,
            safe_metadata={"patient_id": str(patient.id)},
        )
        await self._workflow_service.complete_workflow(
            organization_id=organization_id,
            workflow_run_id=run.id,
            actor_type=ActorType.SYSTEM,
            actor_identifier=_ACTOR_IDENTIFIER,
        )
        return run


__all__ = ["PatientRegistrationService"]
