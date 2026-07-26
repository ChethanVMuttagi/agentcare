# ADR-0005: Patient Identity and Access

Status: Accepted

Date: 2026-07-26

## Context

STORY-004 (ADR-0004) established global `User` identity, per-organization
`OrganizationMembership`/`Role`, and backend-enforced RBAC primitives
(`get_current_membership`, `require_roles`) — but no route consumed them
yet, and no real business/domain resource existed for them to protect.
STORY-005 introduces `Patient`, AgentCare's first real, tenant-owned
healthcare-administration domain object, and the first production API
that must actually enforce tenant/role authorization end-to-end.

Central questions this story had to resolve: is a `Patient` the same
thing as a `User`? Does a patient administrative record require a portal
identity to exist at all? How is "this patient record belongs to this
organization" enforced so it can never be forgotten by a future route?
How does a patient-role user reach their own record without being able
to reach anyone else's? And, critically for a healthcare-administration
product: what must this model NOT contain.

## Decision

We will use:

1. **`User` and `Patient` are separate concepts.** `User` (STORY-004) is
   a global authentication identity. `Patient` is a healthcare
   organization's administrative record of a person, scoped to exactly
   one `Organization`. Neither implies the other.
2. **`Patient` belongs directly to `Organization`** — `organization_id`
   is a required, indexed, `ON DELETE RESTRICT` foreign key. Every
   patient read/write in the repository/service layer requires an
   explicit `organization_id`; there is no unscoped
   `get_by_id(patient_id)` shape to call by mistake.
3. **Optional portal `User` linkage** via `Patient.user_id` (nullable FK,
   `ON DELETE RESTRICT`). An organization may register a patient
   administratively before that person has, or ever gets, a portal
   identity.
4. **Organization-scoped `patient_number`**: `UNIQUE(organization_id,
   patient_number)`, not globally unique. Supplied by the authorized
   `ADMIN`/`STAFF` caller; no number-generation engine is built.
5. **Tenant-scoped repository and service** (`app.repositories.patient`,
   `app.services.patient.PatientService`) as the ONLY way this story's
   API reaches patient data — establishing the
   `Route -> Service -> Repository -> Session` pattern for the rest of
   the domain to follow.
6. **Patient self-access by linked User identity**: a `PATIENT`-role
   membership may retrieve only the `Patient` record where
   `Patient.user_id == <their own User.id>`, via a dedicated
   `GET .../patients/me` route — never by supplying an arbitrary patient
   UUID to a general lookup route.
7. **Admin/staff administrative access**: `ADMIN`/`STAFF` may create,
   list, and retrieve-by-id patient records within their own
   organization; a `PATIENT`-role membership may not.
8. **No clinical data in `Patient`.** The model holds identifier, name,
   date of birth, tenant/identity linkage, and status only — no
   diagnosis, symptoms, medication, treatment, clinical notes, insurance,
   or emergency-triage content.
9. **No update/delete yet.** Only create, get-by-id, list, and
   self-access are implemented this story.
10. **Data minimization**: `PatientResponse` exposes administrative
    fields only; no relationship objects, no internal DB state.

## Rationale

- **Separate `User`/`Patient`, optional linkage**: conflating the two
  would force every administrative patient record to have a portal
  login before it could exist, which does not match how healthcare
  organizations actually operate (patients are frequently registered by
  front-desk/admin staff well before, or entirely without, ever using a
  patient portal). Keeping them separate, with an optional link, matches
  reality without complicating the common case.
- **`organization_id` mandatory, repository has no unscoped read**: the
  explicit design goal (per this story's own instructions and
  ADR-0004's tenant-isolation direction) is that tenant scoping cannot be
  silently forgotten by a future route. Making every repository function
  signature require `organization_id` achieves this structurally — a
  reviewer, or a future contributor, cannot introduce an unscoped patient
  lookup without it being an obvious, deliberate change to the
  repository's own function signatures.
- **Organization-scoped `patient_number`, not global**: different
  healthcare organizations independently assign and manage their own
  patient identifiers (MRNs or equivalent); requiring global uniqueness
  would be both unrealistic (real-world identifier collisions across
  unrelated organizations are inevitable) and would leak information
  about identifiers assigned by unrelated tenants.
- **User-linkage validity enforced at the service level, not the
  database**: a database-level constraint (e.g. a composite foreign key
  or `CHECK` referencing `organization_memberships.role`) would need to
  be continuously re-validated every time a membership's role changes
  (an admin could be promoted/demoted independently of any patient row),
  which effectively requires a trigger — meaningfully more schema
  machinery than this story's scope justifies. A service-level check,
  run once at patient-creation time
  (`app.services.patient._validate_user_link`), keeps the invariant in
  one readable, testable place and matches this project's established
  preference for a minimal schema plus explicit application-level
  business rules (see ADR-0002, ADR-0003). The tradeoff, accepted here:
  if a linked user's membership role is later changed away from
  `Role.PATIENT` (or deactivated), the existing `Patient.user_id` link is
  NOT automatically un-linked or re-validated — this is a known,
  accepted gap for a future story to address if it becomes a real
  requirement, not a silent oversight.
- **`GET .../patients/me` as the only patient self-access path**: a
  general `GET .../patients/{id}` route reachable by a `PATIENT`-role
  membership would require checking, on every single request, whether
  the requested id happens to equal the caller's own linked patient —
  one missed check anywhere in that logic is a direct cross-patient data
  leak. A dedicated route that never accepts an arbitrary id as input
  removes that entire class of mistake structurally, rather than relying
  on a runtime comparison being written correctly and reviewed forever.
- **`ADMIN`/`STAFF` may also call `/patients/me`** rather than being
  blocked with a 403: the route only ever returns the CALLER'S OWN linked
  record, so there is nothing to gain or leak by allowing it — and by
  construction (linkage requires an active `Role.PATIENT` membership,
  previous point), an admin/staff caller will simply get 404. Adding a
  role check here would add code and a decision point without changing
  any actual access outcome.
- **No clinical data**: this story's explicit product/security boundary
  (see `docs/ARCHITECTURE.md` Section 7, `SECURITY.md`) is
  administration and care *coordination*, not the practice of medicine.
  A `Patient` model is exactly where "just one clinical field, just for
  convenience" scope creep would first appear; ruling it out explicitly,
  in this ADR, is a deliberate, documented boundary rather than an
  implicit one a future story could quietly erode.
- **No update/delete yet**: create + read covers this story's actual
  requirement (registering and looking up administrative patients).
  Update/delete introduce their own authorization and auditability
  questions (who may edit a patient's name? does a delete need to be
  soft, given downstream references a later story might add?) that
  deserve their own deliberate design once there's a concrete need,
  rather than being bundled in speculatively now.

## Alternatives Considered

- **A single `organization_id` + `patient_number` combined "smart" key**
  used as the primary key instead of a UUID: rejected — every other
  entity in this codebase uses an application-generated UUID primary key
  (see `docs/DATABASE.md`); a composite natural key would be an
  inconsistent, harder-to-reference exception for no compelling benefit.
- **Requiring every `Patient` to have a linked `User`**: rejected — does
  not match real-world registration workflows (Rationale above), and
  this story explicitly requires `user_id` to be optional.
- **A database trigger or composite foreign key enforcing "linked user's
  membership role must be `Role.PATIENT`"**: rejected as premature
  schema complexity for this story; deferred to a future story if
  service-level enforcement proves insufficient in practice (see
  Rationale).
- **Allowing a `PATIENT`-role membership to call `GET .../patients/{id}`
  with an equality check against their own linked id**: rejected in
  favor of a dedicated `/me` route — see Rationale; the equality-check
  design is one missed line away from a cross-patient data leak, the
  dedicated-route design cannot leak by construction.
- **Blocking `ADMIN`/`STAFF` from `/patients/me` with a role check**:
  considered and rejected as unnecessary — see Rationale.
- **Global (not organization-scoped) `patient_number` uniqueness**:
  rejected — unrealistic given independently-operated organizations, and
  would leak cross-tenant identifier information via conflict errors.
- **Update/delete endpoints in this story**: rejected as scope creep
  beyond what was asked; deferred to a future story.

## Consequences

- Every future patient-adjacent entity (e.g. a later `Appointment` tied
  to a `Patient`) should follow the same
  `Route -> Service -> Repository -> Session` layering and the same
  "every repository function requires `organization_id`" discipline
  established here, rather than reverting to ad hoc query logic in
  routes.
- If a linked user's role/membership changes after linkage, the
  `Patient.user_id` link does not automatically react (Rationale) — a
  future story must decide explicitly whether/how to reconcile this
  (e.g. re-validate on every read, or require an explicit unlink step)
  if it becomes a real operational concern.
- The first story that adds patient update/delete must design its own
  authorization rules for those operations (e.g. can a `PATIENT`-role
  membership edit their own record's name? almost certainly a separate,
  deliberate decision) — this ADR does not pre-decide that.
- The first story that adds any clinical/medical content anywhere in the
  domain model must treat that as a new, explicit boundary-crossing
  decision (its own ADR, matching `docs/ARCHITECTURE.md` Section 7), not
  an incremental field addition to `Patient`.
