"""`ConsoleNotificationProvider`: the one real `NotificationProvider`
implemented in STORY-013 — writes a safe, structured line to the
application logger instead of actually delivering anything.

This is deliberately a development/demo stand-in, not a production
delivery channel — see docs/adr/ADR-0012-reminder-engine.md "Current vs.
Planned". It exists so the reminder engine's full pipeline (schedule ->
acquire -> send -> mark sent/failed -> workflow audit trail) is
genuinely, observably exercised end-to-end without requiring a real
email/SMS/WhatsApp integration and its associated secrets. Logs via
`logging`, not `print()`, so its output is captured the same way every
other structured log line in this codebase is (see
`app.core.logging.configure_logging`) — never a bespoke, uncaptured
stdout write.
"""

from __future__ import annotations

import logging

from app.notifications.base import NotificationMessage, NotificationResult

logger = logging.getLogger("agentcare.notifications.console")

_PROVIDER_NAME = "console"


class ConsoleNotificationProvider:
    """Always succeeds — there is no real delivery channel to fail.
    Logs only the safe fields `NotificationMessage` carries; see that
    class's docstring for why nothing PHI-shaped is ever available to
    log here in the first place."""

    @property
    def provider_name(self) -> str:
        return _PROVIDER_NAME

    async def send(self, message: NotificationMessage) -> NotificationResult:
        logger.info(
            "reminder notification (console): reminder_id=%s organization_id=%s "
            "patient_id=%s appointment_id=%s reminder_type=%s appointment_start_at=%s",
            message.reminder_id,
            message.organization_id,
            message.patient_id,
            message.appointment_id,
            message.reminder_type.value,
            message.appointment_start_at.isoformat(),
        )
        return NotificationResult(success=True, provider_name=_PROVIDER_NAME)
