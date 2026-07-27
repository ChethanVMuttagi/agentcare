# ADR-0006: Scheduling Resources (Department, Practitioner, Availability)

Status: Accepted

Date: 2026-07-27

## Context

STORY-005 (ADR-0005) established `Patient`, AgentCare's first real
tenant-owned domain resource, and the `Route -> Service -> Repository ->
Session` layering pattern. STORY-006 needed to establish the parallel
*administrative scheduling* side of the domain — the resources an
eventual appointment-booking story (and later agents) will query to
answer "who can see this patient, where, and when" — without building
appointments themselves yet.

Central questions this story had to resolve: what is the right core
vocabulary for a schedulable healthcare professional (`Doctor` is too
narrow)? How does a `Department`, which belongs to a `Facility`, avoid
being createable under the wrong `Organization`? How does a practitioner
relate to more than one department, and how is that relationship kept
tenant-safe? Does a practitioner need its own facility, given it already
reaches one through department assignment? How should recurring
availability be modeled without prematurely building appointment slots?
And, in a story explicitly about *scheduling* resources for a healthcare
product: how do we keep this administrative, not clinical?

## Decision

We will use:

1. **`Practitioner`, not `Doctor`, as the core domain concept.** A
   `PractitionerType` enum (`physician`, `physiotherapist`, `counselor`,
   `therapist`, `other`) covers the schedulable-healthcare-professional
   space without hardcoding the architecture around physicians
   specifically, and without encoding medical specialty into the type.
2. **`Department` belongs to exactly one `Facility`**, and that
   facility MUST belong to the same `Organization` as the department.
   Enforced at the DATABASE level via a composite foreign key
   `(organization_id, facility_id) -> facilities(organization_id, id)`,
   which required adding a composite unique constraint
   `uq_facilities_organization_id_id` to `Facility` for the FK to target.
3. **`Practitioner` <-> `Department` is many-to-many**, via a real model,
   `PractitionerDepartment` (not a bare association table), carrying its
   own `is_active` lifecycle flag and timestamps. Cross-organization
   assignment is prevented at the DATABASE level via two further
   composite foreign keys, the same technique as (2).
4. **No `Practitioner.facility_id`.** A practitioner's facility
   association is derived transitively through department assignment
   (`Practitioner -> PractitionerDepartment -> Department -> Facility`).
5. **Recurring availability, not materialized appointment slots.**
   `PractitionerAvailability` represents a weekly recurring rule
   ("Monday 09:00–12:00 in Cardiology"), not concrete bookable calendar
   slots tied to specific dates.
6. **Availability requires an existing practitioner-department
   assignment**, enforced at the DATABASE level via a third composite
   foreign key into `practitioner_departments`. Whether that assignment
   is currently ACTIVE is checked at the service level (mutable state a
   composite key can't naturally express).
7. **IANA timezone strings, validated via Python's `zoneinfo`** — same
   strategy as `Facility.timezone` (ADR predates this one; STORY-003).
   No custom timezone enum.
8. **Overlap policy**: reject overlapping ACTIVE availability windows
   for the same organization + practitioner + department + day of week;
   adjacent windows (one ending exactly when another begins) are
   allowed. Enforced as a SERVICE-LEVEL pre-check (query + compare +
   insert), explicitly NOT a race-proof database exclusion constraint.
9. **Appointment booking remains future work.** This story's resources
   (departments, practitioners, assignments, availability) are the
   foundation a later booking story will consume; no `Appointment`
   concept, slot materialization, or booking/cancellation flow exists
   here.
10. **Administrative routing is not diagnosis.** A future agent may
    route "book my cardiology follow-up" to an existing `Cardiology`
    department by name/code; it must never assert or imply a clinical
    judgment ("you have heart disease, therefore Cardiology"). This
    story builds no such agent, but the boundary is recorded here for
    whichever future story does.

## Rationale

- **`Practitioner` over `Doctor`**: hardcoding the architecture around
  physicians would require a disruptive rename/redesign the first time a
  physiotherapist, counselor, or therapist needed to be scheduled —
  which the product's own healthcare-administration scope (referrals,
  follow-ups, coordination across professional types) makes an early,
  foreseeable need, not a speculative one.
- **Composite-FK ownership integrity, not API-only validation**: the
  project's established discipline (ADR-0003, ADR-0005) already prefers
  database-level tenant-ownership enforcement over trusting every future
  caller to remember an application-level check. The composite-FK
  pattern proven here for `Department` (facility ownership) is reused
  identically for `PractitionerDepartment` (practitioner AND department
  ownership) and `PractitionerAvailability` (assignment existence) —
  establishing one consistent, auditable technique for "child of child
  must share the same tenant as its grandparent" across this entire
  story, rather than three different bespoke solutions.
- **Real `PractitionerDepartment` model, not a bare association table**:
  an assignment needs to be revocable (`is_active`) without destroying
  the historical record of "this practitioner once worked in this
  department" — a plain many-to-many join table with no lifecycle field
  can't represent that distinction. This mirrors why `Patient.user_id`
  linkage validity (STORY-005) also needed real row-level state, not
  just a join.
- **No `Practitioner.facility_id`**: adding one would create a second,
  independently-maintained ownership path that could disagree with the
  facilities implied by a practitioner's actual department assignments
  (which facility does a practitioner "belong to" if it differs from
  every department they work in?). Deriving facility through department
  assignment is the only design with a single source of truth, and
  costs nothing today since no query in this story needs
  practitioner-by-facility lookups directly.
- **Recurring availability, not materialized slots**: pre-generating
  concrete bookable slots (e.g. every 30-minute slot for the next N
  weeks) is exactly the kind of speculative complexity this project's
  stated discipline argues against building ahead of the story that
  actually needs it (appointment booking). A recurring rule is the
  minimal representation that answers "when is this practitioner
  generally available" without committing to a slot-generation strategy
  (fixed duration? variable? buffer time between appointments?) that
  hasn't been designed yet.
- **Assignment-existence at the database level, assignment-*activity*
  at the service level**: `PractitionerDepartment.is_active` is mutable
  independently of any availability row, so a composite FK/unique
  constraint that included it would need continuous re-validation on
  every assignment-status change — the same reasoning ADR-0005 used for
  why `Patient` linkage role-validity is a service check, not a DB
  constraint, applied consistently here.
- **`zoneinfo`, not a custom timezone table/enum**: Python's standard
  library already maintains the canonical, regularly-updated IANA
  timezone database; duplicating that as a hand-maintained enum would be
  both redundant and a maintenance liability (timezone rules and names
  do change over time).
- **Service-level, non-race-proof overlap check**: a true race-proof
  guarantee requires PostgreSQL's `btree_gist` extension and an
  exclusion-constraint schema redesign (`EXCLUDE USING gist`) — real,
  proven technology, but meaningfully more infrastructure than this
  story's administrative-resource-foundation scope justifies before any
  concurrent-write problem has actually been observed. The tradeoff is
  explicit and documented (`docs/SCHEDULING_RESOURCES.md` Section 9), not
  silently assumed away.
- **STAFF may create availability, not departments/practitioners**:
  distinguishes day-to-day scheduling operations (staff maintaining a
  practitioner's calendar) from administrative-structure changes
  (which departments/practitioners exist at all) — the latter is a more
  consequential, less frequent action appropriately reserved for
  `ADMIN`.
- **No patient-readable discovery endpoints yet**: exposing
  departments/practitioners/availability to `PATIENT`-role callers needs
  a genuinely safe projection driven by real booking-flow requirements
  that don't exist until the booking story does. Building that
  projection speculatively risks guessing the wrong shape and having to
  redesign it; deferring is explicit, not an oversight (the story
  instructions themselves frame this as optional for STORY-006).

## Alternatives Considered

- **`Doctor` as the core entity, generalized later if needed**: rejected
  — the generalization need (physiotherapists, counselors, therapists)
  is already implied by this product's stated administrative-coordination
  scope, not speculative; naming it correctly now avoids a disruptive
  rename.
- **API-only validation for department/assignment ownership** (no
  composite FKs): rejected per the story's explicit instruction and this
  project's established preference for database-enforced invariants
  where practical (ADR-0003, ADR-0005) — an API-only check can be
  bypassed by any future direct-DB code path (a script, a different
  service, a bug) that a database constraint cannot.
- **A bare `practitioner_departments` association table** (no model,
  no `is_active`): rejected — loses the ability to represent "no longer
  assigned" without deleting history, which the availability-assignment
  relationship (Section 7 of the resources doc) explicitly depends on.
- **`Practitioner.facility_id` as a direct column**: rejected — creates
  a second, conflicting ownership path; see Rationale.
- **Materialized appointment slots in this story**: rejected as
  explicitly out of scope; recurring availability is the correct,
  minimal foundation for a later booking story to build on.
- **A PostgreSQL exclusion constraint for overlap prevention now**:
  considered and deferred, not rejected outright — real added
  infrastructure (the `btree_gist` extension, a schema shape built around
  range types) that this story's scope doesn't yet justify; revisit if/
  when a booking story's concurrency requirements make the current
  service-level check demonstrably insufficient.
- **Exposing patient-readable listing endpoints now**: considered (the
  story instructions explicitly permitted it) and declined — see
  Rationale.

## Consequences

- Every future scheduling-adjacent table that needs "child belongs to
  the same tenant as its parent, transitively" should reuse the
  composite-FK pattern established here (`Department`, its unique-key
  extension for `Facility`), rather than reinventing ownership
  enforcement per-table.
- Because overlap prevention is not race-proof, a future
  appointment/scheduling-hardening story MUST explicitly decide whether
  to add a database-level exclusion constraint (or another race-proof
  mechanism) before this system is exposed to genuinely concurrent
  scheduling writes at a scale where the race is likely to matter. This
  is a known, accepted gap, not a hidden one.
- The first story that builds patient-facing discovery
  (departments/practitioners/availability search) must design that
  projection explicitly — deciding exactly which fields are safe to
  expose and how results are filtered/paginated for a patient caller —
  rather than simply reusing the ADMIN/STAFF response shapes as-is.
- The first story that builds `Appointment` inherits `Department`,
  `Practitioner`, `PractitionerDepartment`, and
  `PractitionerAvailability` as given, stable foundations; it should not
  need to alter their schemas to add booking, only to consume them.
- Any future agent that routes administrative scheduling requests
  ("book my cardiology follow-up" -> `Cardiology` department) must
  respect the administrative-routing-is-not-diagnosis boundary recorded
  here (`docs/SCHEDULING_RESOURCES.md` Section 14) — this ADR does not
  design that agent, only constrains it in advance.
