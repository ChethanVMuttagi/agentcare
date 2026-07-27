"""`SafetyPolicy`: a deterministic, code-level healthcare-safety
boundary.

This is NOT the only safety mechanism (see docs/AI_SAFETY.md
"Prompt Injection Defense" for the full layered model) — it is the ONE
layer that runs BEFORE the model is ever called, so a clinical/
symptom-based request is refused deterministically regardless of what
any provider (real or fake, cooperative or successfully manipulated)
would have said about it. `app.ai.orchestration.AgentOrchestrationService`
calls `screen_request_text` first, and short-circuits to a `REFUSAL`
decision without ever invoking the LLM if it trips.

**Explicit limitation**: this is keyword/phrase-pattern matching, not
real natural-language understanding. It is deliberately biased toward
over-refusing rather than under-refusing — a false positive (an
administrative request incorrectly flagged) costs the caller a
clarification round-trip; a false negative (clinical content that slips
through) would cross the healthcare safety boundary this project treats
as non-negotiable. See docs/AI_SAFETY.md "Limitations" for what this
does and does not guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.ai.decisions import AdministrativeDecision, DecisionKind


class SafetyCategory(StrEnum):
    """Why a request was flagged — mirrors `RefusalCategory` but is kept
    as its own type since `SafetyPolicy` is a lower-level module than
    `app.ai.decisions` and should not need every future refusal reason a
    decision might carry."""

    SYMPTOM_BASED_ROUTING = "symptom_based_routing"
    MEDICATION_OR_DOSAGE = "medication_or_dosage"
    DIAGNOSIS_REQUEST = "diagnosis_request"


@dataclass(frozen=True)
class SafetyViolation:
    category: SafetyCategory
    safe_message: str


# Deliberately short, administrative-safety-focused phrase lists — not an
# attempt at general medical NLP. Matched case-insensitively against
# word boundaries where practical, to reduce (not eliminate) false
# positives like a department literally named "Family Medicine".
_SYMPTOM_PATTERNS = (
    r"\bi have (a |an )?(chest pain|headache|fever|rash|cough|nausea|dizziness)\b",
    r"\bi feel (dizzy|nauseous|sick|faint)\b",
    r"\bi('m| am) (having|experiencing) (chest pain|shortness of breath|a headache)\b",
    r"\bchest pain\b",
    r"\bshortness of breath\b",
    r"\bwhich department (should|do) i\b",
    r"\bwhat('s| is) wrong with me\b",
    r"\bdo i have\b.*\b(cancer|diabetes|covid|flu|infection)\b",
    r"\bam i having a heart attack\b",
    r"\bis this serious\b",
    r"\bwhat disease\b",
    r"\bwhat condition\b",
)

_DIAGNOSIS_PATTERNS = (
    r"\bdiagnos(e|is|ing)\b",
    r"\bwhat('s| is) causing (this|my)\b",
    r"\binterpret (my|these|this) (test|lab) results?\b",
)

_MEDICATION_PATTERNS = (
    r"\b\d+\s*(mg|milligram|mcg|microgram)s?\b",
    r"\bdosage\b",
    r"\bhow much (medication|medicine|drug)\b",
    r"\bprescri(be|ption)\b",
    r"\bwhat medication\b",
    r"\bshould i take\b",
    r"\bincrease my dose\b",
    r"\bstop taking\b.*\bmedication\b",
)

_SAFE_ESCALATION_MESSAGE = (
    "This looks like it may involve a medical symptom or condition. I'm not able "
    "to help with that — please contact your care team directly, or seek "
    "emergency care if this is urgent."
)
_SAFE_MEDICATION_MESSAGE = (
    "I'm not able to help with medication, dosage, or prescription questions — "
    "please contact your care team or pharmacist directly."
)
_SAFE_DIAGNOSIS_MESSAGE = (
    "I'm not able to interpret symptoms, test results, or provide a diagnosis — "
    "please contact your care team directly."
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


class SafetyPolicy:
    """Stateless; safe to share a single instance across requests."""

    def screen_request_text(self, request_text: str) -> SafetyViolation | None:
        """Pre-LLM screen. Returns a `SafetyViolation` if the request
        text itself appears to describe symptoms, ask for a diagnosis,
        or ask about medication/dosage — `None` if it looks
        administrative. Called BEFORE the LLM is ever invoked; a
        violation here means the LLM is never called at all for this
        request."""
        if _matches_any(request_text, _MEDICATION_PATTERNS):
            return SafetyViolation(
                category=SafetyCategory.MEDICATION_OR_DOSAGE,
                safe_message=_SAFE_MEDICATION_MESSAGE,
            )
        if _matches_any(request_text, _DIAGNOSIS_PATTERNS):
            return SafetyViolation(
                category=SafetyCategory.DIAGNOSIS_REQUEST,
                safe_message=_SAFE_DIAGNOSIS_MESSAGE,
            )
        if _matches_any(request_text, _SYMPTOM_PATTERNS):
            return SafetyViolation(
                category=SafetyCategory.SYMPTOM_BASED_ROUTING,
                safe_message=_SAFE_ESCALATION_MESSAGE,
            )
        return None

    def screen_decision(self, decision: AdministrativeDecision) -> SafetyViolation | None:
        """Post-LLM, second-layer screen — defense-in-depth in case a
        decision's own content (e.g. a `safe_response`/`clarification`
        message the model composed) drifts into clinical territory even
        when the ORIGINAL request text didn't trip
        `screen_request_text`. Tool calls are not text-screened here —
        their safety is enforced structurally by the `ToolRegistry`
        allowlist (no clinical tool exists to call) and by each tool's
        own bounded argument schema, not by scanning argument values."""
        if decision.kind in (DecisionKind.CLARIFICATION_REQUIRED, DecisionKind.SAFE_RESPONSE):
            message = decision.message
            violation = self.screen_request_text(message)
            if violation is not None:
                return violation
        return None
