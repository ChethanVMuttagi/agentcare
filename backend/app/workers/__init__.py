"""Durable background workers (STORY-013).

See `reminder_worker.py` for `ReminderWorker` — the only worker this
story implements. No in-memory queue, no background thread that could
lose work: every unit of work is a `Reminder` row in PostgreSQL, claimed
via `SELECT ... FOR UPDATE SKIP LOCKED` (see
`app.repositories.reminder.acquire_pending`), so a worker process
restarting (or crashing mid-attempt) never loses or duplicates durable
state — see docs/adr/ADR-0012-reminder-engine.md.
"""
