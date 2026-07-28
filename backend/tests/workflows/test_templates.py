"""`app.workflows.templates` unit tests — pure in-memory validation, no
database required (mirrors `tests/ai/test_agents.py`/`tests/ai/test_tools.py`'s
"plain registry, no DB" test style)."""

from __future__ import annotations

import pytest

from app.models.approval import ApprovalType
from app.models.workflow import WorkflowRequestType
from app.workflows.templates import (
    APPOINTMENT_BOOKING_TEMPLATE,
    APPOINTMENT_RESCHEDULING_TEMPLATE,
    DOCUMENT_COLLECTION_TEMPLATE,
    PATIENT_REGISTRATION_TEMPLATE,
    WorkflowStepTemplate,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
    build_default_workflow_template_registry,
    get_workflow_template_registry,
)

# --- WorkflowStepTemplate validation ---


def test_step_template_rejects_sequence_number_below_one() -> None:
    with pytest.raises(ValueError, match="sequence_number"):
        WorkflowStepTemplate(sequence_number=0, step_type="x", description="d")


def test_step_template_rejects_empty_step_type() -> None:
    with pytest.raises(ValueError, match="step_type"):
        WorkflowStepTemplate(sequence_number=1, step_type="", description="d")


def test_step_template_rejects_oversized_step_type() -> None:
    with pytest.raises(ValueError, match="step_type"):
        WorkflowStepTemplate(sequence_number=1, step_type="x" * 65, description="d")


def test_step_template_accepts_step_type_at_max_length() -> None:
    step = WorkflowStepTemplate(sequence_number=1, step_type="x" * 64, description="d")
    assert step.step_type == "x" * 64


def test_step_template_requires_approval_type_when_requires_approval() -> None:
    with pytest.raises(ValueError, match="approval_type"):
        WorkflowStepTemplate(
            sequence_number=1, step_type="x", description="d", requires_approval=True
        )


def test_step_template_allows_requires_approval_with_type() -> None:
    step = WorkflowStepTemplate(
        sequence_number=1,
        step_type="x",
        description="d",
        requires_approval=True,
        approval_type=ApprovalType.CUSTOM,
    )
    assert step.requires_approval is True
    assert step.approval_type is ApprovalType.CUSTOM


# --- WorkflowTemplate validation ---


def test_template_rejects_empty_steps() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        WorkflowTemplate(
            request_type=WorkflowRequestType.FOLLOW_UP, name="n", description="d", steps=()
        )


def test_template_rejects_non_contiguous_sequence_numbers() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        WorkflowTemplate(
            request_type=WorkflowRequestType.FOLLOW_UP,
            name="n",
            description="d",
            steps=(
                WorkflowStepTemplate(sequence_number=1, step_type="a", description="d"),
                WorkflowStepTemplate(sequence_number=3, step_type="b", description="d"),
            ),
        )


def test_template_rejects_sequence_not_starting_at_one() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        WorkflowTemplate(
            request_type=WorkflowRequestType.FOLLOW_UP,
            name="n",
            description="d",
            steps=(WorkflowStepTemplate(sequence_number=2, step_type="a", description="d"),),
        )


def test_template_accepts_valid_contiguous_steps() -> None:
    template = WorkflowTemplate(
        request_type=WorkflowRequestType.FOLLOW_UP,
        name="n",
        description="d",
        steps=(
            WorkflowStepTemplate(sequence_number=1, step_type="a", description="d"),
            WorkflowStepTemplate(sequence_number=2, step_type="b", description="d"),
        ),
    )
    assert len(template.steps) == 2


# --- The four concrete templates ---


def test_patient_registration_template_shape() -> None:
    assert PATIENT_REGISTRATION_TEMPLATE.request_type is WorkflowRequestType.PATIENT_REGISTRATION
    assert len(PATIENT_REGISTRATION_TEMPLATE.steps) == 2
    step1, step2 = PATIENT_REGISTRATION_TEMPLATE.steps
    assert step1.step_type == "patient_duplicate_check"
    assert step1.requires_approval is True
    assert step1.approval_type is ApprovalType.CUSTOM
    assert step2.step_type == "patient_record_creation"
    assert step2.requires_approval is False
    assert step2.schedules_reminder is False


def test_appointment_booking_template_shape() -> None:
    assert APPOINTMENT_BOOKING_TEMPLATE.request_type is WorkflowRequestType.APPOINTMENT_BOOKING
    step1, step2 = APPOINTMENT_BOOKING_TEMPLATE.steps
    assert step1.step_type == "coordination"
    assert step1.agent_name == "coordinator"
    assert step2.step_type == "specialist_execution"
    assert step2.agent_name == "scheduling"
    assert step2.schedules_reminder is True
    assert step2.requires_approval is False


def test_appointment_rescheduling_template_shape() -> None:
    assert (
        APPOINTMENT_RESCHEDULING_TEMPLATE.request_type
        is WorkflowRequestType.APPOINTMENT_RESCHEDULING
    )
    step1, step2 = APPOINTMENT_RESCHEDULING_TEMPLATE.steps
    assert step1.agent_name == "coordinator"
    assert step2.agent_name == "scheduling"
    assert step2.schedules_reminder is True


def test_document_collection_template_shape() -> None:
    assert DOCUMENT_COLLECTION_TEMPLATE.request_type is WorkflowRequestType.DOCUMENT_COLLECTION
    step1, step2 = DOCUMENT_COLLECTION_TEMPLATE.steps
    assert step1.agent_name == "coordinator"
    assert step2.agent_name == "document"
    assert step2.schedules_reminder is False
    assert step2.requires_approval is False


# --- WorkflowTemplateRegistry ---


def test_registry_get_for_request_type_returns_registered_template() -> None:
    registry = WorkflowTemplateRegistry()
    registry.register(PATIENT_REGISTRATION_TEMPLATE)
    assert (
        registry.get_for_request_type(WorkflowRequestType.PATIENT_REGISTRATION)
        is PATIENT_REGISTRATION_TEMPLATE
    )


def test_registry_get_for_request_type_returns_none_when_unregistered() -> None:
    registry = WorkflowTemplateRegistry()
    assert registry.get_for_request_type(WorkflowRequestType.ADMINISTRATIVE_ROUTING) is None


def test_default_registry_has_all_four_templates() -> None:
    registry = build_default_workflow_template_registry()
    assert registry.get_for_request_type(WorkflowRequestType.PATIENT_REGISTRATION) is not None
    assert registry.get_for_request_type(WorkflowRequestType.APPOINTMENT_BOOKING) is not None
    assert registry.get_for_request_type(WorkflowRequestType.APPOINTMENT_RESCHEDULING) is not None
    assert registry.get_for_request_type(WorkflowRequestType.DOCUMENT_COLLECTION) is not None
    assert len(registry.list_all()) == 4


def test_default_registry_has_no_template_for_untargeted_request_types() -> None:
    registry = build_default_workflow_template_registry()
    assert registry.get_for_request_type(WorkflowRequestType.ADMINISTRATIVE_ROUTING) is None
    assert registry.get_for_request_type(WorkflowRequestType.FOLLOW_UP) is None
    assert registry.get_for_request_type(WorkflowRequestType.REMINDER_DELIVERY) is None


def test_cached_registry_accessor_returns_same_instance() -> None:
    assert get_workflow_template_registry() is get_workflow_template_registry()
