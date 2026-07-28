# ADR-0012: Reminder Engine

Status: Accepted

Date: 2026-07-28

## Context

Every story through STORY-011 was synchronous request/response: a human
or agent action produces a result within the same HTTP call, and nothing
in the system acts on its own after that call returns. STORY-013 is the
first to require genuine background processing — a reminder must fire
at a FUTURE time, with no request in flight to drive it — and the
requirement is explicit that this cannot be built with an in-memory
queue or a background thread that loses work on a crash or restart:
every unit of work must be durable, restart-safe, and safe under
multiple concurrent workers (no duplicate sends, no lost reminders, no
stale locks left stuck forever). This is also the first story where an
EXTERNAL side effect (notifying a patient) is combined with a database
transaction — a combination that cannot be made perfectly atomic without
infrastructure (a transactional outbox) this story's scope does not
justify, so the tradeoff has to be made explicit rather than assumed
away.

## Decision

1. **PostgreSQL IS the queue.** A `reminders` table (`Reminder` /
   `ReminderStatus` / `ReminderType`), claimed via `SELECT ... FOR
   UPDATE SKIP LOCKED` (`app.repositories.reminder.acquire_pending`) —
   the standard, proven job-queue pattern. No Celery, no Redis, no
   external broker. A worker process restarting loses nothing: every
   claimed-but-incomplete reminder is either picked up again immediately
   or recovered once its lock goes stale.
2. **Stale-lock recovery is part of the SAME claim query**, not a
   separate sweep: `acquire_pending` claims a `PENDING` reminder that is
   due, OR a `PROCESSING` reminder whose `locked_at` is older than a
   configurable timeout (a worker that crashed mid-attempt). One query,
   one code path, no separate "reaper" process to keep correct.
3. **Every reminder owns its own `WorkflowRun`/`WorkflowStep`**
   (`request_type=reminder_delivery`), created by
   `ReminderService.schedule_reminder` — never optional, never a
   parallel audit trail. Five new `WorkflowEventType` values
   (`reminder_scheduled`/`started`/`sent`/`failed`/`cancelled`) reuse
   the EXACT persistence/audit machinery STORY-009/010/011 already
   built and proved, rather than inventing a second one.
4. **A dedicated `ReminderService` state machine**
   (`_ALLOWED_REMINDER_TRANSITIONS`), mirroring
   `WorkflowService`'s `_ALLOWED_RUN_TRANSITIONS` exactly — the
   repository never decides whether a transition is legal.
   `ReminderScheduler` is kept SEPARATE from `ReminderService`: the
   scheduler owns the POLICY ("24 hours before an appointment"); the
   service owns the STATE MACHINE and persistence, reusable regardless
   of why a reminder exists.
5. **`AppointmentService` gains an OPTIONAL `initiated_by_user_id`** on
   `book_appointment`/`reschedule_appointment`/`cancel_appointment`.
   Omitted (every pre-STORY-013 call site and test): behavior is
   byte-for-byte unchanged. Supplied (the real API routes and the
   Scheduling agent's `book_appointment` tool): the appointment
   mutation's own commit is followed by a SEPARATE call into
   `ReminderScheduler`, wrapped so that a reminder-scheduling failure is
   logged and swallowed, NEVER allowed to make an already-genuinely-
   successful booking look like it failed.
6. **`NotificationProvider` is a small `Protocol`**
   (`app.notifications.base`), mirroring `app.ai.providers.base.LLMProvider`'s
   shape. `ConsoleNotificationProvider` is the one real implementation
   this story ships — it logs a structured line instead of delivering
   anything. `NotificationMessage` carries ONLY safe internal
   identifiers and a timestamp — no patient name, no contact detail, no
   PHI — so no future provider implementation can leak PHI through this
   interface even by accident.
7. **`ReminderWorker.run_once()` claims a batch, then processes each
   claimed reminder in ITS OWN session/transaction** — one reminder's
   failure (or even an unexpected exception from the notification
   provider) can never corrupt or block another's processing.
8. **An explicit, operator-authorized retry after exhaustion
   (`ReminderService.retry_failed`) creates a NEW `WorkflowRun`**,
   reassigning `Reminder.workflow_run_id`/`workflow_step_id` to it. The
   ORIGINAL run stays `FAILED` (a `WorkflowStatus` with zero allowed
   outgoing transitions) forever, as an honest historical record —
   never resurrected.

## Rationale

- **`SELECT ... FOR UPDATE SKIP LOCKED` over a message broker**: this
  story's queue semantics (claim-once, retry-with-backoff-free polling,
  a single durable source of truth already required for the audit
  trail) are exactly what this PostgreSQL pattern provides, with zero
  new infrastructure, zero new failure modes to reason about (a broker
  going down independently of the database), and zero new secrets to
  manage. A broker becomes worth its complexity at a scale/throughput
  this story does not need to assume.
- **Stale-lock recovery folded into `acquire_pending`, not a separate
  sweep job**: a separate "find and reset stuck reminders" process would
  itself need to be durable, restart-safe, and race-safe against the
  very workers it's trying to recover — i.e., it would need the exact
  same `SKIP LOCKED` claim logic again. Recognizing that the "claim a
  stale lock" case and "claim a due `PENDING` reminder" case are the
  SAME operation (both are "a reminder is available for a worker to
  own") avoids building and maintaining that logic twice.
- **`initiated_by_user_id` optional, not required**: making it required
  would force every existing `AppointmentService` call site and test (a
  large, STORY-007-era surface) to be touched for a STORY-013 concern,
  for no benefit — none of those call sites have a natural "who should
  this reminder be attributed to" answer to give. Optional-with-clear-
  attribution-when-available is the honest reflection of what identity
  is actually known at each call site, the same reasoning
  `WorkflowRun.patient_id` being optional already established in this
  codebase.
- **A reminder-scheduling failure never fails the appointment
  operation**: the appointment mutation has ALREADY committed by the
  time reminder scheduling runs. Letting a downstream, secondary
  failure propagate back as "your booking failed" would be a lie — the
  booking succeeded. This mirrors `ToolRegistry.execute`'s "never let a
  downstream failure corrupt an already-true result" philosophy from
  ADR-0010, applied to a new layer.
- **`NotificationMessage` carries no PHI by construction**: rather than
  trusting every future `NotificationProvider` implementation to
  remember not to log a patient's name, the interface simply never gives
  one a name to log — only internal UUIDs and a timestamp, the same
  "UUIDs are safe, human-readable identifiers are the sensitive part"
  discipline WORKFLOWS.md already established for `WorkflowRun.patient_id`.
  A real provider needing a display name/contact detail would resolve it
  itself, at the point of sending, from a trusted lookup — never carry
  it through this interface.
- **A NEW `WorkflowRun` on explicit retry, not resurrecting the old
  one**: `WorkflowStatus.FAILED` having zero allowed outgoing
  transitions is a deliberate, already-established invariant (STORY-009)
  — terminal states are truly terminal. Modeling an authorized retry as
  a fresh administrative execution keeps that invariant intact and
  produces a more honest audit trail: "attempt batch 1 failed; attempt
  batch 2 (authorized by X) succeeded" rather than a single run whose
  terminal status silently changed.

## Alternatives Considered

- **Celery + Redis/RabbitMQ**: rejected for this story's scale — see
  Rationale. Revisit if reminder volume or delivery-channel complexity
  (retries with backoff schedules, dead-letter queues, cross-region
  workers) grows past what a single-table `SKIP LOCKED` queue
  comfortably handles.
- **`asyncio.Queue` / in-process background task holding reminders in
  memory**: explicitly rejected by the story's own requirements — a
  process restart would silently lose every queued reminder. Not
  considered further.
- **A transactional outbox pattern** (writing "deliver this" as part of
  the SAME transaction as the notification send, with an idempotency
  key against the provider) for stronger delivery guarantees: considered
  and deferred. This story accepts an honest, industry-standard
  at-least-once tradeoff: if a worker crashes AFTER a real notification
  send but BEFORE committing `SENT`, the reminder is recovered and could
  be delivered a second time. `ConsoleNotificationProvider`'s only
  "delivery" is a log line, so this is a documented, low-stakes
  limitation for this story, not a production email/SMS gap left
  unacknowledged — a real provider added later should carry its own
  idempotency key derived from `Reminder.id`/`attempt_number` if
  exactly-once delivery becomes a real requirement.
- **A required `initiated_by_user_id`**: rejected — see Rationale.
- **Storing patient name/contact info in `NotificationMessage`**:
  rejected — see Rationale; also moot today, since `Patient` has no
  contact-detail fields yet.
- **Resetting the original `WorkflowRun` back to `RUNNING` on retry**:
  rejected — would violate the already-established "terminal statuses
  have zero outgoing transitions" invariant; see Rationale.

## Consequences

- A real production notification channel (email/SMS/WhatsApp) is a new
  module under `app/notifications/` implementing `NotificationProvider`
  — no change to `ReminderService`, `ReminderWorker`, or the database
  schema.
- A second reminder type (e.g. a document-collection follow-up) is a new
  `ReminderType` enum value plus a new `ReminderScheduler` method — no
  change to `ReminderService`'s state machine or `ReminderWorker`.
- If reminder volume ever requires a message broker, `acquire_pending`'s
  claim semantics are the contract a migration to one would need to
  preserve (at-least-once, no double-claim under concurrency) — this ADR
  should be superseded, not silently reinterpreted, if that migration
  happens.
- Any future story adding a REST API surface for reminders (list/cancel/
  retry as human-facing endpoints) reuses `ReminderService` directly —
  it already exposes every operation such an API would need.
