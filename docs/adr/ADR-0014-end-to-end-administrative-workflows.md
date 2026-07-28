# ADR-0014: End-to-End Administrative Workflow Templates

Status: Accepted

Date: 2026-07-28

## Context

STORIES 009–014 built six genuinely durable, well-tested primitives — the
Workflow Engine, Multi-Agent Coordination, Safe Tool Calling, the Reminder
Engine, and the Human-in-the-Loop Approval Engine — but each STORY-011
Coordinator request is still exactly one round trip: interpret, decide,
optionally hand off, optionally call one tool, done. Nothing yet combines
these primitives into a realistic, MULTI-STEP administrative process, and
two real gaps blocked doing so honestly: a Coordinator clarification
COMPLETED the workflow run rather than pausing it (so a natural
follow-up message had no run to continue), and there was no workflow
kind at all for the single most common front-door administrative action
— registering a new patient.

## Decision

1. **`WorkflowTemplate`/`WorkflowStepTemplate`** (`app.workflows.templates`)
   are plain, frozen, in-memory dataclasses — directly mirroring
   `AgentDefinition`/`ToolDefinition`'s "developer-authored configuration,
   not a database table" shape. A template is DECLARATIVE: it documents
   the ordered steps a workflow kind goes through (`step_type`,
   `agent_name`, whether a step requires approval or schedules a
   reminder) so that shape is inspectable and testable in one place,
   never scattered across services as implicit knowledge. It does NOT
   become a generic step-execution engine — see Decision 4.
2. **Four templates**, one per `WorkflowRequestType` this story targets:
   Patient Registration (new), Appointment Booking, Appointment
   Rescheduling, and Document Collection (all three reusing STORY-011's
   existing Coordinator -> specialist -> tool flow, now labeled from the
   template instead of a bare hardcoded constant).
3. **A new `WorkflowRequestType.PATIENT_REGISTRATION`** and
   `app.services.patient_registration.PatientRegistrationService` — the
   ONE template with no natural-language step at all. Registering a
   patient from known, structured fields needs no model interpretation,
   so this service never touches the Coordinator; it drives
   `WorkflowService`/`ApprovalService`/`PatientService` directly, exactly
   like `ReminderService` already drives the reminder-delivery workflow
   kind without any model involvement. Its two steps:
   `patient_duplicate_check` (a hard `patient_number` conflict fails the
   workflow; a soft name/date-of-birth match pauses it for approval) and
   `patient_record_creation`.
4. **Templates describe, they do not execute.** `WorkflowTemplateService`
   as a generic multi-step interpreter (reading a template and
   dynamically driving Coordinator/tool/approval calls for ANY workflow
   kind) was considered and rejected — see Alternatives. Each of the
   four templates' actual execution stays wherever it already correctly
   lived: `AgentOrchestrationService` for the three Coordinator-driven
   ones, `PatientRegistrationService` for the deterministic one.
5. **A Coordinator clarification now PAUSES the run** (`WorkflowStatus.WAITING`,
   via the EXISTING `mark_step_waiting`/`mark_waiting` primitives — no
   new state) instead of completing it. A follow-up request carrying
   `workflow_run_id` (new, optional, on `AgentExecuteRequest`) RESUMES
   that exact run and re-enters the Coordinator with the new text —
   `AgentOrchestrationService.execute_administrative_request` was
   refactored to extract the shared "already-`RUNNING`-coordination-step
   onward" logic (`_run_coordinator_turn`) so both the fresh-run path and
   the resume path drive the identical decision logic.
6. **Resuming is disambiguated from approval-pausing.** `WorkflowStatus.WAITING`
   is shared by two unrelated reasons (a clarification pause, STORY-015;
   an approval pause, STORY-014) — resuming via `workflow_run_id` checks
   for a `PENDING` `ApprovalRequest` on the run first
   (`ApprovalService.get_pending_for_run`, new) and rejects the attempt
   with a clear `WorkflowConflictError` (409) if one exists, directing
   the caller to the approvals API instead of silently misinterpreting a
   follow-up message as an approval decision.
7. **Two new read-only inspection routes, on the EXISTING `/workflows`
   router** — `GET .../workflows/{id}/timeline` (steps and events
   merged, chronologically, so a caller doesn't need a second request
   and a client-side join) and, the one necessary mutating exception,
   `POST .../workflows/patient-registrations` (there is no other way to
   trigger a workflow kind that never goes through the Coordinator). No
   new router, no new URL prefix — seven prior stories already
   established `/organizations/{organization_id}/workflows`, and this
   story's own instruction is explicit: do not introduce a parallel
   architecture.
8. **`reschedule_appointment` is a real, new tool** (`app.ai.tools.appointment_tools`),
   mirroring `book_appointment` exactly and added to the Scheduling
   agent's allowlist. Without it, "Appointment Rescheduling" as a
   Coordinator-driven template would have nothing for the Scheduling
   specialist to actually call — STORY-010 deliberately deferred it
   ("depth over breadth"); this story is exactly the one that needed it
   for real, not just as a label.

## A Registry Gap Found and Fixed

Adding `reschedule_appointment` to `SCHEDULING_AGENT.allowed_tools`
without ALSO registering it in
`app.ai.tools.registry_builder.build_full_tool_registry` — the SEPARATE
registry the real application actually wires to `AgentOrchestrationService`
(kept deliberately apart from `appointment_tools.build_default_registry`,
whose narrower contents several STORY-010 tests assert exactly) — would
have made the tool pass its per-agent allowlist check and then fail with
`unknown_tool` the moment it was actually invoked: allowed but
unreachable. The end-to-end rescheduling test caught this immediately;
fixed by registering it in both places, with a regression test
(`test_full_tool_registry_includes_reschedule_appointment`) asserting
the PRODUCTION registry specifically, not just the narrower one.

## Rationale

- **Declarative templates, not a generic engine**: STORY-011's
  Coordinator/specialist/tool machinery is already correct, tested, and
  the ONE place natural-language requests get interpreted. A second,
  template-driven execution path would either duplicate that machinery
  or subordinate it to a new abstraction — both are the "parallel
  architecture" this story was explicitly told not to build. A
  declarative template that LABELS and DOCUMENTS the existing flow
  captures the real value (an inspectable, testable specification) at a
  fraction of the risk.
- **Patient Registration bypasses the Coordinator entirely**: the
  Coordinator's job is resolving AMBIGUITY in natural language.
  "Register a patient with these exact fields" has none — routing it
  through an LLM would add cost, latency, and a new failure mode for zero
  benefit, the same reasoning that already keeps `ReminderService`
  model-free.
- **Clarification pausing, not completing**: STORY-011 modeled a
  clarification as terminal because nothing yet needed it to be
  anything else. Once a real conversational flow needs a follow-up, a
  clarification is obviously a PAUSE, not an ending — and the primitive
  to express that (`WorkflowStatus.WAITING`) already existed, unused for
  this purpose, since STORY-009.
- **Reusing the `/workflows` router for the new routes**: introducing a
  `/workflow-runs` prefix (as this story's own illustrative endpoint
  examples name them) alongside the existing `/workflows` prefix would
  create two URL surfaces for the same resource — confusing for API
  consumers and a direct violation of "do not introduce a parallel
  architecture," read at the API-surface level as much as the code
  level.

## Alternatives Considered

- **A generic `WorkflowTemplateService` that dynamically executes ANY
  template's steps**: rejected — see Decision 4 and Rationale. Revisit
  only if a FIFTH template genuinely cannot be expressed as either
  "Coordinator-driven" or "deterministic service-driven" — no such case
  exists yet.
- **A new `/workflow-runs` API prefix matching this story's illustrative
  route names literally**: rejected — see Rationale; the existing
  `/workflows` prefix already names the same resource.
- **Making `AgentExecuteRequest.request_type` optional when resuming**:
  considered, rejected as unnecessary complexity — the field is simply
  ignored on the resume path (the run already has one), and keeping it
  required avoids widening the schema's validation surface for a
  cosmetic gain.
- **Approving a duplicate-check automatically creating the second
  patient record**: rejected — see
  [ADR-0013](ADR-0013-human-in-the-loop-approvals.md)'s already-
  established scope boundary; applied here rather than re-litigated.

## Consequences

- A future FIFTH workflow template reuses this exact split: if it's
  natural-language-driven, it's a `WorkflowTemplate` entry plus (at
  most) a new specialist/tool pair; if it's deterministic, it's a new
  service mirroring `PatientRegistrationService`'s shape.
- Specialist-level clarification (a specialist itself asking a follow-up
  question, as opposed to the Coordinator) still completes the run
  immediately, unchanged from STORY-011 — extending pause/resume to that
  level is a deliberate, explicit scope boundary for a future story, not
  an oversight.
- Any future tool addition MUST be registered in BOTH the narrow,
  per-module registry AND `build_full_tool_registry` — see "A Registry
  Gap Found and Fixed"; a regression test now guards this specific
  tool, and the same two-registration discipline applies to the next
  one.
