# AgentCare Scheduling Resources (Department, Practitioner, Availability)

This document describes the administrative scheduling-resource foundation
implemented in STORY-006: `Department`, `Practitioner`,
`PractitionerDepartment` (assignment), and `PractitionerAvailability`
(recurring availability). It follows the same CURRENT vs. PLANNED
discipline as [ARCHITECTURE.md](ARCHITECTURE.md): everything described
here as implemented exists in the repository today; anything marked
PLANNED does not yet. See
[adr/ADR-0006-scheduling-resources.md](adr/ADR-0006-scheduling-resources.md)
for the decision record.

**This story does NOT implement appointments** — that is STORY-007, see
[APPOINTMENTS.md](APPOINTMENTS.md). This story establishes the real,
persisted resources — departments, practitioners, their assignments, and
recurring availability windows — that the appointment-booking engine
(and later agents) query. See Section 15.

## 1. Administrative Scope (Healthcare Safety Boundary)

`Department` and `Practitioner` are ADMINISTRATIVE scheduling resources
only. Neither holds, nor is permitted to hold, any clinical/medical
content — no diagnosis, symptoms, medication, treatment recommendations,
prescriptions, or clinical notes anywhere in this domain (see
[ARCHITECTURE.md](ARCHITECTURE.md) Section 7 "Healthcare Safety
Boundary"). This mirrors the same discipline STORY-005 established for
`Patient`.

**`Practitioner`, not `Doctor`**: this codebase deliberately uses the
broader domain term `Practitioner` for the schedulable-healthcare-
professional concept, rather than hardcoding the model around physicians
specifically. A `PractitionerType` (Section 3) may be a physician,
physiotherapist, counselor, therapist, or other healthcare professional —
the scheduling model doesn't need to (and shouldn't) know or care about
clinical distinctions beyond that coarse categorization. See Section 13
for the related, and more safety-critical, "administrative routing is not
diagnosis" boundary.

## 2. Department

`app/models/department.py` — table `departments`. An administrative
scheduling unit (e.g. "Cardiology") belonging to exactly one `Facility`
(see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for `Facility` itself).

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key, application-generated |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id` (`ON DELETE RESTRICT`), indexed |
| `facility_id` | UUID | NOT NULL, indexed — see Section 4 for its FK |
| `name` | VARCHAR(255) | NOT NULL |
| `code` | VARCHAR(50) | NOT NULL, unique together with `facility_id` |
| `is_active` | BOOLEAN | NOT NULL, default `true` |
| `created_at` | TIMESTAMPTZ | NOT NULL |
| `updated_at` | TIMESTAMPTZ | NOT NULL |

`code` is scoped to its facility, not global — the same code may
validly appear under different facilities (including facilities
belonging to different organizations).

## 3. Practitioner

`app/models/practitioner.py` — table `practitioners`. A schedulable
healthcare professional, scoped to exactly one `Organization`.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key, application-generated |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id` (`ON DELETE RESTRICT`), indexed |
| `first_name` | VARCHAR(255) | NOT NULL, whitespace-normalized on assignment |
| `last_name` | VARCHAR(255) | NOT NULL, whitespace-normalized on assignment |
| `practitioner_type` | VARCHAR(32) | NOT NULL, CHECK constrained to `PractitionerType` |
| `is_active` | BOOLEAN | NOT NULL, default `true` |
| `created_at` | TIMESTAMPTZ | NOT NULL |
| `updated_at` | TIMESTAMPTZ | NOT NULL |

`PractitionerType` (`enum.StrEnum`): `physician`, `physiotherapist`,
`counselor`, `therapist`, `other` — deliberately small (same persistence
strategy as every other enum in this codebase: `VARCHAR` + a real
database `CHECK` constraint, not a native PostgreSQL `ENUM`). This is
**not** a medical specialty field — specialty/department association is
a separate concept (`PractitionerDepartment`, Section 5), not encoded
into `practitioner_type`.

Deliberately minimal, matching `Patient`'s data-minimization discipline
(STORY-005): no diagnosis capability, no treatment/prescription
authority, and no unnecessary personal information (no email, phone,
address, or `User` linkage — see Section 12).

## 4. Department Ownership Integrity

A `Department` belongs to both an `Organization` (directly,
`organization_id`) and a `Facility` (`facility_id`) — and the referenced
`Facility` MUST belong to that SAME `Organization`. A `Department` with
`organization_id = Org A` but `facility_id` pointing at a facility
belonging to `Org B` must never be possible.

**This is enforced at the DATABASE level**, not just application
validation:

1. `Facility` gained a composite unique constraint,
   `uq_facilities_organization_id_id` on `(organization_id, id)` — in
   addition to its existing primary key on `id` alone (see
   `app/models/facility.py`). This is "redundant" in the sense that
   `id` alone is already unique; it exists purely so something else can
   hold a composite foreign key against `(organization_id, id)` together.
2. `Department` holds a composite foreign key,
   `fk_departments_organization_id_facility_id_facilities`, on
   `(organization_id, facility_id) -> facilities(organization_id, id)`.

The practical effect: PostgreSQL itself rejects any `Department` insert
or update where the referenced facility's `organization_id` doesn't
match the department row's own `organization_id` — verified directly
against real PostgreSQL (`tests/models/test_department.py`) and via a
raw-SQL insert bypassing the ORM entirely (proving it's a real
database-level `IntegrityError`, not just SQLAlchemy-side validation).

This same composite-FK technique is used again, twice more, one level
down — see Section 5.

## 5. Practitioner ↔ Department Association

A practitioner may work in multiple departments; a department may
contain multiple practitioners — a genuine many-to-many relationship,
implemented as a real model (not a bare association table),
`PractitionerDepartment` (`app/models/practitioner_department.py`, table
`practitioner_departments`), so the assignment has its own lifecycle:

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key, application-generated |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id` (`ON DELETE RESTRICT`), indexed |
| `practitioner_id` | UUID | NOT NULL, indexed |
| `department_id` | UUID | NOT NULL, indexed |
| `is_active` | BOOLEAN | NOT NULL, default `true` |
| `created_at` | TIMESTAMPTZ | NOT NULL |
| `updated_at` | TIMESTAMPTZ | NOT NULL |

`is_active` lets an assignment be revoked (a practitioner stops working
in a department) without deleting the historical record.

**Cross-organization assignment is prevented at the DATABASE level**,
the same composite-FK technique as Section 4, applied twice:

- `(organization_id, practitioner_id) -> practitioners(organization_id, id)`
- `(organization_id, department_id) -> departments(organization_id, id)`

A practitioner from Organization A can never be assigned to a department
in Organization B — PostgreSQL rejects it outright (verified in
`tests/models/test_practitioner_department.py`).

`UNIQUE(organization_id, practitioner_id, department_id)` prevents a
duplicate assignment of the same practitioner to the same department.
`organization_id`'s inclusion here (rather than just
`UNIQUE(practitioner_id, department_id)`) is deliberate: it gives
`PractitionerAvailability` (Section 7) something to hold a THIRD
composite foreign key against.

## 6. Facility Relationship (No Direct `Practitioner.facility_id`)

`Practitioner` deliberately has **no** `facility_id` column. Because
`Department` already belongs to exactly one `Facility`, a practitioner's
facility association is derivable through department assignment
(`Practitioner -> PractitionerDepartment -> Department -> Facility`) —
adding a direct `Practitioner.facility_id` would create a second,
potentially conflicting ownership path (which facility does a
practitioner "belong to" if it differs from every department they're
assigned to?) without a concrete requirement driving it. A practitioner
working across multiple facilities is expected to do so through multiple
department assignments, each department anchoring one facility.

## 7. Recurring Availability

`app/models/practitioner_availability.py` — table
`practitioner_availability`. `PractitionerAvailability` represents a
**recurring weekly availability window** — e.g. "Practitioner X,
Cardiology, Monday, 09:00–12:00, Asia/Kolkata" — NOT a materialized
appointment slot and NOT tied to any specific calendar date. Turning this
into concrete, bookable slots is future work (Section 15).

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key, application-generated |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id` (`ON DELETE RESTRICT`), indexed |
| `practitioner_id` | UUID | NOT NULL, FK → `practitioners.id` (`ON DELETE RESTRICT`), indexed |
| `department_id` | UUID | NOT NULL, FK → `departments.id` (`ON DELETE RESTRICT`), indexed |
| `day_of_week` | VARCHAR(16) | NOT NULL, CHECK constrained to `DayOfWeek` |
| `start_time` | TIME | NOT NULL |
| `end_time` | TIME | NOT NULL, `CHECK (start_time < end_time)` |
| `timezone` | VARCHAR(64) | NOT NULL, validated as a real IANA timezone at the application layer |
| `is_active` | BOOLEAN | NOT NULL, default `true` |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL |

`DayOfWeek` (`enum.StrEnum`): `monday` through `sunday` — same
persistence strategy as every other enum in this codebase, chosen over a
raw integer specifically so the persisted/API value is self-describing.

**The `(practitioner_id, department_id)` pairing must already be an
existing assignment.** Enforced at the DATABASE level via a third
composite foreign key,
`(organization_id, practitioner_id, department_id) ->
practitioner_departments(organization_id, practitioner_id,
department_id)` (`fk_practitioner_availability_assignment`) — an
availability window can never reference a practitioner/department
combination that was never assigned. This does NOT, by itself, guarantee
the assignment is still ACTIVE (`PractitionerDepartment.is_active` is not
part of the composite key, since it's mutable) — that check is enforced
at the service level (`app.services.availability`), the same layered
pattern STORY-005 used for `Patient` <-> `User` linkage validity.

`practitioner_id`/`department_id` ALSO carry plain single-column foreign
keys to `practitioners`/`departments` respectively, in addition to the
three-column composite FK above. These are intentionally redundant for
constraint purposes (the composite FK already guarantees both reference
real, matching rows) — they exist because SQLAlchemy's ORM
`relationship()` needs an explicit, direct foreign-key path between two
tables to infer a join condition automatically, and the composite FK
alone points at `practitioner_departments`, a third table, not directly
at `practitioners`/`departments`.

## 8. Timezone Semantics

`timezone` is stored as a plain IANA timezone identifier string (e.g.
`"Asia/Kolkata"`), validated at the application layer against Python's
standard `zoneinfo.available_timezones()` — the identical strategy
`Facility.timezone` already uses (see
[DOMAIN_MODEL.md](DOMAIN_MODEL.md)). No giant database-level timezone
enum was created; `zoneinfo` (Python 3.9+ standard library, backed by the
system/tzdata IANA database) is the single source of truth for
validity. `start_time`/`end_time` are stored as plain wall-clock `TIME`
values with no timezone attached to the column itself — `timezone` is
what gives them meaning.

## 9. Overlapping Availability

For STORY-006, creating an availability window rejects it if it
overlaps an existing ACTIVE window for the same organization +
practitioner + department + day_of_week:

```
09:00–12:00
11:00–13:00   -> conflict (overlap)

09:00–12:00
12:00–15:00   -> allowed (adjacent, not overlapping)
```

Overlap uses a standard half-open interval comparison
(`new.start < existing.end AND existing.start < new.end`) — a window
ending exactly when another begins does not overlap it.

**Deliberate scope note**: overlap is checked only within the SAME
`(practitioner, department)` pairing and day — a practitioner with
availability in two DIFFERENT departments at overlapping times is not
rejected by this story. A real person obviously cannot be in two
departments simultaneously, but reconciling a practitioner's availability
*across* departments is a materially different (and larger) problem this
story deliberately does not attempt — see
[adr/ADR-0006-scheduling-resources.md](adr/ADR-0006-scheduling-resources.md).

### Concurrency Limitation

**Overlap rejection here is a SERVICE-LEVEL pre-check, not a
database-enforced exclusion constraint, and it is NOT race-proof.**
`AvailabilityService.create_availability` queries existing active
windows, compares them against the proposed window in Python, and only
then inserts. Two concurrent requests creating overlapping windows for
the same organization/practitioner/department/day could both pass the
pre-check before either commits, and both succeed — producing an overlap
the database itself does not reject (there is no PostgreSQL exclusion
constraint, e.g. `EXCLUDE USING gist`, on this table).

This is a **deliberate, documented scope boundary** for this story, not
an oversight: implementing a true race-proof exclusion constraint (which
would require the `btree_gist` extension and a `tsrange`/exclusion-
constraint schema redesign) was judged more infrastructure than this
story's administrative-resource-foundation scope justifies, given no
concurrent-write scenario has been demonstrated as a real operational
problem yet. Hardened, race-proof conflict prevention is deferred to a
future appointment/scheduling-hardening story if and when it's actually
needed — see Section 15.

## 10. Repository & Service Layers

Following the `Route -> Service -> Repository -> Session` pattern
established in STORY-005:

- **Repositories** (`app/repositories/department.py`,
  `app/repositories/practitioner.py`,
  `app/repositories/practitioner_department.py`,
  `app/repositories/availability.py`, plus a minimal
  `app/repositories/facility.py` added for `Department`'s ownership
  check): every business-facing read requires an explicit
  `organization_id` — there is no unscoped lookup for any tenant-owned
  resource in this story. `add`/`flush`/`query` only; never commit.
- **Services** (`app/services/department.py`, `app/services/practitioner.py`,
  `app/services/availability.py`) own transaction completion for mutating
  operations (`create_department`, `create_practitioner`,
  `assign_to_department`, `create_availability` each commit only once
  every validation check has passed) and hold the business rules a
  database constraint alone can't express (facility-ownership pre-checks
  for a clean 404, assignment-activity checks, time-range/timezone
  validation, overlap rejection).

## 11. API Routes

```
POST /api/v1/organizations/{organization_id}/departments
GET  /api/v1/organizations/{organization_id}/departments
GET  /api/v1/organizations/{organization_id}/departments/{department_id}

POST /api/v1/organizations/{organization_id}/practitioners
GET  /api/v1/organizations/{organization_id}/practitioners
GET  /api/v1/organizations/{organization_id}/practitioners/{practitioner_id}

POST /api/v1/organizations/{organization_id}/practitioners/{practitioner_id}/departments/{department_id}

POST /api/v1/organizations/{organization_id}/practitioners/{practitioner_id}/availability
GET  /api/v1/organizations/{organization_id}/practitioners/{practitioner_id}/availability
```

Every route requires a valid Bearer token and an active, database-
resolved `OrganizationMembership` (`get_current_membership`,
STORY-004/005) before any scheduling data is touched — `organization_id`
is never trusted from the JWT.

## 12. RBAC (Authorization Matrix)

| Action | ADMIN | STAFF | PATIENT |
|---|---|---|---|
| Create department | allowed | 403 | 403 |
| List/get department | allowed | allowed | 403 |
| Create practitioner | allowed | 403 | 403 |
| List/get practitioner | allowed | allowed | 403 |
| Assign practitioner → department | allowed | 403 | 403 |
| Create availability | allowed | allowed | 403 |
| List availability | allowed | allowed | 403 |

**STAFF may create availability** (setting up a practitioner's calendar
is treated as a day-to-day front-desk/scheduling operation, not an
administrative-structure change), but may NOT create departments,
practitioners, or assignments — those change the organization's
scheduling *structure*, not just a calendar within it.

**PATIENT may not create/modify/list/get any scheduling resource in this
story.** Patients will eventually need to *discover* departments,
practitioners, and availability to book an appointment — this story
deliberately does not build that discovery API yet. Exposing patient-
readable listing endpoints requires a genuinely safe projection (no
unnecessary practitioner PII, no cross-tenant leakage) driven by actual
booking-flow requirements, which don't exist until the appointment-
booking story does. Building it speculatively now, ahead of that
concrete need, would risk designing the wrong shape — see
[adr/ADR-0006-scheduling-resources.md](adr/ADR-0006-scheduling-resources.md).
This is an explicit, documented deferral, not accidental behavior.

**Resolved in STORY-007**: `GET .../practitioners/{id}/available-times`
is exactly the concrete, safe projection this deferral was waiting for —
a `PATIENT` caller can discover bookable TIMES for a specific, already-
known practitioner/department without any general-purpose "browse every
practitioner" endpoint. See [APPOINTMENTS.md](APPOINTMENTS.md) Sections
14-15 for the RBAC and privacy design. General-purpose department/
practitioner discovery (e.g. "list all Cardiology practitioners") is
still not implemented.

## 13. Safe Practitioner Response

`PractitionerResponse` (`app/schemas/practitioner.py`) exposes only:
`id`, `organization_id`, `first_name`, `last_name`, `practitioner_type`,
`is_active`, `created_at`, `updated_at`. There is no private email,
phone, home address, `User` identity/linkage, internal credential, or
employment-sensitive field anywhere on the `Practitioner` model in the
first place (Section 3) — data minimization enforced by what the model
doesn't contain, the same principle STORY-005 established for `Patient`.

## 14. Department Routing Safety

This story provides administrative department resources only — a
`Department` is nothing more than a named, coded scheduling bucket
belonging to a facility. A future agent may reasonably route an
administrative request such as *"book my cardiology follow-up"* to an
existing `Cardiology` department by name/code matching. It must **never**
assert or imply *"you have heart disease, therefore Cardiology"* — that
would be a clinical/diagnostic judgment, categorically outside this
project's scope (see [ARCHITECTURE.md](ARCHITECTURE.md) Section 7 and
[README.md](../README.md)). Nothing in this story implements any such
routing agent yet; this section exists to preserve the distinction for
whichever future story does.

## 15. Error Model

Application exceptions (`app.services.department`,
`app.services.practitioner`, `app.services.availability`), all mapped
through the existing global exception handling — never a raw
`IntegrityError`/driver exception reaching a client:

| Exception | Status | Meaning |
|---|---|---|
| `DepartmentNotFoundError` | 404 | No department matches, within the caller's own organization. |
| `FacilityNotFoundError` | 404 | The referenced facility doesn't exist within this organization (uniform whether truly nonexistent or belonging to another org). |
| `DepartmentCodeConflictError` | 409 | `code` already in use within this facility. |
| `PractitionerNotFoundError` | 404 | No practitioner matches, within the caller's own organization. |
| `PractitionerAlreadyAssignedError` | 409 | This practitioner is already assigned to this department. |
| `PractitionerNotAssignedError` | 422 | No active assignment exists for this practitioner/department pairing. |
| `InvalidAvailabilityTimeRangeError` | 422 | `start_time` is not before `end_time`. |
| `InvalidAvailabilityTimezoneError` | 422 | `timezone` is not a valid IANA identifier. |
| `AvailabilityOverlapError` | 409 | This window overlaps an existing active window (Section 9). |

## 16. Current vs. Planned

**Current (this story):** `Department`, `Practitioner`,
`PractitionerDepartment`, `PractitionerAvailability`; database-enforced
tenant/facility ownership integrity via composite foreign keys; the
full repository/service/API layers above; the documented RBAC matrix.

**Explicitly not implemented in this story** (later stories):
`Appointment`, appointment-slot materialization, booking, rescheduling,
cancellation, waitlists; patient-readable discovery endpoints for
departments/practitioners/availability; race-proof (database exclusion
constraint) overlap prevention; documents; `WorkflowRun`; any agent, LLM,
or LangGraph integration; clinical routing/diagnosis/treatment/
prescriptions of any kind; a frontend.

**Implemented in STORY-007** (see [APPOINTMENTS.md](APPOINTMENTS.md)):
`Appointment`, booking, rescheduling, cancellation; a patient-readable
available-times discovery endpoint (a safe projection, not general
department/practitioner browsing); and genuinely race-safe database
exclusion-constraint overlap prevention — for `Appointment`, NOT for
`PractitionerAvailability` itself, whose own overlap check (Section 9
above) remains a non-race-proof service-level pre-check, unchanged by
STORY-007. Still not implemented: appointment waitlists, documents,
`WorkflowRun`, any agent/LLM/LangGraph integration, and clinical
routing/diagnosis/treatment/prescriptions of any kind.
