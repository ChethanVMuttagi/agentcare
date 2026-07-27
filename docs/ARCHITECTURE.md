# AgentCare Architecture

This document describes AgentCare's backend architecture as of STORY-006
(Department, Practitioner & Availability Foundation), building on
STORY-005 (Patient Domain, Self-Access & Tenant-Safe API), STORY-004
(Identity, Membership & RBAC Foundation), STORY-003 (Organization &
Facility Tenancy Foundation), STORY-002 (PostgreSQL & Database
Foundation), and STORY-001 (Architecture & Python Backend Foundation). It
clearly separates **CURRENT** (what exists in the repository today) from
**PLANNED** (direction only — not implemented). See
[README.md](../README.md) for the same distinction applied to the
project as a whole, [DATABASE.md](DATABASE.md) for the full
database-layer detail, [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the domain
model itself, [RBAC.md](RBAC.md) for the full identity/authentication/
authorization model, [PATIENTS.md](PATIENTS.md) for the patient domain,
and [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) for the
department/practitioner/availability domain this story adds — this
document only summarizes all five.

## 1. Architectural Principles

- **Build what the current story needs, not what a future story might.**
  Layers, abstractions, and dependencies are introduced when a story
  requires them, not speculatively ahead of time.
- **Truthful status over impressive-looking scaffolding.** Health/readiness
  endpoints, documentation, and configuration report what is actually true
  of the running system — no faked dependency checks, no claimed
  capabilities that don't exist yet.
- **Security and safety boundaries are structural, not incidental.** Where
  a boundary matters (e.g. agents never touching the database directly,
  secrets never being logged), it is enforced by the shape of the code, not
  left to convention alone.
- **Configuration over hardcoding.** Environment-dependent behavior is
  driven by typed settings, not scattered string comparisons or hardcoded
  values.

## 2. Current Architecture (STORY-001 through STORY-006)

The backend is a single Python package (`backend/app/`) — a **modular
monolith**:

```
backend/app/
├── main.py              # Application factory: create_app() -> FastAPI
│                         # + lifespan (disposes the DB engine on shutdown)
├── api/v1/
│   ├── router.py         # Aggregates versioned API routes
│   └── endpoints/
│       ├── auth.py           # POST /auth/token, GET /auth/me
│       ├── health.py         # GET /api/v1/health, GET /api/v1/ready
│       ├── patients.py       # POST/GET .../patients, .../patients/{id}, .../patients/me
│       ├── departments.py    # POST/GET .../departments, .../departments/{id}
│       └── practitioners.py  # POST/GET .../practitioners(/{id}), assignment, availability
├── auth/
│   ├── security.py       # hash_password / verify_password (Argon2id)
│   ├── jwt.py             # create_access_token / decode_access_token (PyJWT)
│   ├── service.py         # authenticate_user (email+password -> User | None)
│   └── dependencies.py    # get_current_user, get_current_membership,
│                           # require_roles — see RBAC.md
├── core/
│   ├── config.py         # Settings (pydantic-settings), Environment enum
│   ├── logging.py        # Logging configuration
│   └── exceptions.py     # AppException base + global exception handlers
├── db/
│   ├── base.py            # Declarative Base (+ naming convention)
│   ├── session.py         # Async engine/session lifecycle, get_db_session
│   ├── health.py          # Real SELECT 1 connectivity check
│   ├── mixins.py          # UUIDPrimaryKeyMixin, TimestampMixin
│   └── types.py           # enum_values() — shared Enum column helper
├── models/
│   ├── organization.py              # Organization, OrganizationType
│   ├── facility.py                  # Facility, FacilityType
│   ├── user.py                       # User, normalize_email()
│   ├── membership.py                 # OrganizationMembership, Role
│   ├── patient.py                    # Patient, normalize_person_name()
│   ├── department.py                 # Department
│   ├── practitioner.py               # Practitioner, PractitionerType
│   ├── practitioner_department.py    # PractitionerDepartment (assignment)
│   └── practitioner_availability.py  # PractitionerAvailability, DayOfWeek
├── repositories/
│   ├── patient.py               # Tenant-scoped persistence/query only — see PATIENTS.md
│   ├── facility.py              # Minimal: Department's ownership pre-check only
│   ├── department.py
│   ├── practitioner.py
│   ├── practitioner_department.py
│   └── availability.py
├── services/
│   ├── patient.py               # PatientService — business rules + transaction ownership
│   ├── department.py            # DepartmentService
│   ├── practitioner.py          # PractitionerService (incl. department assignment)
│   └── availability.py          # AvailabilityService (assignment/time/timezone/overlap rules)
└── schemas/
    ├── common.py          # ErrorResponse, HealthResponse, ReadinessResponse
    ├── auth.py             # TokenRequest, TokenResponse, CurrentUserResponse
    ├── patient.py           # PatientCreate, PatientResponse, PatientListResponse
    ├── department.py        # DepartmentCreate, DepartmentResponse, DepartmentListResponse
    ├── practitioner.py      # PractitionerCreate/Response/ListResponse, assignment response
    └── availability.py      # AvailabilityCreate, AvailabilityResponse, AvailabilityListResponse
```

Plus, under `backend/` alongside `app/`: `alembic.ini` and `migrations/`
(Alembic migration infrastructure — see [DATABASE.md](DATABASE.md)).

What exists today:
- A FastAPI application built via an **application factory**
  (`create_app()`), not constructed at import time, so tests can create
  independent app instances. A **lifespan** handler disposes the database
  engine's connection pool cleanly on shutdown.
- A versioned API mounted under `/api/v1`, exposing health and readiness
  endpoints — readiness reflects real database connectivity (see Section
  6 and [DATABASE.md](DATABASE.md)).
- Typed, environment-driven configuration (`Settings`), including
  `DATABASE_URL` (optional, `SecretStr`). LLM provider fields remain
  optional configuration only — no LLM integration exists yet.
- **Async SQLAlchemy 2.x engine/session management** (`app/db/session.py`):
  a lazily-created, cached engine (PostgreSQL via `asyncpg` in
  production), a session factory, and a `get_db_session()` FastAPI
  dependency that creates, yields, and closes a session without
  auto-committing. No route consumes it yet — no service/repository layer
  exists to call it (Section 3).
- **A real database connectivity check** (`app/db/health.py`): an actual
  `SELECT 1`, never a faked result, wired into `/api/v1/ready`.
- **Alembic migration infrastructure** (`backend/alembic.ini`,
  `backend/migrations/`), reading `DATABASE_URL` from `Settings` at
  runtime rather than embedding credentials, with `target_metadata`
  pointed at `app.db.base.Base.metadata` via a clean `app.models` package
  import (`migrations/env.py`). **One real migration exists**: it creates
  the `organizations` and `facilities` tables — validated end-to-end
  (upgrade, downgrade, re-upgrade) against real PostgreSQL.
- **The first real domain models** (`app/models/organization.py`,
  `app/models/facility.py` — STORY-003): `Organization` (AgentCare's
  tenant boundary) and `Facility` (belongs to exactly one Organization).
  See [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the full model, and
  ADR-0003 for the tenancy decision. Nothing queries or writes these
  tables yet outside of tests — there is no CRUD API, service, or
  repository layer for them (Section 3).
- **Identity, membership, and backend-enforced RBAC** (`app/models/user.py`,
  `app/models/membership.py`, `app/auth/` — STORY-004): a global `User`
  identity, `OrganizationMembership` (user + organization + `Role`,
  `UNIQUE(organization_id, user_id)`), Argon2id password hashing,
  stateless JWT access tokens (`sub`/`iat`/`exp`/`jti` only),
  `get_current_user` (401 on failure), and `get_current_membership`/
  `require_roles` (403 on failure, always re-resolved from the database,
  never trusted from the token). A minimal auth API exists:
  `POST /api/v1/auth/token`, `GET /api/v1/auth/me`. See
  [RBAC.md](RBAC.md) for the full model and ADR-0004 for the decision
  record.
- **The first tenant-scoped domain resource, repository, service, and
  protected API** (`app/models/patient.py`, `app/repositories/patient.py`,
  `app/services/patient.py`, `app/api/v1/endpoints/patients.py` —
  STORY-005): `Patient`, an ADMINISTRATIVE patient record belonging to
  exactly one `Organization`, optionally linked to a `User`. This is the
  first route that actually depends on `get_current_membership`/
  `require_roles` (Section 4 established the pattern; this story
  consumes it) — every patient read/write requires an explicit
  `organization_id`, and there is no unscoped repository function that a
  route could call by mistake. Also the first real mutating-transaction
  pattern (`Route -> Service -> Repository -> Session`, with the service
  owning commit/no-commit — Section 4). See [PATIENTS.md](PATIENTS.md)
  for the full model and ADR-0005 for the decision record.
- **The administrative scheduling-resource foundation**
  (`app/models/department.py`, `practitioner.py`,
  `practitioner_department.py`, `practitioner_availability.py`; their
  repositories and services; `app/api/v1/endpoints/departments.py`,
  `practitioners.py` — STORY-006): `Department` (belongs to a `Facility`,
  which must share its `Organization`), `Practitioner` (a schedulable
  healthcare professional, deliberately not named `Doctor`),
  `PractitionerDepartment` (many-to-many assignment), and
  `PractitionerAvailability` (recurring weekly windows, not materialized
  appointment slots). Tenant/facility/assignment ownership is enforced at
  the DATABASE level via composite foreign keys (not just application
  validation) — see [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md)
  and ADR-0006. This story does not implement appointments.
- A structured logging foundation (level, timestamp, logger name, message)
  with an explicit rule against logging secrets or patient data — this
  now explicitly includes never logging `JWT_SECRET_KEY`, issued tokens,
  plaintext passwords, `PatientCreate` payloads, or practitioner/patient
  names beyond what's operationally necessary (see
  [SECURITY.md](../SECURITY.md)).
- A standardized error response shape and global exception handling that
  never leaks stack traces or internal detail to clients — this also
  covers database errors (never exposing connection strings or driver
  exception text via the API), authentication/authorization failures
  (never revealing whether an email or organization exists — see
  [RBAC.md](RBAC.md) Section 7), patient lookups (never revealing
  that a patient UUID exists under a different organization — see
  [PATIENTS.md](PATIENTS.md) Section 10), and department/practitioner
  lookups (same non-disclosure principle — see
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 15).

What does **not** exist yet: a repository or service layer for
`Organization`/`Facility` themselves; any entity beyond `Organization`/
`Facility`/`User`/`OrganizationMembership`/`Patient`/`Department`/
`Practitioner`/`PractitionerDepartment`/`PractitionerAvailability`; any
CRUD API for `Organization`/`Facility`; patient update/delete;
`Appointment` or any booking/rescheduling/cancellation flow; a
race-proof (database exclusion constraint) availability-overlap check;
patient-readable scheduling discovery endpoints; any LLM call; any
agent; any frontend; public user registration; password reset; email
verification; refresh tokens; or MFA.

## 3. Planned Architecture (NOT Implemented)

Everything in this section is direction, not current behavior.

- **A service/repository layer for `Organization`/`Facility` themselves**
  — STORY-005/006 established the `Route -> Service -> Repository ->
  Session` pattern for `Patient` and the scheduling resources, but
  `Organization`/`Facility` still have no service or CRUD API of their
  own (only the minimal `app/repositories/facility.py` read used by
  `DepartmentService`'s ownership check); STORY-003 established
  persistence only.
- **`Appointment` and appointment-slot materialization** — `Department`,
  `Practitioner`, `PractitionerDepartment`, and
  `PractitionerAvailability` (STORY-006) are the foundation a future
  booking story will consume; booking, rescheduling, cancellation, and
  waitlists don't exist yet — see
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 16.
- **Race-proof availability-overlap prevention** (a PostgreSQL exclusion
  constraint) — the current check is a documented, non-race-proof
  service-level pre-check — see
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 9.
- **Patient-readable scheduling discovery** (departments/practitioners/
  availability search) — deliberately deferred until a concrete
  booking-flow need defines the safe projection — see
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 12.
- **Further healthcare domain models** below the tenant hierarchy
  (referrals, staff profiles, documents, etc.), as SQLAlchemy 2.x
  `Mapped[...]` classes subclassing `app.db.base.Base` — see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 19.
- **Patient update/delete** — only create, get-by-id, list, and
  self-access exist (STORY-005) — see [PATIENTS.md](PATIENTS.md)
  Section 13.
- **Finer-grained permissions** beyond the current closed `Role` enum
  (`admin`/`staff`/`patient`), refresh tokens, and token
  revocation/session control — see [RBAC.md](RBAC.md) Sections 9 and 11.
- **LangGraph-based agent workflows** for coordination tasks (scheduling,
  intake, referrals, follow-ups), invoked through the service layer. Any
  future administrative-routing agent (e.g. matching "book my cardiology
  follow-up" to a `Department`) must respect the
  administrative-routing-is-not-diagnosis boundary — see
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 14.
- **An LLM provider abstraction** so agents/workflows are not hardcoded to
  a single vendor (Groq / OpenAI / Anthropic).
- **Docker** packaging for consistent local/dev/prod environments.
- **A Next.js frontend** consuming the versioned API.

## 4. Backend Layering Philosophy

The intended request flow, once every layer is built out, is:

```
Route (API layer)
  → Service (business rules / orchestration / transaction ownership)
    → Workflow (LangGraph, for agentic coordination tasks)
      → Agent (a bounded, tool-using participant in a workflow)
        → Tool (a narrow, explicit capability an agent is allowed to invoke)
    → Repository (data access abstraction)
      → Database (PostgreSQL)
```

**`Route → Service → Repository → Database` is CURRENT as of STORY-005**,
first proven for the patient domain (`app/api/v1/endpoints/patients.py`
→ `app.services.patient.PatientService` → `app.repositories.patient` →
`AsyncSession`), **and reused unchanged for the scheduling-resource
domain in STORY-006** (`departments.py`/`practitioners.py` →
`DepartmentService`/`PractitionerService`/`AvailabilityService` → their
respective repositories) — now demonstrated across two independent
domains, the pattern every future domain resource is expected to
follow. `Workflow`/`Agent`/`Tool` remain PLANNED (Section 3) — no
agentic coordination exists yet.

Routes stay thin: they parse/validate input, call a service, and shape the
response. Services hold business rules AND own the transaction boundary
for mutating operations (`PatientService.create_patient`,
`DepartmentService.create_department`,
`PractitionerService.create_practitioner`/`assign_to_department`,
`AvailabilityService.create_availability` all commit only once every
check passes; repositories only ever add/flush — see
[PATIENTS.md](PATIENTS.md) Section 6,
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 10, and
[DATABASE.md](DATABASE.md) "Transaction Ownership Philosophy"). Once
workflows exist, services will also decide whether a task needs one at
all. Workflows will orchestrate multi-step agent coordination. Agents
will never bypass their tools to act directly on the system.

## 5. Why Agents Will Not Directly Access the Database

When agent workflows are introduced, agents will interact with data
exclusively through explicit, narrow **tools** — never through a direct
database session or repository call. Reasons this boundary is planned as
structural, not just a convention:

- **Auditability**: a tool call is a discrete, loggable, reviewable action.
  An agent with a raw database session can perform arbitrary, hard-to-audit
  operations.
- **Blast radius**: a tool exposes only the specific operations an agent
  needs (e.g. "look up appointment status"), not the full read/write
  surface of the schema.
- **Healthcare safety boundary**: this project must not let an agent
  autonomously take actions (e.g. modifying medical/clinical records) that
  belong under human supervision (see Section 7). Constraining agents to
  tools is what makes that boundary enforceable rather than aspirational.

## 6. Security Boundaries

- Secrets are never hardcoded and never logged (see
  [SECURITY.md](../SECURITY.md) and `app/core/logging.py`). Settings fields
  that hold credentials — including `DATABASE_URL` and `JWT_SECRET_KEY` —
  use Pydantic's `SecretStr`, which masks the value in `repr()`/`str()`
  output.
- `JWT_SECRET_KEY` must be explicitly configured in `staging`/`production`
  — a settings validator rejects startup otherwise, the same
  fail-loud-not-silent pattern as the `production`+`DEBUG=true` rule
  below. `development`/`test` may run without one (an unconfigured
  secret is a startup-time `RuntimeError` only if a token is actually
  requested there — see [RBAC.md](RBAC.md) Section 3).
- Passwords are never hashed by hand — `app/auth/security.py` wraps
  `argon2-cffi` exclusively. JWTs are never encoded/decoded by hand —
  `app/auth/jwt.py` wraps `PyJWT` exclusively. Plaintext passwords are
  never persisted or logged; `password_hash` is never returned by any API
  response (`app/schemas/auth.py`'s `CurrentUserResponse` deliberately
  excludes it). See [RBAC.md](RBAC.md) for the full model.
- Authorization is always re-resolved from the database on every
  request — a JWT is never trusted for organization membership or role
  (see [RBAC.md](RBAC.md) Section 4 and ADR-0004).
- `Patient` (STORY-005) holds administrative fields only — no diagnosis,
  symptoms, medication, treatment, clinical notes, insurance, or
  emergency-triage content exists anywhere in the domain model (see
  Section 7 above and [PATIENTS.md](PATIENTS.md) Section 1). Patient
  lookups are tenant-scoped
  by construction (`app/repositories/patient.py` has no unscoped read),
  and cross-tenant lookups return the same "not found" response as a
  truly nonexistent id — never disclosing that a patient exists under a
  different organization (see [PATIENTS.md](PATIENTS.md) Section 10).
- `Department`/`Practitioner` (STORY-006) hold administrative scheduling
  fields only — same discipline as `Patient`. Tenant/facility/assignment
  ownership integrity is enforced at the DATABASE level via composite
  foreign keys, not just application validation — a department can never
  be created under a facility belonging to a different organization, and
  a practitioner can never be assigned to a department in a different
  organization, regardless of caller (see
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Sections 4–5).
  `PractitionerResponse` exposes no private email, phone, address, or
  `User` linkage — none of those fields exist on the model in the first
  place (Section 13 of that document).
- The application must be able to start, and pass health checks, **without**
  any LLM API key or database connection configured. `DATABASE_URL` is
  optional at the configuration layer in every environment, and the real
  database connectivity check (`app/db/health.py`) is only invoked when
  it's set. LLM provider fields remain optional/unused since no LLM
  integration exists yet.
- **Readiness is production-safety-aware, not just database-aware**: an
  unconfigured database is acceptable in `development`/`test` (the app
  must run without one there), but makes `/api/v1/ready` report
  `not_ready`/503 in `staging`/`production` — an operator who forgets to
  configure `DATABASE_URL` in a real deployment gets a hard failure
  signal, not a silently-passing readiness probe. This policy lives in
  exactly one place, `_is_check_acceptable()` in
  `app/api/v1/endpoints/health.py`, using the existing typed `Environment`
  enum — not scattered string comparisons. See
  [DATABASE.md](DATABASE.md) Section 7 for the full table and rationale.
- The database connectivity check never raises and never forwards driver
  exception detail (which can include host/port information) to its
  caller — connection failures are logged server-side only, and the
  `/api/v1/ready` response exposes only an enum-like status, never
  `DATABASE_URL`, credentials, or stack traces, in any environment.
- `APP_ENV=production` combined with `DEBUG=true` is rejected at startup by
  a settings validator, rather than silently allowed — production must not
  accidentally run in debug mode.
- Unhandled exceptions return a generic, standardized error body to
  clients; full detail is logged server-side only.

## 7. Healthcare Safety Boundary

AgentCare handles healthcare **administration and care coordination**. Per
the product boundary, the system — including any future agent — must not
autonomously diagnose conditions, prescribe medication, recommend dosages,
change medication, or otherwise claim to replace a healthcare professional.
Clinical decisions remain under human supervision. This is a product-level
constraint today (documented here and in the README) and will become an
enforced constraint at the agent/tool layer once agents exist (Section 5):
tools will simply not expose clinical-decision capabilities.

## 8. Configuration Strategy

- `app/core/config.py` defines a single typed `Settings` model
  (`pydantic-settings`), sourced from environment variables and, in local
  development, a `.env` file (never committed — see
  [SECURITY.md](../SECURITY.md)).
- `Environment` is a closed enum (`development`, `test`, `staging`,
  `production`) so environment-dependent behavior is checked against a
  known type rather than compared as loose strings throughout the codebase.
- Fields that don't yet have a real integration (LLM providers, JWT
  signing) — and `DATABASE_URL`, which has a real integration
  (STORY-002) but is still optional at the configuration layer — let the
  app start for tests and health checks without them. `DATABASE_URL`
  becomes operationally required only in environments that actually need
  the database (not enforced by `Settings` itself; see
  [DATABASE.md](DATABASE.md) Section 10).
- `get_settings()` is cached (`lru_cache`) and used as a FastAPI dependency,
  so settings are read once per process rather than re-parsed per request.

## 9. Testing Strategy

- Tests exercise the real FastAPI application (via `TestClient`), not
  mocks of the endpoint under test.
- Coverage as of STORY-001: application factory behavior, health/readiness
  endpoint responses and schema, configuration defaults and validation
  (including the production/debug safety rule), and the standardized error
  shape for an unhandled route.
- Coverage added in STORY-002: engine/session lifecycle and the database
  connectivity check, exercised against a real (if non-production) SQLite
  database rather than mocked — see [DATABASE.md](DATABASE.md) Sections
  11–12 for what this does and doesn't prove; readiness-endpoint behavior
  across database states (`ok`/`unavailable`/`not_configured`) via FastAPI
  dependency overrides; that the readiness response never leaks
  connection details; and, per the production-readiness-safety
  correction, `not_configured`/`ok`/`unavailable` each parametrized across
  all four `Environment` values, plus a dedicated test confirming
  `/api/v1/health` stays 200 in every environment regardless of database
  state.
- Coverage added in STORY-003: the `Organization`/`Facility` domain model
  suite (`backend/tests/models/`) runs exclusively against real
  PostgreSQL (not SQLite), each test isolated by a rolled-back savepoint
  — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 18 for what's covered
  and why SQLite isn't used here.
- Coverage added in STORY-004: `User`/`OrganizationMembership` model
  tests, password hashing/verification, JWT creation/decoding
  (including expiry and tampered-signature rejection),
  `authenticate_user` (including the unknown-email/wrong-password/
  inactive-user response uniformity), `get_current_user`/
  `get_current_membership`/`require_roles` (401 vs. 403, inactive user,
  inactive membership, wrong role, cross-organization isolation), and the
  `POST /auth/token`/`GET /auth/me` endpoints end-to-end — all against
  real PostgreSQL, via `httpx.AsyncClient` + `ASGITransport` rather than
  `starlette.testclient.TestClient` (the latter's separate event-loop
  thread is incompatible with sharing an async DB session created in the
  test's own event loop — see `backend/tests/conftest.py`).
- Coverage added in STORY-005: `Patient` model tests (constraints,
  `NULL`-distinctness, normalization, future-DOB rejection);
  `app.repositories.patient` tests (tenant-scoped get/list, cross-tenant
  isolation, that `create()` flushes but never commits);
  `PatientService` tests (patient-number conflict, all four user-linkage
  rejection reasons, tenant-scoped retrieval, self-access); and the full
  patient API authorization matrix end-to-end (admin/staff/patient ×
  create/list/get/me, inactive-membership rejection, cross-tenant lookup
  returning 404, and that a patient response never contains anything
  beyond the documented administrative fields) — all against real
  PostgreSQL.
- Coverage added in STORY-006: `Department`/`Practitioner`/
  `PractitionerDepartment`/`PractitionerAvailability` model tests,
  including the composite ownership-integrity foreign keys verified both
  through the ORM and via raw SQL (facility-organization mismatch,
  practitioner-organization mismatch, department-organization mismatch,
  and the assignment-existence FK on availability); repository tests
  (tenant-scoped reads, no hidden commits); service tests (facility
  ownership, code-conflict, duplicate-assignment, unassigned-practitioner
  rejection, invalid time range, invalid timezone, overlap rejection,
  adjacent-window acceptance, cross-day/cross-inactive-window
  acceptance); and the full department/practitioner/availability API
  authorization matrices end-to-end (admin/staff/patient ×
  create/list/get/assign/availability, cross-tenant rejection at every
  level) — all against real PostgreSQL.
- Test values (e.g. a JWT secret used only to test that `SecretStr` masking
  works, or synthetic organization/facility/user/email/password/patient/
  practitioner values) are synthetic and clearly non-production.
- As services, repositories, and workflows are introduced, this section
  will describe how unit, integration, and end-to-end layers divide.

## 10. Multi-Tenancy Direction

**Decided (STORY-003, ADR-0003)**: `Organization` is AgentCare's tenant
boundary; `Facility` belongs to exactly one Organization via a required
FK. Every future tenant-owned entity is expected to carry
`organization_id`, directly or through an explicit ownership path. See
[DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 13 and ADR-0003 for the full
decision, including what is deliberately **not** yet enforced.

**Extended (STORY-004, ADR-0004)**: identity (`User`) is deliberately
*not* tenant-scoped — a `User` may hold at most one `OrganizationMembership`
per organization, but memberships in multiple organizations. Request-level
tenant access is resolvable via `get_current_membership`/
`require_roles` (`app/auth/dependencies.py`), which re-query
`OrganizationMembership` fresh from the database on every request rather
than trusting anything cached in a JWT — see [RBAC.md](RBAC.md) Sections
1, 4–6 and ADR-0004.

**Enforced end-to-end (STORY-005, ADR-0005)**: `Patient` is the first
tenant-owned resource with a real, protected route depending on those
primitives. Beyond request-level membership/role checks,
`app/repositories/patient.py` requires an explicit `organization_id` on
every function — there is no unscoped read to call by mistake — and
cross-tenant lookups return the same "not found" response as a truly
nonexistent id (Section 6, [PATIENTS.md](PATIENTS.md) Section 10),
closing the "does this let a non-member learn a resource exists
elsewhere" gap for this resource specifically.

**Extended to a multi-level hierarchy (STORY-006, ADR-0006)**: `Department`
belongs to `Facility` which belongs to `Organization`; `Practitioner`
belongs to `Organization`; `PractitionerDepartment` and
`PractitionerAvailability` each depend on two or three levels of that
hierarchy simultaneously. Rather than trust application code to
re-verify "does this facility really belong to this organization" at
every call site, STORY-006 pushed that invariant into the DATABASE
itself via composite foreign keys (`(organization_id, facility_id) ->
facilities(organization_id, id)`, and two more analogous ones for
practitioner/department assignment and a third for availability's
assignment-existence check) — see
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Sections 4–5, 7 for
the full mechanism. This is a stronger guarantee than STORY-005's
single-level tenant check: it holds even against a hypothetical future
code path that forgot to call `get_current_membership` at all, as long
as it still goes through the ORM/database.

**Still planned**: there is no global, automatic tenant-access filter at
the query level, and none is assumed — each future domain resource's
repository must independently require `organization_id` on every
function, the way `app.repositories.patient`/`.department`/
`.practitioner` do, rather than relying on a shared interceptor. Row-level
security and a schema-per-tenant/database-per-tenant alternative were
both considered and deferred (not rejected) in ADR-0003. A race-proof
database exclusion constraint for availability-overlap prevention
remains deferred — see
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 9.

## 11. Observability Direction (Planned)

STORY-001 provides only a structured logging foundation (level, timestamp,
logger name, message — see `app/core/logging.py`). Metrics, tracing, and
any external observability platform integration are not implemented and
are deferred to a later story, to be documented in `docs/OBSERVABILITY.md`
when built.
