"""`app.ai.decisions` tests — no database, no network."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.decisions import (
    ClarificationRequiredDecision,
    DecisionKind,
    RefusalCategory,
    RefusalDecision,
    SafeResponseDecision,
    ToolCallDecision,
    parse_decision,
)


def test_parses_valid_tool_call_decision() -> None:
    decision = parse_decision(
        {
            "kind": "tool_call",
            "tool_name": "book_appointment",
            "arguments": {"practitioner_id": "abc"},
        }
    )
    assert isinstance(decision, ToolCallDecision)
    assert decision.tool_name == "book_appointment"
    assert decision.arguments == {"practitioner_id": "abc"}


def test_parses_valid_clarification_decision() -> None:
    decision = parse_decision(
        {"kind": "clarification_required", "message": "Which practitioner did you mean?"}
    )
    assert isinstance(decision, ClarificationRequiredDecision)
    assert decision.message == "Which practitioner did you mean?"


def test_parses_valid_safe_response_decision() -> None:
    decision = parse_decision({"kind": "safe_response", "message": "Your appointment is booked."})
    assert isinstance(decision, SafeResponseDecision)


def test_parses_valid_refusal_decision() -> None:
    decision = parse_decision(
        {
            "kind": "refusal",
            "reason_category": "clinical_content",
            "safe_message": "Please contact your care team.",
        }
    )
    assert isinstance(decision, RefusalDecision)
    assert decision.reason_category is RefusalCategory.CLINICAL_CONTENT


def test_rejects_unknown_decision_kind() -> None:
    with pytest.raises(ValidationError):
        parse_decision({"kind": "run_sql", "query": "UPDATE users SET role='admin'"})


def test_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        parse_decision({"kind": "tool_call"})  # missing tool_name


def test_rejects_malformed_tool_call_missing_tool_name() -> None:
    with pytest.raises(ValidationError):
        parse_decision({"kind": "tool_call", "arguments": {}})


def test_rejects_empty_tool_name() -> None:
    with pytest.raises(ValidationError):
        parse_decision({"kind": "tool_call", "tool_name": "", "arguments": {}})


def test_rejects_chain_of_thought_field_on_tool_call() -> None:
    """Structural proof of "no chain-of-thought": an otherwise-valid
    decision carrying a smuggled reasoning field is REJECTED entirely,
    not silently stripped."""
    with pytest.raises(ValidationError):
        parse_decision(
            {
                "kind": "tool_call",
                "tool_name": "book_appointment",
                "arguments": {},
                "chain_of_thought": "First I considered the patient's history...",
            }
        )


def test_rejects_reasoning_field_on_safe_response() -> None:
    with pytest.raises(ValidationError):
        parse_decision(
            {
                "kind": "safe_response",
                "message": "Done.",
                "reasoning": "I inferred this from context.",
            }
        )


def test_rejects_scratchpad_field_on_refusal() -> None:
    with pytest.raises(ValidationError):
        parse_decision(
            {
                "kind": "refusal",
                "reason_category": "clinical_content",
                "safe_message": "Please contact your care team.",
                "scratchpad": "internal notes",
            }
        )


def test_rejects_internal_reasoning_field_on_clarification() -> None:
    with pytest.raises(ValidationError):
        parse_decision(
            {
                "kind": "clarification_required",
                "message": "Which department?",
                "internal_reasoning": "The user seems confused",
            }
        )


def test_rejects_unknown_refusal_category() -> None:
    with pytest.raises(ValidationError):
        parse_decision(
            {"kind": "refusal", "reason_category": "bogus_category", "safe_message": "No."}
        )


def test_rejects_oversized_tool_name() -> None:
    with pytest.raises(ValidationError):
        parse_decision({"kind": "tool_call", "tool_name": "x" * 200, "arguments": {}})


def test_rejects_too_many_argument_keys() -> None:
    with pytest.raises(ValidationError):
        parse_decision(
            {
                "kind": "tool_call",
                "tool_name": "book_appointment",
                "arguments": {f"key_{i}": i for i in range(50)},
            }
        )


def test_decision_kind_values_are_stable_strings() -> None:
    assert DecisionKind.TOOL_CALL.value == "tool_call"
    assert DecisionKind.CLARIFICATION_REQUIRED.value == "clarification_required"
    assert DecisionKind.SAFE_RESPONSE.value == "safe_response"
    assert DecisionKind.REFUSAL.value == "refusal"


def test_top_level_not_a_dict_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_decision("just a string")  # type: ignore[arg-type]
