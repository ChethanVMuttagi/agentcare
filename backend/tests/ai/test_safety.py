"""`app.ai.safety.SafetyPolicy` tests — no database, no network."""

from __future__ import annotations

from app.ai.decisions import (
    ClarificationRequiredDecision,
    RefusalCategory,
    RefusalDecision,
    ToolCallDecision,
)
from app.ai.safety import SafetyCategory, SafetyPolicy

_policy = SafetyPolicy()


def test_administrative_booking_request_passes_screen() -> None:
    assert _policy.screen_request_text("Book a Cardiology follow-up for next Tuesday") is None


def test_explicit_department_reschedule_passes_screen() -> None:
    assert _policy.screen_request_text("Please reschedule my appointment to Friday") is None


def test_symptom_based_department_routing_is_rejected() -> None:
    """The mandatory example case: symptom description asking the model
    to pick a department must be refused, never autonomously routed."""
    violation = _policy.screen_request_text(
        "I have chest pain, which department should I see?"
    )
    assert violation is not None
    assert violation.category is SafetyCategory.SYMPTOM_BASED_ROUTING


def test_symptom_description_alone_is_rejected() -> None:
    violation = _policy.screen_request_text("I have a headache and I feel dizzy")
    assert violation is not None
    assert violation.category is SafetyCategory.SYMPTOM_BASED_ROUTING


def test_diagnosis_request_is_rejected() -> None:
    violation = _policy.screen_request_text("Can you diagnose what's wrong with me?")
    assert violation is not None
    assert violation.category is SafetyCategory.DIAGNOSIS_REQUEST


def test_medication_dosage_request_is_rejected() -> None:
    violation = _policy.screen_request_text("Should I take 500 mg of my medication today?")
    assert violation is not None
    assert violation.category is SafetyCategory.MEDICATION_OR_DOSAGE


def test_prescription_request_is_rejected() -> None:
    violation = _policy.screen_request_text("Can you prescribe something for my pain?")
    assert violation is not None


def test_dosage_change_request_is_rejected() -> None:
    violation = _policy.screen_request_text("I want to increase my dose")
    assert violation is not None


def test_am_i_having_a_heart_attack_is_rejected() -> None:
    violation = _policy.screen_request_text("Am I having a heart attack?")
    assert violation is not None
    assert violation.category is SafetyCategory.SYMPTOM_BASED_ROUTING


def test_document_collection_request_passes_screen() -> None:
    assert _policy.screen_request_text("I need to submit my insurance document") is None


def test_availability_check_request_passes_screen() -> None:
    assert (
        _policy.screen_request_text("What times is Dr. Smith available next week?") is None
    )


def test_safe_message_never_contains_original_request_text() -> None:
    request_text = "I have chest pain and I am worried"
    violation = _policy.screen_request_text(request_text)
    assert violation is not None
    assert request_text not in violation.safe_message


def test_screen_decision_passes_safe_clarification() -> None:
    decision = ClarificationRequiredDecision(message="Which practitioner would you like to see?")
    assert _policy.screen_decision(decision) is None


def test_screen_decision_flags_clinical_content_in_message() -> None:
    decision = ClarificationRequiredDecision(message="Do you have chest pain right now?")
    violation = _policy.screen_decision(decision)
    assert violation is not None


def test_screen_decision_does_not_screen_tool_calls() -> None:
    """Tool call safety is enforced structurally (registry allowlist +
    per-tool schemas), not by scanning argument values as text — see
    the module docstring."""
    decision = ToolCallDecision(tool_name="book_appointment", arguments={"note": "chest pain"})
    assert _policy.screen_decision(decision) is None


def test_refusal_decision_message_is_safe_and_bounded() -> None:
    violation = _policy.screen_request_text("I have chest pain, which department should I see?")
    assert violation is not None
    refusal = RefusalDecision(
        reason_category=RefusalCategory.CLINICAL_CONTENT, safe_message=violation.safe_message
    )
    assert len(refusal.safe_message) <= 1000
    assert "chest pain" not in refusal.safe_message
