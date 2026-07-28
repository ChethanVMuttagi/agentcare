"""`WorkflowTemplate`: a reusable, declarative description of the ordered
steps one kind of administrative workflow (`WorkflowRequestType`) goes
through.

Directly mirrors `app.ai.agents.base.AgentDefinition`/
`app.ai.tools.base.ToolDefinition` — a plain, in-memory, developer-
authored, frozen dataclass, deliberately NOT a database table (this is
static application configuration, not tenant data) and deliberately NOT
a generic step-execution engine: a template describes WHAT the ordered
steps of a workflow kind are (for introspection, validation, and to
drive step labeling), it does not itself interpret natural language or
decide anything — that remains `app.ai.orchestration.AgentOrchestrationService`'s
job for Coordinator-driven templates, and
`app.services.patient_registration.PatientRegistrationService`'s job for
the one template with no natural-language step at all. See
docs/adr/ADR-0014-end-to-end-administrative-workflows.md.

Four templates exist, one per `WorkflowRequestType` this story targets:
- Patient Registration: two DETERMINISTIC steps (duplicate check,
  record creation) — no Coordinator/specialist involvement, driven
  entirely by `PatientRegistrationService`.
- Appointment Booking / Appointment Rescheduling / Document Collection:
  two Coordinator-driven steps (coordination, specialist execution) —
  driven by `AgentOrchestrationService`, which looks up a template ONLY
  to label its coordination step consistently; the actual Coordinator/
  specialist/tool execution is unchanged from STORY-011.

`requires_approval`/`schedules_reminder` on a step are DESCRIPTIVE
metadata, not executable instructions this module runs — the real
approval-gating happens through `app.services.approval.ApprovalService`
and the real reminder-scheduling happens through
`app.services.reminder_scheduler.ReminderScheduler` (already
automatically wired into `app.services.appointment.AppointmentService` —
see docs/adr/ADR-0012-reminder-engine.md). A template does not
duplicate either; it documents that they apply, so
`WorkflowTemplateRegistry`-driven tests can prove the documented shape
matches what actually gets persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.models.approval import ApprovalType
from app.models.workflow import WorkflowRequestType

_STEP_TYPE_MAX_LENGTH = 64  # matches `WorkflowStep.step_type`'s `String(64)` column bound


@dataclass(frozen=True)
class WorkflowStepTemplate:
    """One declared step within a `WorkflowTemplate`.

    `sequence_number` must match the `WorkflowStep.sequence_number` the
    real execution actually assigns — `WorkflowTemplate.__post_init__`
    validates this template-internally (1-based, contiguous, unique);
    nothing here validates it against a REAL persisted run — that
    correspondence is what this story's tests prove.
    """

    sequence_number: int
    step_type: str
    description: str
    agent_name: str | None = None
    requires_approval: bool = False
    approval_type: ApprovalType | None = None
    schedules_reminder: bool = False

    def __post_init__(self) -> None:
        if self.sequence_number < 1:
            raise ValueError("sequence_number must be >= 1.")
        if not self.step_type or len(self.step_type) > _STEP_TYPE_MAX_LENGTH:
            raise ValueError(
                f"step_type must be non-empty and at most {_STEP_TYPE_MAX_LENGTH} characters."
            )
        if self.requires_approval and self.approval_type is None:
            raise ValueError("A step that requires approval must declare an approval_type.")


@dataclass(frozen=True)
class WorkflowTemplate:
    """One kind of end-to-end administrative workflow, as an ordered
    sequence of `WorkflowStepTemplate`s."""

    request_type: WorkflowRequestType
    name: str
    description: str
    steps: tuple[WorkflowStepTemplate, ...]

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("A workflow template must declare at least one step.")
        sequence_numbers = [step.sequence_number for step in self.steps]
        expected = list(range(1, len(self.steps) + 1))
        if sequence_numbers != expected:
            raise ValueError(
                f"Step sequence_numbers must be contiguous starting at 1; got {sequence_numbers}."
            )


PATIENT_REGISTRATION_TEMPLATE = WorkflowTemplate(
    request_type=WorkflowRequestType.PATIENT_REGISTRATION,
    name="Patient Registration",
    description=(
        "Register a new patient: check for a likely duplicate by patient number "
        "(hard conflict) or name/date-of-birth (soft match requiring human approval "
        "to proceed), then create the patient record."
    ),
    steps=(
        WorkflowStepTemplate(
            sequence_number=1,
            step_type="patient_duplicate_check",
            description=(
                "Check for a patient-number conflict (fails the workflow outright) or a "
                "name/date-of-birth soft match (pauses the workflow for human approval)."
            ),
            requires_approval=True,
            approval_type=ApprovalType.CUSTOM,
        ),
        WorkflowStepTemplate(
            sequence_number=2,
            step_type="patient_record_creation",
            description="Create the patient record once the duplicate check is clear.",
        ),
    ),
)

APPOINTMENT_BOOKING_TEMPLATE = WorkflowTemplate(
    request_type=WorkflowRequestType.APPOINTMENT_BOOKING,
    name="Appointment Booking",
    description=(
        "Coordinator hands off to the Scheduling specialist, which checks availability "
        "and books the appointment; booking automatically schedules a reminder."
    ),
    steps=(
        WorkflowStepTemplate(
            sequence_number=1,
            step_type="coordination",
            description="The Coordinator interprets the request and hands off to Scheduling.",
            agent_name="coordinator",
        ),
        WorkflowStepTemplate(
            sequence_number=2,
            step_type="specialist_execution",
            description="The Scheduling specialist books the appointment.",
            agent_name="scheduling",
            schedules_reminder=True,
        ),
    ),
)

APPOINTMENT_RESCHEDULING_TEMPLATE = WorkflowTemplate(
    request_type=WorkflowRequestType.APPOINTMENT_RESCHEDULING,
    name="Appointment Rescheduling",
    description=(
        "Coordinator hands off to the Scheduling specialist, which reschedules an "
        "existing appointment; rescheduling automatically cancels the old reminder and "
        "schedules a new one for the new time."
    ),
    steps=(
        WorkflowStepTemplate(
            sequence_number=1,
            step_type="coordination",
            description="The Coordinator interprets the request and hands off to Scheduling.",
            agent_name="coordinator",
        ),
        WorkflowStepTemplate(
            sequence_number=2,
            step_type="specialist_execution",
            description="The Scheduling specialist reschedules the appointment.",
            agent_name="scheduling",
            schedules_reminder=True,
        ),
    ),
)

DOCUMENT_COLLECTION_TEMPLATE = WorkflowTemplate(
    request_type=WorkflowRequestType.DOCUMENT_COLLECTION,
    name="Document Collection",
    description=(
        "Coordinator hands off to the Document specialist, which reports which "
        "administrative documents are on file and their status."
    ),
    steps=(
        WorkflowStepTemplate(
            sequence_number=1,
            step_type="coordination",
            description="The Coordinator interprets the request and hands off to Document.",
            agent_name="coordinator",
        ),
        WorkflowStepTemplate(
            sequence_number=2,
            step_type="specialist_execution",
            description="The Document specialist reports document status.",
            agent_name="document",
        ),
    ),
)


class WorkflowTemplateRegistry:
    """A plain-dict, in-memory registry of `WorkflowTemplate`s, keyed by
    `WorkflowRequestType` — directly mirrors `app.ai.agents.registry.AgentRegistry`/
    `app.ai.tools.registry.ToolRegistry`'s shape. `get_for_request_type`
    returns `None` (never raises) for a `WorkflowRequestType` with no
    registered template — several request types (`administrative_routing`,
    `follow_up`, `reminder_delivery`) deliberately have none; callers
    (see `app.ai.orchestration.AgentOrchestrationService`) fall back to
    their own existing default behavior in that case."""

    def __init__(self) -> None:
        self._templates: dict[WorkflowRequestType, WorkflowTemplate] = {}

    def register(self, template: WorkflowTemplate) -> None:
        self._templates[template.request_type] = template

    def get_for_request_type(self, request_type: WorkflowRequestType) -> WorkflowTemplate | None:
        return self._templates.get(request_type)

    def list_all(self) -> list[WorkflowTemplate]:
        return list(self._templates.values())


def build_default_workflow_template_registry() -> WorkflowTemplateRegistry:
    """The `WorkflowTemplateRegistry` this codebase actually uses. Adding
    a new workflow template means adding one more `registry.register(...)`
    call here."""
    registry = WorkflowTemplateRegistry()
    registry.register(PATIENT_REGISTRATION_TEMPLATE)
    registry.register(APPOINTMENT_BOOKING_TEMPLATE)
    registry.register(APPOINTMENT_RESCHEDULING_TEMPLATE)
    registry.register(DOCUMENT_COLLECTION_TEMPLATE)
    return registry


@lru_cache
def get_workflow_template_registry() -> WorkflowTemplateRegistry:
    """FastAPI-dependency-friendly, cached accessor — the registry is
    stateless and identical on every call, so it is built exactly once
    per process (mirrors `app.ai.agents.definitions.get_agent_registry`)."""
    return build_default_workflow_template_registry()
