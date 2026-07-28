"""Notification delivery abstraction (STORY-013).

See `base.py` for `NotificationProvider`/`NotificationMessage`/
`NotificationResult`, and `console.py` for the one real implementation
this story ships (`ConsoleNotificationProvider`). No email, SMS, or
WhatsApp provider exists yet — see docs/adr/ADR-0012-reminder-engine.md
"Current vs. Planned". The abstraction is deliberately provider-agnostic
so adding one later is a new adapter module, not a change to
`app.workers.reminder_worker.ReminderWorker` or
`app.services.reminder.ReminderService`.
"""
