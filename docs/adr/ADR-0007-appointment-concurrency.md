# ADR-0007: Appointment Booking & Concurrency Safety

Status: Accepted

Date: 2026-07-27

## Context

STORY-006 (ADR-0006) established `Department`, `Practitioner`,
`PractitionerDepartment`, and `PractitionerAvailability` — recurring
availability rules, deliberately NOT materialized appointment slots, and
deliberately NOT race-proof (its overlap check is a service-level
pre-check only; see `docs/SCHEDULING_RESOURCES.md` Section 9). That ADR
explicitly deferred the question of genuine concurrency safety to "a
future appointment/scheduling-hardening story... if and when a booking
story's concurrency requirements make the current service-level check
demonstrably insufficient."

This is that story. STORY-007 introduces `Appointment`, the first
genuinely concurrent-write-sensitive resource in AgentCare: two
receptionists, two patient-portal sessions, or a human and a future
agent could all attempt to book the same practitioner's same time slot
within milliseconds of each other. A SELECT-then-INSERT
service-level check (the pattern ADR-0006 used and explicitly flagged as
non-race-proof) is NOT sufficient here — two concurrent requests can both
pass the pre-check before either commits, and both succeed, producing a
double-booking. The central question this ADR resolves: what mechanism
makes double-booking genuinely impossible, not just unlikely?

Secondary questions: how does a concrete appointment relate to a
recurring `PractitionerAvailability` rule? What statuses does an
appointment need, and which of them should occupy a practitioner's (and
a patient's) time for collision purposes? Should a patient be preventable
from double-booking themselves, the same way a practitioner is? Should
rescheduling replace an appointment's identity, or preserve it?

## Decision

1. **PostgreSQL `EXCLUDE` constraints, using `btree_gist`, are the
   collision-prevention mechanism** — not a service-level pre-check, and
   not application-level locking. Two constraints on `appointments`:
   `ex_appointments_practitioner_no_overlap` (`practitioner_id` equality
   `+` `tstzrange(start_at, end_at, '[)')` overlap) and
   `ex_appointments_patient_no_overlap` (same shape, keyed on
   `patient_id`). Both apply `WHERE (status = 'booked')`. This is a
   genuinely race-safe, database-enforced guarantee: PostgreSQL itself
   serializes conflicting concurrent inserts/updates via the same
   index-level mechanism that backs ordinary unique constraints, and
   rejects the losing transaction with a real `ExclusionViolationError`
   — proven against real PostgreSQL with genuinely concurrent
   transactions in `tests/db/test_appointment_concurrency.py`, not merely
   asserted.
2. **`btree_gist` is provisioned via Alembic**
   (`CREATE EXTENSION IF NOT EXISTS btree_gist`), not assumed to already
   exist — required because GiST indexes have no native equality operator
   class for scalar types (`uuid`) out of the box; `btree_gist` supplies
   one, letting an EXCLUDE constraint combine "same practitioner/patient"
   (equality) with "overlapping time range" (range overlap, `&&`) in a
   single index.
3. **The predicate `WHERE status = 'booked'` is what makes historical
   status changes safe.** A `cancelled` or `completed` appointment never
   participates in either exclusion constraint, so cancelling a booking
   immediately frees its time for a new one, and a `completed` historical
   record can never spuriously conflict with a later, unrelated booking
   merely because of a status transition. This was a deliberate schema
   decision, not an incidental side effect of using `WHERE` at all.
4. **Patient double-booking is also DB-enforced, using the identical
   mechanism** as practitioner double-booking (Decision 1) — not merely
   documented as a policy. A patient with two overlapping active
   appointments (with the same or different practitioners) is prevented
   for the same reason a practitioner double-booking is: it represents a
   genuinely conflicting real-world commitment for one person's time,
   and AgentCare has no information suggesting a legitimate reason for a
   single patient to hold two simultaneous administrative appointments in
   this story's scope. See "Alternatives Considered" for what would
   justify relaxing this later.
5. **Recurring availability is queried and converted on demand, per
   request** (`AvailabilityQueryService`) — no materialized appointment-
   slot table. A `PractitionerAvailability` window is combined with a
   requested calendar date and its OWN `timezone` (via `zoneinfo`) to
   produce concrete UTC `[start, end)` instants. This continues ADR-0006's
   Decision 5 rather than reopening it — nothing about concurrency safety
   requires materializing slots; the EXCLUDE constraint operates on
   `Appointment` rows directly.
6. **`start_at`/`end_at` are `TIMESTAMPTZ`, persisted and reasoned about
   in UTC**, with `CHECK (start_at < end_at)`. `duration_minutes` is a
   caller-supplied, bounded value (15-240 minutes) — not hardcoded to any
   one medical appointment type, and not required to be a multiple of the
   availability-query slot interval (15 minutes, `AvailabilityQueryService`).
7. **`AppointmentStatus` is `booked` / `cancelled` / `completed`** — no
   larger speculative workflow. `completed` is modeled (so the exclusion-
   constraint predicate design is correct and forward-compatible) but NO
   API transition into it exists in this story — see Consequences.
8. **Rescheduling updates the SAME row in place** — never cancel-old-
   plus-create-new. An appointment's `id` is its durable identity across
   reschedules; there is no reason to fragment that history across
   multiple rows when a single mutable `start_at`/`end_at` pair
   expresses the same fact with less ceremony and no audit-linkage
   problem to solve.
9. **Cancellation transitions are one-way and non-idempotent**:
   `booked -> cancelled` is the only transition `POST .../cancel`
   performs; cancelling an already-`cancelled` (or `completed`)
   appointment is rejected with the same `InvalidAppointmentTransitionError`
   as any other invalid transition, rather than silently succeeding a
   second time. A single, consistent rule is simpler to reason about
   than a special-cased idempotent path.
10. **No pre-check select before booking's insert.** `AppointmentService.
    book_appointment`/`reschedule_appointment` always attempt the write
    and translate the specific, expected `IntegrityError` (SQLSTATE
    `23P01`, exclusion_violation) into `AppointmentConflictError` (409).
    This is deliberate, not an oversight: a pre-check here would
    misleadingly suggest the pre-check is what prevents double-booking,
    when the EXCLUDE constraint is the only thing that actually does.

## Rationale

- **`EXCLUDE` + `btree_gist` over service-level locking or
  `SELECT ... FOR UPDATE`**: a row-locking approach only prevents
  collisions between transactions that lock the SAME existing row(s) —
  it does nothing for two transactions INSERTING two NEW, mutually
  overlapping rows, which is exactly the double-booking scenario. An
  EXCLUDE constraint is index-backed and evaluated by PostgreSQL itself
  at insert/update time against every existing (and concurrently
  in-flight) row, which is the correct primitive for "no two rows may
  overlap by this predicate," independent of how many rows already
  exist or which transaction created them.
- **Real infrastructure now, deferred by ADR-0006 then**: ADR-0006
  explicitly reasoned that a race-proof mechanism was "more
  infrastructure than [that] story's... scope justifies" for recurring
  AVAILABILITY windows (an administrative-resource-definition problem,
  low write frequency, low real-world cost if a race briefly occurred).
  `Appointment` is categorically different: it is the actual, patient-
  facing booking write path, explicitly identified by this story as
  needing genuine concurrency guarantees. The infrastructure cost
  (`btree_gist`, two EXCLUDE constraints) is justified here specifically
  because the story's own requirements make it necessary, not because
  the pattern is free.
- **Status-scoped predicate (`WHERE status = 'booked'`)**: without it, a
  `cancelled` appointment's historical row would still be considered by
  the exclusion constraint (its range never changes, only its status
  does), permanently blocking that time slot forever — clearly wrong.
  Scoping the constraint to the one status that actually occupies time
  is the direct, minimal fix, and generalizes correctly to `completed`
  too.
- **Patient double-booking, same mechanism, same predicate**: once the
  EXCLUDE-constraint infrastructure exists for practitioners, adding an
  equivalent constraint for patients is a small marginal cost (one more
  constraint, same migration, same extension) for a real correctness
  property AgentCare has no reason to forgo in this story's
  administrative-scheduling scope. Enforcing it as a database constraint
  — not merely a service-level check — keeps it true under the exact
  same concurrent-write conditions the practitioner guarantee protects
  against, rather than being weaker for patients than for practitioners
  for no principled reason.
- **No materialized slots**: unchanged reasoning from ADR-0006 — a
  practitioner's availability is typically far larger (weeks/months of
  recurring rules) than the sparse set of actually-booked appointments;
  materializing every possible slot as a row would be a write-amplifying,
  storage-wasting solution to a problem the EXCLUDE constraint already
  solves at the level that actually matters (the booked rows themselves).
- **UTC persistence + per-window `zoneinfo` conversion**: an appointment
  is a single, unambiguous instant in time regardless of which timezone
  any particular availability window happens to be authored in;
  converting at read/write time via `zoneinfo` (rather than storing a
  fixed UTC-offset assumption) keeps DST transitions correct for
  whichever calendar date is actually being booked, consistent with
  `Facility`/`PractitionerAvailability`'s existing `zoneinfo` strategy.
- **Reschedule-in-place, not cancel-and-recreate**: this story has no
  concrete audit/compliance requirement demanding a full old-appointment
  paper trail as a SEPARATE row; a single row with `created_at`
  (original booking) and `updated_at` (most recent reschedule) already
  answers "when was this booked, when did it last change" without
  fragmenting one logical appointment's identity across multiple UUIDs.
  If a future story needs a full change-history audit trail, that is a
  new, deliberate decision (e.g. an append-only appointment-history
  table), not a reason to abandon in-place updates now.
- **No pre-check select before insert**: this project's established
  convention elsewhere (e.g. `DepartmentService`'s code-conflict
  pre-check) DOES use a pre-check as a UX nicety layered on top of a
  real database constraint. `Appointment` deliberately does NOT follow
  that pattern: this story's explicit purpose is to prove the database
  constraint is the actual, sufficient safety mechanism, and a redundant
  pre-check risks obscuring that in future reviews ("is the pre-check
  what's actually preventing this, or the constraint?"). Skipping it
  keeps the constraint unambiguously the single source of truth.

## Alternatives Considered

- **`SELECT ... FOR UPDATE` row locking on the practitioner's existing
  appointments before insert**: rejected — cannot lock rows that don't
  exist yet, so it does not prevent two concurrent transactions from
  both inserting new, mutually overlapping rows. Would need to lock a
  coarser resource (e.g. the practitioner row itself), which serializes
  ALL bookings for that practitioner globally — a much larger
  concurrency/throughput cost than an index-level EXCLUDE constraint,
  for a weaker guarantee than what PostgreSQL already provides natively.
- **Application-level distributed lock (e.g. Redis) keyed on
  practitioner+time-bucket**: rejected — introduces a new piece of
  infrastructure (a lock service) and a new failure mode (lock service
  unavailable, lock expiry races) to solve a problem PostgreSQL already
  solves natively and transactionally, with no separate service to keep
  consistent with the database's own commit/rollback semantics.
- **Advisory locks (`pg_advisory_xact_lock`) keyed on practitioner id**:
  considered — would work, but (like row-locking) serializes ALL
  bookings for one practitioner regardless of whether their requested
  times actually overlap, and requires the application to compute and
  maintain a correct locking key/granularity by hand. An EXCLUDE
  constraint expresses the actual invariant ("no two ranges overlap")
  declaratively and lets PostgreSQL's own concurrency control handle
  granularity correctly without bespoke locking code.
- **Only practitioner-side collision prevention, patient double-booking
  left undocumented/unenforced**: rejected — the story explicitly
  required a documented decision either way; leaving it silently
  unenforced would be an accidental gap, not a considered one. Enforcing
  it via the identical, already-justified mechanism cost little extra
  and closes a real gap (nothing stops a patient from being
  administratively double-booked otherwise).
- **Cancel-old-appointment-and-create-new-one for reschedule**: rejected
  — no concrete requirement in this story needs appointment identity to
  fragment across a reschedule, and doing so would need `created_at`
  reinterpretation (does it mean "originally booked" or "this specific
  version created"?) with no clear answer given the current schema.
- **A dedicated `completed` transition/endpoint in this story**:
  considered, declined — no clinical encounter workflow exists yet to
  meaningfully drive "when is an appointment complete" (walked in? seen
  by practitioner? checked out?), and building one speculatively risks
  guessing the wrong trigger. `completed` remains a modeled, reachable-
  in-the-future status; the exclusion-constraint predicate design
  already accounts for it correctly whenever a future story adds the
  transition.
- **Materializing concrete appointment-slot rows ahead of booking**:
  rejected for the same reasons as ADR-0006 — see Rationale.

## Consequences

- Every future story that needs a genuinely concurrent-write-sensitive
  invariant on a range-like resource should default to considering a
  PostgreSQL EXCLUDE constraint (with `btree_gist` if needed) before
  reaching for application-level locking — this ADR is the reference
  precedent for that technique in this codebase, the same way ADR-0003's
  composite-FK pattern is the reference for tenant-ownership integrity.
- `btree_gist` is now a real, migration-managed dependency of this
  schema. Any future migration that needs to run on a fresh database
  must apply migrations in order (the extension is created before
  `appointments`); `alembic downgrade` past this migration removes it —
  see the migration file for the "AgentCare is the only owner of this
  extension" caveat that must be revisited if that ever stops being
  true.
- `completed` exists in the schema and the exclusion-constraint
  predicate today, with NO way to reach it via this story's API. The
  first future story that needs a completion transition inherits a
  status that is already schema-correct and does not need a migration
  to "activate" — only a new service method/endpoint.
- Rescheduling changing `start_at`/`end_at` in place means
  `Appointment.updated_at` reflects "most recently changed," not
  "created." Any future reporting/audit need for a full reschedule
  history is a new, additive design (e.g., an append-only history table
  or event log), not a retrofit of this schema.
- Patient double-booking prevention is now a hard database constraint,
  not a policy note. If a future story identifies a legitimate reason
  for a patient to hold two simultaneous appointments (e.g. a parent
  booking concurrently for themselves and a dependent modeled as a
  SEPARATE patient record — which this constraint does NOT prevent,
  since it's keyed per `patient_id` — or some other case), that is a new
  ADR revisiting this constraint specifically, not a silent workaround.
- Future agent/tool usage: an agentic booking tool built on top of
  `AppointmentService` inherits the exact same guarantees a human-driven
  API caller gets — it cannot bypass the EXCLUDE constraints, and a
  conflicting attempt surfaces as the same `AppointmentConflictError`
  (409) a human client would see, which the agent must handle (e.g. by
  re-querying available times), not something a future story needs to
  re-derive.
