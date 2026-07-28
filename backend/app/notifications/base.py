"""`NotificationProvider`: the provider-independent interface every
notification channel adapter implements (STORY-013).

Mirrors `app.ai.providers.base.LLMProvider`'s shape deliberately: a
small `Protocol`, one real implementation for now
(`app.notifications.console.ConsoleNotificationProvider`), a
deterministic fake for tests
(`app.notifications.fake.FakeNotificationProvider`), and nothing outside
this package ever needs to know which concrete provider is configured.

`NotificationMessage` is built entirely from SAFE, already-non-sensitive
fields — internal UUIDs and a timestamp — never a patient's name,
contact details (no email/phone exists on `Patient` in this codebase
yet), or free-form clinical text. This is a deliberate scope choice, not
an oversight: it means a `NotificationProvider` implementation can never
leak PHI through its input even if a future implementation logged its
argument verbatim. See docs/adr/ADR-0012-reminder-engine.md "Notification
Content".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models.reminder import ReminderType


@dataclass(frozen=True)
class NotificationMessage:
    """Everything a `NotificationProvider` needs to deliver one
    reminder. Every field is a safe internal identifier or a timestamp —
    see the module docstring."""

    reminder_id: uuid.UUID
    organization_id: uuid.UUID
    patient_id: uuid.UUID
    appointment_id: uuid.UUID
    reminder_type: ReminderType
    appointment_start_at: datetime


@dataclass(frozen=True)
class NotificationResult:
    """The outcome of one delivery attempt — structured and safe to
    both persist (in `ReminderAttempt.safe_error_message`) and log.

    `safe_detail`, when present on a failure, must be a short, bounded,
    already-safe description (e.g. `"provider unavailable"`) — NEVER a
    raw exception message, stack trace, or connection detail. Mirrors
    `app.ai.tools.base.ToolResult`'s identical discipline.
    """

    success: bool
    provider_name: str
    safe_detail: str | None = None


class NotificationProvider(Protocol):
    """Provider-independent notification-delivery interface.

    Implementations MUST NOT raise for an ordinary delivery failure —
    return `NotificationResult(success=False, ...)` instead, exactly
    like `app.ai.tools.base.ToolResult` never raises for a normal tool
    failure. An implementation MAY still raise for a genuinely
    unexpected/programming-error condition; callers
    (`app.workers.reminder_worker.ReminderWorker`) treat any raised
    exception as a failed attempt too, as a last-resort safety net —
    never let one bad reminder crash the worker's poll loop.
    """

    @property
    def provider_name(self) -> str:
        """A short, stable, safe identifier for this provider (e.g.
        `"console"`) — persisted in `ReminderAttempt.provider_name`."""
        ...

    async def send(self, message: NotificationMessage) -> NotificationResult:
        """Attempt to deliver `message`. Never raises for an ordinary
        delivery failure — see the class docstring."""
        ...
