# AgentCare Identity, Authentication & Authorization (RBAC)

This document describes the identity, authentication, and authorization
foundation implemented in STORY-004, and how STORY-005/006/007/008 put it
to work against real domain resources (`Patient` — see
[PATIENTS.md](PATIENTS.md); `Department`/`Practitioner`/availability —
see [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md); `Appointment` —
see [APPOINTMENTS.md](APPOINTMENTS.md); `PatientDocument` — see
[DOCUMENTS.md](DOCUMENTS.md)). It follows
the same CURRENT vs. PLANNED discipline as [ARCHITECTURE.md](ARCHITECTURE.md):
everything described here as implemented exists in the repository today;
anything marked PLANNED does not yet.

This is security-critical infrastructure — see
[adr/ADR-0004-identity-and-authorization.md](adr/ADR-0004-identity-and-authorization.md),
[adr/ADR-0005-patient-identity-and-access.md](adr/ADR-0005-patient-identity-and-access.md),
and
[adr/ADR-0006-scheduling-resources.md](adr/ADR-0006-scheduling-resources.md)
for the decision records.

## 1. Identity Model: User vs. OrganizationMembership

Identity is deliberately split into two entities:

- **`User`** (`app/models/user.py`) — a **global** identity: email +
  password hash + active flag. A `User` is not scoped to any
  organization.
- **`OrganizationMembership`** (`app/models/membership.py`) — associates
  one `User` with one `Organization` (see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md)) and carries a `Role` (Section 2)
  and its own `is_active` flag.

**Why not put `organization_id` directly on `User`**: a user may belong
to multiple organizations (e.g. a clinician consulting for more than one
practice, or a patient with records at more than one facility). A single
`organization_id` column on `User` could only ever represent one
organization at a time. `OrganizationMembership` is the many-to-many join
between identity and tenant — enforced with `UNIQUE(organization_id,
user_id)` (at most one membership per user per organization) — see
[DOMAIN_MODEL.md](DOMAIN_MODEL.md) and
[DATABASE.md](DATABASE.md) for the schema.

## 2. Roles

`Role` (`app/models/membership.py`, `enum.StrEnum`) — deliberately small:

| Role | Meaning |
|---|---|
| `admin` | Organization-level administrative access. |
| `staff` | The healthcare organization's administrative/operational staff. |
| `patient` | Patient-facing access — constrained to the patient's own linked record (Section 10; see [PATIENTS.md](PATIENTS.md)). |

Persisted the same way as `OrganizationType`/`FacilityType`
([DOMAIN_MODEL.md](DOMAIN_MODEL.md) "Enum Strategy"): `VARCHAR` + a real
database `CHECK` constraint (`native_enum=False`,
`create_constraint=True`), not a native PostgreSQL `ENUM` — so adding a
future role is an ordinary migration, not an `ALTER TYPE`.

**Role is one input into authorization, not the complete decision.** A
`PATIENT`-role membership having "patient-facing access" does not, by
itself, define exactly what that membership can reach — self-access rules
(Section 10; a patient can only see their *own* linked record) further
constrain access beyond what the role alone implies. `require_roles`
(Section 6) checks role membership; it does not, and will not, become the
entire authorization system.

## 3. Authentication

### Password hashing (`app/auth/security.py`)

Argon2id via `argon2-cffi`'s `PasswordHasher` — never implemented by
hand. Salting is handled entirely by the library (a random salt per
hash, embedded in the stored hash string). Plaintext passwords are never
persisted or logged anywhere; only `hash_password()`'s output
(`User.password_hash`) is ever stored.

### JWT access tokens (`app/auth/jwt.py`)

`PyJWT` — never implemented by hand. `create_access_token(user_id,
settings)` issues a token with exactly four claims:

| Claim | Meaning |
|---|---|
| `sub` | The user's UUID, as a string. |
| `iat` | Issued-at time. |
| `exp` | Expiry time (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` after issuance, default 30). |
| `jti` | A random, per-token identifier. |

**Deliberately excluded**: email, password hash, role, organization
membership, permissions, or any patient/medical data. A token identifies
*who* the user is and nothing else — see Section 4.

### The authentication service (`app/auth/service.py`)

`authenticate_user(session, email, password)` is the only place that
turns "email + password" into "a User, or not." It returns `None`
uniformly for three different situations — no such email, wrong
password, or an inactive user — specifically so callers cannot
distinguish which case applies (Section 7). A dummy password-hash
verification runs even when no matching user exists, to keep the
response-time cost roughly similar to a real wrong-password check
(reduces, does not eliminate, user-enumeration via timing).

## 4. The JWT Trust Boundary

**A JWT identifies WHO the user is. It is never trusted for organization
membership or role.**

Concretely: `get_current_user` (`app/auth/dependencies.py`) resolves a
`User` from a token's `sub` claim — nothing more. Organization membership
and role are **always re-resolved from the database, fresh, on every
request** via `get_current_membership`, which queries
`OrganizationMembership` directly by `(organization_id, user_id)`. A
token cannot carry a stale or forged role or membership, because it never
carries one at all — there is no `role` or `organization_id` claim to
trust or mistrust in the first place.

This means: if an admin's role is downgraded to staff, or their
membership in an organization is deactivated, that change takes effect
on their *very next request* — not after their current token expires.
There is no cached/stale authorization state to invalidate.

## 5. Tenant Context

A request that operates on a specific organization must establish, in
this order:

1. **Authenticated user** — `get_current_user` (Bearer token → `User`).
2. **Target organization** — an `organization_id`, which
   `get_current_membership` accepts as a plain parameter. FastAPI binds
   this automatically from a route's `{organization_id}` path parameter
   (sub-dependencies inherit path parameters by name) — every
   organization-scoped route added so far (`patients.py`,
   `departments.py`, `practitioners.py`) relies on exactly this
   mechanism (Section 8).
3. **Active membership** — `get_current_membership` queries the database
   for `(organization_id, user_id)` and requires `is_active=True`.
4. **Persisted role** — read directly off the resolved
   `OrganizationMembership` row.

There is **no global mutable tenant state** anywhere in this
implementation — no thread-local, no module-level "current organization"
variable, no context that could leak between concurrent requests.
Tenant context is entirely a function of (token → user) + (path
organization_id) + (a fresh database query), resolved independently on
every single request.

## 6. RBAC (Role-Based Access Control)

`require_roles(*allowed_roles)` (`app/auth/dependencies.py`) is a
dependency **factory**: it returns a FastAPI dependency that first
enforces `get_current_membership` (so an inactive/missing membership is
rejected before any role check happens), then checks the resolved
membership's role against `allowed_roles`.

```python
Depends(require_roles(Role.ADMIN))
Depends(require_roles(Role.ADMIN, Role.STAFF))
```

This exists specifically so role checks are never hand-rolled
independently inside individual route functions — a route declares its
required role(s) once, in its dependency list, and the same, tested logic
enforces it everywhere.

**Wired into real routes as of STORY-005/006**:
`app/api/v1/endpoints/patients.py`, `departments.py`, and
`practitioners.py` — see Section 8 and [PATIENTS.md](PATIENTS.md)
Section 8 / [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md)
Section 12 for the full authorization matrices.

## 7. 401 vs. 403

| Status | Meaning | Raised by |
|---|---|---|
| **401** (`AuthenticationError`) | Authentication itself failed: missing/malformed `Authorization` header, invalid/expired/wrong-signature token, a token for a user that no longer exists, or an **inactive user**. | `get_current_user` |
| **403** (`AuthorizationError`) | The user *is* authenticated, but isn't authorized: no membership in the target organization, an **inactive membership**, or (via `require_roles`) a membership whose role isn't in the allowed set. | `get_current_membership`, `require_roles` |

**Cross-tenant information disclosure**: `get_current_membership` returns
the *same* 403 whether the membership is simply missing, is inactive, or
the organization itself doesn't exist. A non-member cannot use the
response to learn "that organization doesn't exist" vs. "it exists but
you're not in it" vs. "you were removed from it" — all three look
identical from the outside. The same applies to login (Section 3):
unknown-email and wrong-password responses are identical in shape
(status code and body), and tested to be so
(`tests/api/test_auth_endpoints.py`).

## 8. Auth API

Deliberately minimal — proving the authentication path end-to-end, not a
full user-management API:

- **`POST /api/v1/auth/token`** — accepts `{email, password}`
  (`TokenRequest`), authenticates against the persisted `User`, returns
  `{access_token, token_type: "bearer"}` (`TokenResponse`) on success, or
  a generic 401 (`invalid_credentials`) otherwise — never revealing
  whether the email exists.
- **`GET /api/v1/auth/me`** — requires a valid Bearer token, returns the
  authenticated user's own profile (`CurrentUserResponse`: `id`, `email`,
  `is_active`, `created_at`). Never returns `password_hash`.

**No CRUD API for `Organization`/`Facility` exists** (see
[DOMAIN_MODEL.md](DOMAIN_MODEL.md)). `get_current_membership` and
`require_roles` were fully implemented and tested
(`tests/auth/test_dependencies.py`) in STORY-004 by calling them
directly, the same way `app.db.session.get_db_session` was built and
tested in STORY-002 before any route consumed it — the patient API
(STORY-005) was the first organization-scoped route to actually depend
on them; the department/practitioner/availability APIs (STORY-006)
followed the same pattern. See [PATIENTS.md](PATIENTS.md) Section 8 and
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 12 for those
authorization matrices.

**Not implemented** (explicitly out of scope for this story): public
registration, password reset, email verification, refresh tokens, MFA,
OAuth/social login, invitation workflows.

## 9. Token Revocation Limitation

**Access tokens are stateless.** There is no server-side session or
revocation store in this story. Concretely, this means:

- A token, once issued, remains valid until it expires — there is no way
  to invalidate it early.
- **Logout does not invalidate an already-issued token** (no logout
  endpoint exists in this story at all — do not assume one exists or
  would revoke anything).
- Deactivating a user (`is_active=False`) *is* effective immediately —
  `get_current_user` checks `is_active` on every request — but a still-
  active user's outstanding tokens remain valid until they individually
  expire.
- `jti` (a random, unique identifier) is included in every token
  specifically so a **future** revocation/session-control mechanism (a
  denylist keyed by `jti`, or a session table) has something to key off
  of. Nothing currently reads or checks `jti` — it is present, unused,
  by design, waiting for that later story.
- The only mitigation available today is **short-lived access tokens**
  (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, default 30 minutes) — a leaked
  token's exposure window is bounded by this, not by any revocation
  capability.

## 10. Patient Self-Access (CURRENT, STORY-005)

Implemented via a dedicated route, `GET /api/v1/organizations/{organization_id}/patients/me`
(`app/api/v1/endpoints/patients.py`), rather than a general-purpose
patient-lookup route with an ownership check layered on top:

- Requires only `get_current_user` + `get_current_membership` (any active
  role) — NOT `require_roles(Role.PATIENT)`. It returns exclusively "the
  `Patient` record where `Patient.user_id == <the caller's own User.id>`,
  within this organization" (`app.services.patient.get_own_patient_record`),
  so there is nothing an `ADMIN`/`STAFF` caller could gain by calling it;
  by construction (Section 2; [PATIENTS.md](PATIENTS.md) Section 4)
  they will simply get a 404, since patient linkage requires an active
  `Role.PATIENT` membership.
- A `PATIENT`-role membership cannot use `GET .../patients/{id}` (the
  general lookup route) at all — that route requires `ADMIN`/`STAFF`
  (`require_roles`). Knowing a patient UUID never grants access on its
  own; a dedicated, id-less self-access route removes the entire class of
  mistake a runtime "is this the caller's own record?" equality check
  could introduce.

See [PATIENTS.md](PATIENTS.md) Sections 8–10 and
[adr/ADR-0005-patient-identity-and-access.md](adr/ADR-0005-patient-identity-and-access.md)
for the full design and rationale.

## 11. Future Role/Permission Evolution (PLANNED)

STORY-004 deliberately does not build a many-to-many permission engine
(explicit instruction, and consistent with "keep this story deliberately
small"). `Role` is a small, closed, enum-backed set (Section 2). If
AgentCare's authorization needs eventually outgrow "one role per
membership, checked against an allow-list" — e.g. needing fine-grained,
independently-grantable permissions — that would be a new, deliberate
architectural decision (its own ADR), not an incremental patch to
`Role`. Nothing in this story precludes that evolution; nothing in this
story assumes it either.

## 12. Current vs. Planned

**Current:** `User`, `OrganizationMembership`, `Role` (STORY-004);
Argon2id password hashing; stateless JWT access tokens; `get_current_user`
(401 on failure); `get_current_membership`/`require_roles`
(403 on failure, DB-authoritative membership/role); `POST /auth/token`,
`GET /auth/me`; `Patient` and patient self-access (STORY-005); `Department`,
`Practitioner`, practitioner-department assignment, and recurring
availability, all RBAC-enforced (STORY-006 — see
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md)); `Appointment`
booking, rescheduling, and cancellation, RBAC-enforced with patient
self-service and server-derived (never client-trusted) patient identity
(STORY-007 — see [APPOINTMENTS.md](APPOINTMENTS.md) Sections 15-16);
`PatientDocument` upload/list/get/download, RBAC-enforced with the same
server-derived patient identity, plus ADMIN/STAFF-only deletion
(STORY-008 — see [DOCUMENTS.md](DOCUMENTS.md) Sections 15-16);
`WorkflowRun` creation/list/get/steps/events, RBAC-enforced with the
same server-derived patient identity, plus ADMIN/STAFF-only
cancellation (STORY-009 — see [WORKFLOWS.md](WORKFLOWS.md) Sections
20-21).

**Explicitly not implemented** (belong to later stories): appointment
completion workflow, malware scanning, a production document-storage
backend, an LLM client, an agent framework, LangGraph, tool calling,
autonomous workflow decision-making, refresh tokens, password reset,
email verification, MFA, OAuth/social login, public registration, a
general-purpose security/compliance audit-event system (distinct from
`WorkflowEvent`'s own workflow-lifecycle audit trail — see
[WORKFLOWS.md](WORKFLOWS.md) Section 18), patient update/delete,
general-purpose patient-readable department/practitioner discovery
endpoints (as opposed to the available-times endpoint, which IS
implemented — see [APPOINTMENTS.md](APPOINTMENTS.md)), finer-grained
permissions beyond `Role` (Section 11), and any CRUD API for
`Organization`/`Facility` themselves.
