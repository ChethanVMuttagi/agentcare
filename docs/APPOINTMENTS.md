# AgentCare Appointments (Booking, Rescheduling, Cancellation)

This document describes the appointment booking engine implemented in
STORY-007: `Appointment`, `AvailabilityQueryService` (recurring
availability -> concrete bookable times), `AppointmentService` (booking,
rescheduling, cancellation), and the PostgreSQL-native concurrency
mechanism that makes double-booking genuinely impossible, not just
unlikely. It follows the same CURRENT vs. PLANNED discipline as
[ARCHITECTURE.md](ARCHITECTURE.md): everything described here as
implemented exists in the repository today; anything marked PLANNED does
not yet. See
[adr/ADR-0007-appointment-concurrency.md](adr/ADR-0007-appointment-concurrency.md)
for the decision record.

This story builds directly on
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) (`Department`,
`Practitioner`, `PractitionerDepartment`, `PractitionerAvailability`) and
[PATIENTS.md](PATIENTS.md) (`Patient`, patient self-access). Read those
first if the ownership/assignment/self-access vocabulary here is
unfamiliar.

## 1. Healthcare Safety Boundary

`Appointment` is an ADMINISTRATIVE SCHEDULING RECORD ONLY. It holds:
`id`, `organization_id`, `patient_id`, `practitioner_id`,
`department_id`, `start_at`, `end_at`, `status`, `cancellation_reason`,
`created_at`, `updated_at` — nothing else. There is no diagnosis, no
symptom field, no treatment/medication/prescription content, and no
clinical notes anywhere on this model, and none may be added without a
fresh, explicit design decision (mirroring `Patient`'s and
`Practitioner`'s discipline — see [PATIENTS.md](PATIENTS.md) and
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md)).

A future agent may book *"my cardiology follow-up"* by resolving
`Cardiology` as an existing `Department` and finding an available
`Practitioner` time — exactly the kind of administrative routing
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 14 describes.
It must **never** diagnose the user to decide they need Cardiology in the
first place. Nothing in this story builds such an agent; this boundary is
recorded here for whichever future story does.

## 2. The `Appointment` Model

`app/models/appointment.py` — table `appointments`.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key, application-generated |
| `organization_id` | UUID | NOT NULL, FK -> `organizations.id` (`ON DELETE RESTRICT`), indexed |
| `patient_id` | UUID | NOT NULL, indexed — see Section 6 for ownership FK |
| `practitioner_id` | UUID | NOT NULL, indexed — see Section 6 |
| `department_id` | UUID | NOT NULL, indexed — see Section 6 |
| `start_at` | TIMESTAMPTZ | NOT NULL |
| `end_at` | TIMESTAMPTZ | NOT NULL, `CHECK (start_at < end_at)` |
| `status` | VARCHAR(16) | NOT NULL, CHECK constrained to `AppointmentStatus`, indexed |
| `cancellation_reason` | VARCHAR(500) | nullable — short ADMINISTRATIVE reason only |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL |

## 3. Time & Timezone Semantics

`start_at`/`end_at` are timezone-aware `TIMESTAMPTZ` columns representing
**absolute instants**, persisted and reasoned about in UTC. This is
different from `PractitionerAvailability.start_time`/`end_time`, which
are plain, timezone-LESS wall-clock `TIME` values that only gain meaning
combined with that row's own `timezone` column (see
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 8).

Converting a recurring, local availability rule into a concrete UTC
instant is `AvailabilityQueryService`'s job (Section 4): it combines a
requested calendar date with a window's `start_time`/`end_time` and
`timezone` via Python's standard `zoneinfo`, so DST transitions on that
specific date are resolved correctly rather than assuming a fixed UTC
offset.

**Known limitation**: an appointment whose local start/end (in a given
availability window's timezone) would cross midnight into a different
calendar date can never be considered "within" that window — recurring
windows are inherently single-day. This is a deliberate simplification,
not a bug; a practitioner whose real-world shift crosses midnight would
need two adjacent `PractitionerAvailability` windows (one ending at
23:59:59, one starting at 00:00:00 the next day) rather than a single
window spanning the boundary.

## 4. Availability Calculation

`AvailabilityQueryService` (`app/services/availability_query.py`) is the
ONLY place recurring `PractitionerAvailability` rules become concrete,
bookable times. It performs no mutation and materializes nothing
persistent — every call recomputes candidates from the current
`PractitionerAvailability` and `Appointment` rows, on demand.

`list_available_times(organization_id, practitioner_id, department_id,
on_date, duration_minutes, slot_interval_minutes=15)`:

1. Requires an ACTIVE practitioner, ACTIVE department, and an ACTIVE
   `PractitionerDepartment` assignment between them — any one being
   inactive (or the assignment not existing) yields an empty result, not
   an error (see Section 13 "Availability Privacy").
2. Finds active `PractitionerAvailability` windows whose `day_of_week`
   matches `on_date`'s weekday.
3. Converts each matching window's local `[start_time, end_time)` into a
   concrete UTC `[start, end)` instant pair for `on_date`, using that
   window's own `timezone`.
4. Generates candidate start times at a fixed 15-minute interval
   (`DEFAULT_SLOT_INTERVAL_MINUTES`) within the window, keeping only
   those where the entire requested `duration_minutes` fits before the
   window ends.
5. Removes any candidate that overlaps an existing `BOOKED` appointment
   for that practitioner (a standard half-open-interval overlap test —
   the same rule the database's exclusion constraints enforce, see
   Section 7). `CANCELLED`/`COMPLETED` appointments never remove a
   candidate.
6. Removes any candidate whose start has already passed.

`is_within_availability(organization_id, practitioner_id, department_id,
start_at, end_at)` answers a narrower question — "does this SPECIFIC
proposed `[start_at, end_at)` fall entirely inside one active window?" —
used by `AppointmentService` to revalidate a booking/reschedule request
at write time (Section 6). It checks EVERY active window in its OWN
timezone independently (a practitioner could have differently-timezoned
windows on different days), and does not consider existing appointments
— that is the database's job (Section 7), not this method's.

### Slot Interval

Candidate times are offered every 15 minutes
(`DEFAULT_SLOT_INTERVAL_MINUTES`) — a deliberate, simple default, not
client-configurable via the API in this story. This keeps the
available-times response shape predictable without committing to a
per-organization/per-department configurable scheduling granularity that
no requirement in this story justifies yet.

## 5. Duration

`duration_minutes` is a caller-supplied, bounded administrative value —
**not** hardcoded to one medical appointment length. Bounds
(`app/services/availability_query.py`):

```
MIN_APPOINTMENT_DURATION_MINUTES = 15
MAX_APPOINTMENT_DURATION_MINUTES = 240
```

Enforced at the request-schema layer (`Field(ge=15, le=240)`,
`app/schemas/appointment.py`) AND re-validated at the service layer
(`AppointmentService._validate_duration`) — the same defense-in-depth
pattern used throughout this codebase (e.g. availability's
start-before-end check). The entire requested duration must fit inside a
single availability window — a duration that would only partially fit is
rejected the same as a start time outside any window at all.

## 6. Tenant & Assignment Ownership Integrity

Enforced at the DATABASE level, the same composite-FK technique
established in [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md)
Section 4-5:

- `(organization_id, patient_id) -> patients(organization_id, id)`
  (`fk_appointments_org_patient_patients`) — the patient must belong to
  the SAME organization as the appointment. This required adding a new
  composite unique constraint, `uq_patients_organization_id_id`, to
  `patients` (in addition to its plain PK) — the same "redundant unique
  key purely so something else can hold a composite FK against it"
  technique already used for `facilities`/`departments`/`practitioners`.
- `(organization_id, practitioner_id, department_id) ->
  practitioner_departments(organization_id, practitioner_id,
  department_id)` (`fk_appointments_assignment`) — the
  practitioner/department pairing must be a real, EXISTING assignment
  within the SAME organization. Because `practitioner_departments` itself
  carries composite FKs into `practitioners`/`departments`, this
  transitively guarantees the practitioner and department also both
  belong to this organization — mirroring exactly how
  `PractitionerAvailability`'s assignment FK works.

This does NOT, by itself, guarantee the assignment (or the
patient/practitioner/department) is currently ACTIVE — mutable state a
composite key can't naturally express. That is checked at the service
layer, every time (Section 8), never assumed from a prior successful
booking or availability query.

`patient_id`/`practitioner_id`/`department_id` also carry plain
single-column foreign keys purely so SQLAlchemy's `relationship()` has
an unambiguous join path distinct from the composite ownership FKs —
the same pattern `PractitionerAvailability` established.

## 7. Collision Prevention (Concurrency Safety)

**Double-booking is prevented at the DATABASE level**, via two
PostgreSQL `EXCLUDE` constraints — genuinely race-safe under concurrent
transactions, NOT a SELECT-then-INSERT service-level pre-check. See
[adr/ADR-0007-appointment-concurrency.md](adr/ADR-0007-appointment-concurrency.md)
for the full design rationale and alternatives considered.

```sql
-- No two BOOKED appointments for the same practitioner may have
-- overlapping [start_at, end_at) ranges.
EXCLUDE USING gist (
    practitioner_id WITH =,
    tstzrange(start_at, end_at, '[)') WITH &&
) WHERE (status = 'booked')

-- Same guarantee, for the patient.
EXCLUDE USING gist (
    patient_id WITH =,
    tstzrange(start_at, end_at, '[)') WITH &&
) WHERE (status = 'booked')
```

Requires the `btree_gist` PostgreSQL extension (GiST has no native
equality operator class for `uuid`; `btree_gist` supplies one) —
provisioned via Alembic (`CREATE EXTENSION IF NOT EXISTS btree_gist`),
never assumed to pre-exist.

```
09:00-09:30  +  09:15-09:45   -> REJECTED (overlap)
09:00-09:30  +  09:30-10:00   -> ALLOWED  (adjacent, half-open interval)
```

**`WHERE status = 'booked'` is the key to correct historical behavior**:
a `cancelled` appointment's row still exists (no hard deletion — Section
9) but never participates in either exclusion constraint, so cancelling
immediately frees that time. A `completed` appointment likewise never
blocks a new, unrelated booking merely because its status changed from
`booked` to `completed` — exactly the guarantee the story requires
("historical completed appointments should not permit contradictory
overlapping historical records merely because their status changed").

### Application-Level Translation

`AppointmentService` never lets a raw database error reach a client. It
catches `sqlalchemy.exc.IntegrityError`, checks
`exc.orig.sqlstate == "23P01"` (PostgreSQL's SQLSTATE for
`exclusion_violation` — the ONLY way this code can occur, since these are
the only two EXCLUDE constraints in the entire schema), rolls back the
transaction (so the session is never left in a failed-transaction state),
and raises `AppointmentConflictError` (409). Any OTHER `IntegrityError`
— one not specifically expected — propagates unhandled, becoming a
generic 500, exactly like every other service in this codebase (see
`app.services.patient`'s module docstring). `constraint_name` is
deliberately NOT used to distinguish the two constraints: SQLAlchemy's
asyncpg dialect wraps the raw `asyncpg.exceptions.ExclusionViolationError`
in its own DBAPI-emulation exception, which preserves `.sqlstate` but
does NOT preserve `.constraint_name` — discovered and verified against
real PostgreSQL while building `tests/db/test_appointment_concurrency.py`.

### Proof, Not Assertion

`tests/db/test_appointment_concurrency.py` launches two GENUINELY
independent, concurrently-executing transactions (separate connections,
`asyncio.gather`, no mocking, no sequential simulation) attempting
overlapping bookings — once for the practitioner-side constraint, once
for the patient-side constraint — and asserts exactly one succeeds and
the other fails with `AppointmentConflictError`, against real
PostgreSQL. This is the mandatory proof this story required; sequential
unit tests (`tests/models/test_appointment.py`) additionally prove the
constraints themselves reject/allow the right cases, but are explicitly
NOT a substitute for the concurrency proof.

### Patient Double-Booking

**Decision: prevented, DB-enforced, same mechanism as practitioners.**
A patient cannot hold two overlapping `booked` appointments — with the
same or different practitioners — for the same reason a practitioner
double-booking is prevented: it represents a genuinely conflicting
real-world time commitment. See
[adr/ADR-0007-appointment-concurrency.md](adr/ADR-0007-appointment-concurrency.md)
for the full rationale and what would justify relaxing this later (e.g.
a dependent modeled as a separate `Patient` record is NOT blocked by
this constraint, since it is keyed per `patient_id`).

## 8. Booking

`AppointmentService.book_appointment(organization_id, patient_id,
practitioner_id, department_id, start_at, duration_minutes)` — commits
only once every check passes AND the database has confirmed no
collision. Validation order (`_ensure_bookable`):

1. `duration_minutes` within bounds (Section 5).
2. Patient exists in this organization and is active.
3. Practitioner exists in this organization and is active.
4. Department exists in this organization and is active.
5. An ACTIVE `PractitionerDepartment` assignment links them.
6. `start_at` is in the future.
7. `[start_at, end_at)` falls entirely within an active availability
   window (`AvailabilityQueryService.is_within_availability`).
8. The INSERT itself succeeds — the database's EXCLUDE constraints are
   the final, authoritative check (Section 7).

**A client is never trusted merely because it previously called the
available-times endpoint.** Every one of the checks above is re-run,
fresh, at booking time — a time that was available a second ago may no
longer be by the time the booking request arrives, and the only
authoritative "is this still free" answer is the database's own
constraint check at insert time.

## 9. Rescheduling

`AppointmentService.reschedule_appointment(organization_id,
appointment_id, start_at, duration_minutes, patient_id=None)` — updates
the SAME row in place (never cancel-old-plus-create-new; see
[adr/ADR-0007-appointment-concurrency.md](adr/ADR-0007-appointment-concurrency.md)
for why). Re-runs the FULL booking validation above against the
appointment's EXISTING practitioner/department/patient — a
practitioner/department/assignment that was active at original booking
time could have become inactive since, and that must be caught again,
not assumed still true.

- Only a `booked` appointment can be rescheduled — `cancelled` or
  `completed` raises `InvalidAppointmentTransitionError` (422).
- A conflicting new time raises `AppointmentConflictError` (409) via the
  identical EXCLUDE-constraint-violation translation as booking. The
  transaction is rolled back, so the ORIGINAL appointment row is left
  **completely unchanged** in the database — proven directly in
  `tests/services/test_appointment.py::test_reschedule_conflict_leaves_original_appointment_unchanged`
  (re-queries the appointment after the failed reschedule and asserts
  its original `start_at`/`end_at`).

## 10. Cancellation

`AppointmentService.cancel_appointment(organization_id, appointment_id,
cancellation_reason=None, patient_id=None)` — `booked -> cancelled`.

- `cancellation_reason` is an OPTIONAL, short (max 500 characters)
  ADMINISTRATIVE reason (e.g. "patient requested", "practitioner
  unavailable") — never clinical content; nothing in this model can hold
  clinical content in the first place (Section 1).
- No hard deletion, ever — a cancelled appointment's row persists, with
  its full original `start_at`/`end_at`/`created_at` intact, only
  `status` and `cancellation_reason` change.
- **Repeated cancellation is rejected, not idempotent**: cancelling an
  already-`cancelled` (or `completed`) appointment raises
  `InvalidAppointmentTransitionError` (422) — the same uniform rule
  every other invalid transition uses, rather than a special-cased
  silent no-op.
- A cancelled appointment immediately stops blocking new bookings over
  its former time (Section 7's `WHERE status = 'booked'` predicate).

## 11. Status Model & Transitions

`AppointmentStatus` (`app/models/appointment.py`, `enum.StrEnum`):
`booked`, `cancelled`, `completed`. Persisted the same way as every
other enum in this codebase (`VARCHAR` + real database `CHECK`
constraint).

| Status | Occupies time (collision-relevant)? | Reachable via this story's API |
|---|---|---|
| `booked` | Yes — participates in both EXCLUDE constraints | Initial state (booking) |
| `cancelled` | No | `POST .../cancel` |
| `completed` | No (historical) | **Not reachable in this story** — see below |

All allowed transitions are centralized in `AppointmentService`
(`reschedule_appointment`'s and `cancel_appointment`'s status guard) —
never scattered `if status == ...` checks in route handlers. An invalid
transition always raises `InvalidAppointmentTransitionError` (422).

**`completed` is modeled but not exposed.** No clinical encounter
workflow exists in AgentCare yet (no "patient checked in," "seen by
practitioner," "checked out" concept) to meaningfully drive a completion
trigger, and building one speculatively risks guessing the wrong
trigger. The status exists in the schema and the EXCLUDE constraints'
predicate design already correctly accounts for it (a `completed`
appointment never blocks a new booking — Section 7) — a future story
adding a completion transition needs only a new service method/route,
not a schema migration.

## 12. Repository

`app/repositories/appointment.py` — tenant-scoped, no RBAC, never
commits (`add`/`flush`/`query` only), the same discipline as every other
repository in this codebase:

- `get_by_id(organization_id, appointment_id)`
- `list_by_organization(organization_id, ...)` — administrative,
  org-wide (Section 14 "Listing Privacy")
- `list_by_patient(organization_id, patient_id, ...)` — self-scoped
- `list_practitioner_appointments_in_range(organization_id,
  practitioner_id, range_start, range_end, statuses=(BOOKED,))` — used
  by `AvailabilityQueryService` to remove conflicting candidate times
- `create(appointment)`

## 13. Services

- **`AvailabilityQueryService`** (`app/services/availability_query.py`)
  — read-only, Section 4.
- **`AppointmentService`** (`app/services/appointment.py`) — booking,
  rescheduling, cancellation; owns transaction completion; translates
  the specific, expected EXCLUDE-constraint violation into a domain
  error (Section 7); centralizes all status transitions (Section 11).

Neither service performs RBAC — that is a route/dependency concern (see
`app.auth.dependencies`), the same layering `PatientService`/
`PractitionerService`/`AvailabilityService` already establish.

## 14. API Routes

```
GET   /api/v1/organizations/{organization_id}/practitioners/{practitioner_id}/available-times
POST  /api/v1/organizations/{organization_id}/appointments
GET   /api/v1/organizations/{organization_id}/appointments/{appointment_id}
GET   /api/v1/organizations/{organization_id}/appointments
PATCH /api/v1/organizations/{organization_id}/appointments/{appointment_id}/reschedule
POST  /api/v1/organizations/{organization_id}/appointments/{appointment_id}/cancel
```

`available-times` lives under the existing `practitioners` router
(`app/api/v1/endpoints/practitioners.py`) rather than a new router, since
its path is naturally scoped under `.../practitioners/{practitioner_id}`.
The remaining five routes live in a new
`app/api/v1/endpoints/appointments.py`. Every route requires a valid
Bearer token and an active, database-resolved `OrganizationMembership`
(`get_current_membership`) before any appointment data is touched.

## 15. RBAC (Authorization Matrix)

| Action | ADMIN | STAFF | PATIENT |
|---|---|---|---|
| View available times | allowed | allowed | allowed (own booking discovery) |
| Book appointment | allowed (any patient) | allowed (any patient) | allowed (self only — Section 16) |
| Get appointment by id | allowed (any, org-wide) | allowed (any, org-wide) | allowed (own only — 404 otherwise) |
| List appointments | allowed (org-wide) | allowed (org-wide) | allowed (self-scoped only — Section 17) |
| Reschedule appointment | allowed (any) | allowed (any) | allowed (own only) |
| Cancel appointment | allowed (any) | allowed (any) | allowed (own only) |

`ADMIN` and `STAFF` have identical access for this story — both are
trusted organizational staff for administrative scheduling purposes,
mirroring [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md)'s existing
precedent of not distinguishing them for scheduling operations except
where structural changes (creating departments/practitioners) are
involved, which appointments never are.

## 16. Patient Identity: Never Trusted From The Client

**A `PATIENT`-role caller can never supply another patient's id and act
on their behalf.** For every route a patient can reach,
`app/api/v1/endpoints/appointments.py` resolves `patient_id` from the
caller's OWN linked `Patient` record
(`PatientService.get_own_patient_record`, keyed off the authenticated
`User.id` — see [RBAC.md](RBAC.md) Section 10), never from a
client-supplied value:

- **Booking**: `AppointmentCreate.patient_id` (request body) is used
  ONLY for `ADMIN`/`STAFF` callers booking on behalf of a patient. For a
  `PATIENT` caller, it is read but **deliberately ignored, not merely
  validated** — even if a patient supplies a different patient's UUID,
  the booking uses their own resolved id. Proven directly in
  `tests/api/test_appointment_endpoints.py::test_patient_cannot_book_for_another_patient`.
- **Get / reschedule / cancel**: the resolved own-patient id is passed
  into `AppointmentService` as an ownership restriction
  (`patient_id=...`); an appointment belonging to a different patient
  404s — the SAME shape as a truly nonexistent appointment id (Section
  17).
- A `PATIENT`-role caller with no linked `Patient` record at all (no
  `Patient.user_id` pointing to them) gets the same 404
  `PatientNotFoundError` the existing `.../patients/me` self-access route
  already produces (see [PATIENTS.md](PATIENTS.md)) — they cannot book,
  view, reschedule, or cancel anything.

## 17. Privacy

### Listing Privacy

`GET .../appointments` is ONE route serving two different views: an
`ADMIN`/`STAFF` caller receives the organization-wide list; a `PATIENT`
caller reaching the exact same route receives ONLY their own
appointments (`AppointmentService.list_appointments_for_patient`) — never
the organization-wide list, and never another patient's appointments
mixed in.

### Availability Privacy

`GET .../available-times` returns ONLY free times
(`AvailableTimeSlotResponse`: `start_at`, `end_at` — nothing else). It
never reveals WHY a time is unavailable, and never exposes any detail of
the appointment that blocks it (whose it is, what patient, what
practitioner note) — a `PATIENT` caller cannot use this endpoint to learn
anything about another patient's schedule. An inactive
practitioner/department/assignment simply produces an empty result, the
same observable outcome as "this practitioner has no windows configured
today" — a caller cannot distinguish "temporarily unschedulable" from
"genuinely fully booked" from the response shape alone.

### Response Shape

`AppointmentResponse` exposes exactly: `id`, `organization_id`,
`patient_id`, `practitioner_id`, `department_id`, `start_at`, `end_at`,
`status`, `cancellation_reason`, `created_at`, `updated_at` — nothing
more, and nothing clinical exists on the model to accidentally expose in
the first place (Section 1).

## 18. Cross-Tenant Protection

Every tenant-owned lookup requires `organization_id` — there is no
unscoped `get_by_id` anywhere in `app.repositories.appointment`, the
same discipline every other repository in this codebase follows. Because
repository queries filter by `organization_id` in the WHERE clause
itself, a cross-tenant lookup naturally returns the same "not found" as a
truly nonexistent id — no special-casing needed, and no information
disclosed about whether a UUID belongs to a real appointment in a
different organization. Verified directly, end-to-end, in
`tests/api/test_appointment_endpoints.py`:

- Org A cannot book using Org B's patient, practitioner, or department
  id (composite-FK-backed 404s from `AppointmentService._ensure_bookable`
  treating a cross-tenant lookup as "not found" — see Section 6).
- Org A cannot retrieve, reschedule, or cancel Org B's appointment by
  UUID.
- Patient A cannot retrieve, reschedule, or cancel Patient B's
  appointment, even within the same organization (Section 16).
- A raw-SQL cross-tenant ownership mismatch is rejected at the database
  level regardless of application code
  (`tests/models/test_appointment.py::test_appointment_rejects_cross_tenant_patient`).

## 19. Error Model

All mapped through the existing global exception handling
(`app.core.exceptions`) — never a raw `IntegrityError`/driver exception
reaching a client:

| Exception | Status | Meaning |
|---|---|---|
| `AppointmentNotFoundError` | 404 | No appointment matches, within the caller's own organization (and, if patient-scoped, that patient). |
| `PatientInactiveError` | 422 | The patient exists but is not active. |
| `PractitionerInactiveError` | 422 | The practitioner exists but is not active. |
| `DepartmentInactiveError` | 422 | The department exists but is not active. |
| `PractitionerNotAssignedError` | 422 | No active practitioner-department assignment exists. |
| `InvalidAppointmentDurationError` | 422 | `duration_minutes` outside [15, 240]. |
| `AppointmentInPastError` | 422 | The requested start time is not in the future. |
| `AppointmentOutsideAvailabilityError` | 422 | The requested time does not fall entirely within an active availability window. |
| `AppointmentConflictError` | 409 | This time is no longer available — translated from the database's EXCLUDE constraint (Section 7). |
| `InvalidAppointmentTransitionError` | 422 | An invalid status transition (reschedule/cancel of a non-`booked` appointment). |
| `PatientIdRequiredError` | 422 | An ADMIN/STAFF caller booked without supplying `patient_id`. |

## 20. Known Limitations

- An appointment that would cross midnight in an availability window's
  local timezone can never be considered inside that window (Section 3).
- The 15-minute slot interval and [15, 240]-minute duration bounds are
  not currently configurable per-organization/per-department — a
  single, global default for this story.
- `completed` has no reachable transition in this story's API (Section
  11) — a deliberate, documented deferral.
- Patient double-booking prevention is per-`patient_id` — a dependent
  modeled as a separate `Patient` record is unaffected by it (Section 7
  "Patient Double-Booking").

## 21. Current vs. Planned

**Current (this story):** `Appointment`, `AppointmentStatus`;
database-enforced tenant/assignment ownership integrity; genuinely
race-safe practitioner AND patient double-booking prevention via
PostgreSQL `EXCLUDE` constraints (`btree_gist`); on-demand availability
calculation (`AvailabilityQueryService`); booking, rescheduling
(in-place), cancellation (`AppointmentService`); the full API/RBAC
surface above; patient self-service with server-derived identity;
cross-tenant and listing/availability privacy protections.

**Explicitly not implemented in this story** (later stories): documents,
reminders, notifications, waitlists, `WorkflowRun`, any LLM/agent/
LangGraph integration, a `completed` transition/clinical-encounter
workflow, diagnosis/treatment/prescription of any kind, a frontend,
per-organization configurable slot interval or duration bounds.
