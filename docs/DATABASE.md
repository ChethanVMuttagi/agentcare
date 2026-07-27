# AgentCare Database

This document describes the database foundation implemented in STORY-002,
extended in STORY-003 (the first real domain tables and migration),
STORY-004 (identity/membership tables and a second migration),
STORY-005 (the `patients` table and a third migration), STORY-006
(department/practitioner/availability tables and a fourth migration),
STORY-007 (the `appointments` table, a fifth migration, and the
`btree_gist` PostgreSQL extension), and STORY-008 (the
`patient_documents` table and a sixth migration — metadata only, no file
bytes) — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the model itself,
[RBAC.md](RBAC.md) for the identity/authorization model,
[PATIENTS.md](PATIENTS.md) for the patient domain,
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) for the scheduling-
resource domain, [APPOINTMENTS.md](APPOINTMENTS.md) for the appointment
booking engine, and [DOCUMENTS.md](DOCUMENTS.md) for the secure document
management capability these tables back. It follows the same CURRENT
vs. PLANNED discipline as [ARCHITECTURE.md](ARCHITECTURE.md): everything
described here as implemented exists in the repository today; anything
marked PLANNED does not yet.

Eleven domain tables exist so far: `organizations`, `facilities`,
`users`, `organization_memberships`, `patients`, `departments`,
`practitioners`, `practitioner_departments`, `practitioner_availability`,
`appointments`, and `patient_documents` — see
[DOMAIN_MODEL.md](DOMAIN_MODEL.md). No other healthcare domain tables
exist yet, and no table anywhere in this schema stores file bytes
(`BLOB`/`bytea`) — `patient_documents` holds metadata and a storage
reference only (see [DOCUMENTS.md](DOCUMENTS.md) Section 2).

## 1. PostgreSQL as Production System of Record

PostgreSQL is AgentCare's production relational database and remains the
system of record for all domain data. No other database is used in
production. `DATABASE_URL` always identifies the PostgreSQL instance the
running application should use.

## 2. SQLAlchemy 2.x

The ORM/Core layer is SQLAlchemy 2.x, using its modern declarative style:

- `app/db/base.py` defines `Base(DeclarativeBase)` — the single
  declarative base every ORM model must subclass. It also sets a
  **naming convention** on `Base.metadata` (e.g. constraints render as
  `uq_organizations_slug`, not a driver-generated name) — set once,
  before the first migration, so Alembic autogenerate diffs against
  predictable names.
- `app/models/organization.py` and `app/models/facility.py` (STORY-003),
  `app/models/user.py` and `app/models/membership.py` (STORY-004),
  `app/models/patient.py` (STORY-005), `app/models/department.py`,
  `practitioner.py`, `practitioner_department.py`,
  `practitioner_availability.py` (STORY-006),
  `app/models/appointment.py` (STORY-007), and
  `app/models/patient_document.py` (STORY-008) are the real
  `Mapped[...]`-typed model classes — SQLAlchemy 2.x's native typed
  mapping (`Mapped[str]`, `mapped_column(...)`), not the legacy 1.x
  style. See [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the models themselves
  and `app/db/mixins.py` for the shared `UUIDPrimaryKeyMixin`/
  `TimestampMixin` they're built on.
- Enum-backed columns (`OrganizationType`, `FacilityType`, `Role`,
  `PractitionerType`, `DayOfWeek`, `AppointmentStatus`, and — as of
  STORY-008 — `DocumentType`/`DocumentStatus`/`DocumentMediaType`) use
  SQLAlchemy's `Enum` type with `native_enum=False` (VARCHAR + a real
  `CHECK` constraint, not a native PostgreSQL `ENUM` type) and
  `values_callable` (persist each member's lowercase `.value`, not its
  uppercase Python name) — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md)
  Section 14 for the full rationale.
- **Composite foreign keys** (introduced in STORY-006, extended in
  STORY-007/008): `Department`, `PractitionerDepartment`,
  `PractitionerAvailability`, `Appointment` (STORY-007), and
  `PatientDocument` (STORY-008 — TWO composite FKs: patient ownership,
  and uploader-membership existence, see
  [DOCUMENTS.md](DOCUMENTS.md) Section 5) each hold a multi-column
  `ForeignKeyConstraint` (in addition to, or instead of, single-column
  ones) to enforce tenant/facility/assignment ownership integrity at the
  database level rather than relying solely on
  application checks — see
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Sections 4–5, 7,
  [APPOINTMENTS.md](APPOINTMENTS.md) Section 6, and ADR-0006/ADR-0007
  for the full mechanism and rationale. This required adding a composite
  `UNIQUE(organization_id, id)` constraint to `Facility` (and,
  transitively, to `Department`, `Practitioner`, and — STORY-007 —
  `Patient`) purely so a composite FK has something valid to target —
  PostgreSQL requires the referenced column set to be covered by a
  unique constraint or index.
- **PostgreSQL `EXCLUDE` constraints** (new in STORY-007): `Appointment`
  holds two GiST `EXCLUDE` constraints (via
  `sqlalchemy.dialects.postgresql.ExcludeConstraint`, `using="gist"`) —
  the first genuinely race-safe, database-enforced concurrency
  constraint in this codebase, as opposed to a service-level pre-check.
  Requires the `btree_gist` extension (provisioned via Alembic,
  `CREATE EXTENSION IF NOT EXISTS btree_gist`) for GiST to support an
  equality operator class on `uuid`. See
  [APPOINTMENTS.md](APPOINTMENTS.md) Section 7 and
  [adr/ADR-0007-appointment-concurrency.md](adr/ADR-0007-appointment-concurrency.md).

## 3. asyncpg

The application talks to PostgreSQL exclusively through the `asyncpg`
driver, via the `postgresql+asyncpg://` SQLAlchemy URL scheme. This keeps
database access async end-to-end, consistent with FastAPI's async request
handling (see ADR-0002 for the rationale versus a synchronous driver).

## 4. Async Session Lifecycle

`app/db/session.py`:

- `get_engine()` returns the process-wide `AsyncEngine`, created lazily on
  first call and cached (`lru_cache`) for the life of the process — the
  same pattern `get_settings()` already uses. **Importing this module, or
  any other application module, never opens a database connection.**
  Engine creation itself doesn't connect either; SQLAlchemy engines
  connect lazily on first actual use.
- `get_engine()` raises `RuntimeError` if `DATABASE_URL` is not
  configured. Code paths that don't need the database (e.g. liveness) must
  not call it.
- `get_sessionmaker()` returns a cached `async_sessionmaker[AsyncSession]`
  bound to that engine.
- `get_db_session()` is the FastAPI dependency every domain route uses:
  it creates a session, `yield`s it, and closes it via `async with`. As
  of STORY-005/006/007 it is consumed by the patient, department,
  practitioner, and appointment endpoints
  (`app/api/v1/endpoints/patients.py`, `departments.py`,
  `practitioners.py`, `appointments.py`).
- `dispose_engine()` disposes the engine's connection pool if one was ever
  created, and is a safe no-op otherwise. It's called from the FastAPI
  `lifespan` handler in `app/main.py` on shutdown.

## 5. Transaction Ownership Philosophy

`get_db_session()` **does not auto-commit or auto-rollback**. It creates a
session, yields it, and closes it; any exception raised while the session
is in use propagates to the caller unmodified.

This is deliberate (see ADR-0002): once services and, later, multi-step
agent workflows exist, an implicitly auto-committing session per request
would make partial-failure behavior surprising.

**Realized as of STORY-005** (`app/repositories/patient.py`,
`app/services/patient.py` — see [PATIENTS.md](PATIENTS.md) Section 6),
**reused unchanged in STORY-006**
(`app/repositories/department.py`/`practitioner.py`/
`practitioner_department.py`/`availability.py`,
`app/services/department.py`/`practitioner.py`/`availability.py` — see
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) Section 10), **and
again in STORY-007** (`app/repositories/appointment.py`,
`app/services/appointment.py`/`availability_query.py` — see
[APPOINTMENTS.md](APPOINTMENTS.md) Sections 12-13):

- **Repositories** perform persistence operations (`add`/`flush`/`query`
  only) but never arbitrarily commit business transactions —
  `app.repositories.patient.create()` (and its STORY-006/007
  counterparts) add and flush, and nothing more.
- **Services** own the transaction boundary explicitly —
  `PatientService.create_patient`, `DepartmentService.create_department`,
  `PractitionerService.create_practitioner`/`assign_to_department`,
  `AvailabilityService.create_availability`, and
  `AppointmentService.book_appointment`/`reschedule_appointment`/
  `cancel_appointment` each commit once every validation check has
  passed, and commit nothing at all if a check fails first (nothing was
  ever staged, so there's no partial state to roll back). `AppointmentService`
  additionally handles the ONE case where a commit itself can fail after
  passing every prior check — a database-level collision (see
  [APPOINTMENTS.md](APPOINTMENTS.md) Section 7): it explicitly rolls back
  before translating the error, rather than leaving the session in a
  failed-transaction state. Read operations never commit. Multi-step
  agent workflows, when they exist, are expected to follow the same
  principle: the workflow/service layer decides commit/rollback, never
  the repository.

This is now demonstrated end-to-end across three independent domains
(`Patient`; `Department`/`Practitioner`/availability; `Appointment`);
every future mutating domain operation is expected to follow the same
`Route -> Service -> Repository -> Session` shape rather than reverting
to ad hoc commits inside a route or repository.

## 6. Engine Lifecycle

- Created lazily, on first call to `get_engine()`.
- `pool_pre_ping=True` is set so a stale/dropped connection is detected
  and replaced rather than surfacing a confusing error on first use — a
  reasonable production default that doesn't require tuning pool size or
  overflow settings, which are left at SQLAlchemy's defaults (no
  connection-pool over-engineering at this stage — see ADR-0002).
- Disposed cleanly via `dispose_engine()` during FastAPI's `lifespan`
  shutdown phase (`app/main.py`), so no dangling connections are left open
  when the process exits.

## 7. Connection / Readiness Checking

`app/db/health.py` provides `check_database_connection(engine)`, which
runs a real `SELECT 1` against the given engine and returns `True`/`False`
— it never fakes success, and it never raises (driver/connection errors
are caught, logged server-side, and turned into `False`).

This is wired into `GET /api/v1/ready` (`app/api/v1/endpoints/health.py`)
via two collaborators with a deliberate separation of concerns:

- `get_database_readiness_check` reports the **factual** state:
  `ok` / `unavailable` / `not_configured`. It never considers environment
  — it only ever reports what's true.
- `_is_check_acceptable(check, environment)` — the **one place** in the
  codebase that decides whether a given factual state counts as "ready",
  as a function of the current `Environment` (from the same typed
  `Settings`/`Environment` enum used everywhere else — no scattered
  string comparisons). `get_readiness` applies it to every check to
  decide the overall `status` and HTTP code.

Post-STORY-002-correction semantics (production-readiness safety):

| `DATABASE_URL` | Connectivity | `ReadinessCheck.status` | `development` / `test` | `staging` / `production` |
|---|---|---|---|---|
| not set | n/a | `not_configured` | `ready` (200) | **`not_ready` (503)** |
| set | reachable | `ok` | `ready` (200) | `ready` (200) |
| set | unreachable | `unavailable` | `not_ready` (503) | `not_ready` (503) |

Rationale:

- **`development`/`test` + `not_configured` → `ready`**: the application
  must be importable, startable, and pass its test suite without a live
  PostgreSQL instance — this is a hard STORY-002 requirement, and remains
  true after the correction.
- **`staging`/`production` + `not_configured` → `not_ready` (503)**: an
  operator who forgot to configure `DATABASE_URL` in a real deployment
  must see a hard failure, not a silently-passing health check. Treating
  an unconfigured required dependency as "ready" outside development/test
  would be unsafe — it could let a misconfigured instance pass a
  readiness probe and receive traffic.
- **`unavailable` → always `not_ready` (503), in every environment**: the
  operator *did* provide `DATABASE_URL` and the application genuinely
  cannot reach it — that is a real failure regardless of environment, and
  readiness reflects it as such unconditionally.
- **`ok` → always `ready` (200), in every environment**: a working,
  configured database is ready everywhere.

`GET /api/v1/health` (liveness) is completely unaffected by any of this —
it only ever reports that the process itself is alive, independent of any
dependency's state or the current environment, exactly as in STORY-001.
This independence is deliberate and explicitly tested (see Section 11).

**What the readiness response never includes**: `DATABASE_URL`, host,
port, username, password, or raw driver/connection exception text or
stack traces. Only the enum-like `status` values above are exposed. Full
exception detail is logged server-side only (see `app/db/health.py`'s
`logger.exception(...)` call) — see Section 13.

## 8. Alembic Migration Strategy

Alembic is configured under `backend/` (`backend/alembic.ini`,
`backend/migrations/`), using SQLAlchemy 2.x's async engine support.

- `migrations/env.py` reads `DATABASE_URL` from the application's own
  `Settings` (`app.core.config.get_settings()`) at runtime — the same
  configuration mechanism the application itself uses. **No credentials
  or connection strings are embedded in `alembic.ini` or any committed
  file.**
- `target_metadata = Base.metadata` (`app.db.base.Base`), so Alembic's
  `--autogenerate` can diff against whatever models are registered on
  `Base` at the time it runs.
- **How model modules get registered**: `migrations/env.py` imports the
  `app.models` package (`from app import models  # noqa: F401`), and
  `app/models/__init__.py` imports every model module in turn — this is
  what actually attaches each model's table to `Base.metadata` before
  autogenerate runs. A new domain model only needs its module added to
  `app/models/__init__.py`; `migrations/env.py` itself never needs to
  change again for that. (STORY-002 used a placeholder comment here
  instead, since no model package existed yet; STORY-003 replaced it with
  this real, permanent mechanism as soon as there was something to
  import.)
- **First real migration (STORY-003)**: `3e41d3b01f81_create_organizations_and_facilities_.py`
  creates the `organizations` and `facilities` tables — see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the schema itself. Generated via
  `alembic revision --autogenerate`, then reviewed and reformatted by
  hand (import style, line length) to pass the same `ruff check .` gate
  as the rest of the codebase — autogenerated migrations are not exempt
  from review. Validated against real PostgreSQL: applied, inspected,
  downgraded, and re-applied cleanly (see the STORY-003 report for the
  full validation transcript).
- **Second migration (STORY-004)**: `445bcf7d22b9_create_users_and_organization_.py`
  (`down_revision = "3e41d3b01f81"`) creates `users` (unique `email`,
  `password_hash`, `is_active`) and `organization_memberships` (FKs to
  `organizations`/`users` with `ondelete="RESTRICT"`, a `role` column with
  a real `CHECK` constraint, `UNIQUE(organization_id, user_id)`, and
  indexes on both FK columns) — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md)
  Sections 4–5 for the schema itself. Same review/reformat discipline as
  the first migration. Validated against real PostgreSQL: applied,
  inspected (columns, constraints, indexes), downgraded one step (with
  `organizations`/`facilities` data confirmed untouched), and re-applied
  cleanly. Contains no seeded users, passwords, or credentials of any
  kind — only schema DDL.
- **Third migration (STORY-005)**: `2037af2600c4_create_patients_table.py`
  (`down_revision = "445bcf7d22b9"`) creates `patients` (`organization_id`
  FK `ondelete="RESTRICT"`, nullable `user_id` FK `ondelete="RESTRICT"`,
  `patient_number`/`first_name`/`last_name`/`date_of_birth`/`is_active`,
  `UNIQUE(organization_id, patient_number)`,
  `UNIQUE(organization_id, user_id)`, indexes on both FK columns) — see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 6 and
  [PATIENTS.md](PATIENTS.md) for the schema itself. Same review/reformat
  discipline as the prior two migrations. Validated against real
  PostgreSQL: applied, inspected (columns, constraints, indexes,
  including a direct proof that two `NULL`-`user_id` patients are allowed
  in the same organization), downgraded one step (with
  `organizations`/`facilities`/`users`/`organization_memberships` data
  confirmed untouched), and re-applied cleanly. Contains no seeded
  patients or credentials of any kind — only schema DDL.
- **Fourth migration (STORY-006)**: `6251a20d9632_create_department_practitioner_and_.py`
  (`down_revision = "2037af2600c4"`) creates `practitioners`,
  `departments`, `practitioner_departments`, and
  `practitioner_availability`, plus alters `facilities` to add the
  composite `uq_facilities_organization_id_id` constraint. See
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Sections 7–10 and
  [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md) for the schema
  itself. Same review/reformat discipline as the prior three migrations
  — plus one additional manual fix: `--autogenerate`'s default operation
  order placed the `facilities` alter (adding the composite unique
  constraint) AFTER creating `departments`, but `departments`' composite
  FK requires that constraint to already exist — the generated file was
  reordered by hand (constraint addition first in `upgrade()`, removal
  last in `downgrade()`) before it would apply at all; this was caught by
  actually running the migration against real PostgreSQL, not just by
  reading the generated SQL. Validated against real PostgreSQL: applied,
  inspected (columns, all composite and simple FKs, unique constraints,
  `CHECK` constraints, indexes), a direct raw-SQL/rollback smoke test
  confirming the ownership-integrity composite FK really rejects a
  mismatched facility/organization pairing, downgraded one step (with
  all five prior tables' data confirmed untouched), and re-applied
  cleanly. Contains no seeded departments, practitioners, or credentials
  of any kind — only schema DDL.
- **Fifth migration (STORY-007)**: `c6674a696c08_create_appointments_table.py`
  (`down_revision = "6251a20d9632"`) creates the `appointments` table
  (composite ownership FKs, the `start_at < end_at` `CHECK`, the
  `appointment_status` `CHECK`, and the two GiST `EXCLUDE` constraints —
  see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 15 and
  [APPOINTMENTS.md](APPOINTMENTS.md) for the schema itself), first runs
  `CREATE EXTENSION IF NOT EXISTS btree_gist` (required by the `EXCLUDE`
  constraints — Section 2), and alters `patients` to add the composite
  `uq_patients_organization_id_id` constraint (the same
  "redundant-unique-key-for-a-composite-FK-to-target" technique as the
  fourth migration's `facilities` alter, applied one level down — the
  extension creation and this alter both had to be manually reordered
  BEFORE `appointments`' `CREATE TABLE`, since Alembic's autogenerate
  does not order operations around forward dependencies like this on its
  own). Validated against real PostgreSQL: applied, inspected (columns,
  all composite and simple FKs, `CHECK` constraints, both `EXCLUDE`
  constraints via `\d appointments`, the `btree_gist` extension via
  `\dx`), a direct raw-SQL smoke test proving the exclusion constraints
  reject an overlapping practitioner/patient booking while allowing an
  adjacent one and a cross-tenant patient FK mismatch is rejected,
  downgraded one step (with all prior tables' data confirmed untouched,
  and the extension dropped), and re-applied cleanly. Contains no
  seeded appointments or credentials of any kind — only schema DDL.
- **Sixth migration (STORY-008)**: `f4702bbc0be1_create_patient_documents_table.py`
  (`down_revision = "c6674a696c08"`) creates the `patient_documents`
  table — composite ownership FKs (`organization_id`+`patient_id`
  against `patients`, `organization_id`+`uploaded_by_user_id` against
  `organization_memberships`), the `document_type`/`document_status`/
  `document_media_type` `CHECK` constraints, the
  `available_has_size_and_hash` `CHECK`, `UNIQUE(storage_key)`, and
  supporting indexes — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 15
  and [DOCUMENTS.md](DOCUMENTS.md) for the schema itself. A single,
  self-contained table requiring no cross-migration operation reordering
  (unlike the fourth/fifth migrations) — every table it references
  already carries the composite unique constraint its FKs target.
  Validated against real PostgreSQL: applied, inspected (columns, all
  composite and simple FKs, `CHECK` constraints, the unique constraint),
  a direct raw-SQL smoke test proving the `available_has_size_and_hash`
  `CHECK` rejects an `available` row with `NULL` `size_bytes`/`sha256`
  while a `pending` row with the same `NULL`s succeeds, a cross-tenant
  patient FK mismatch is rejected, an uploader with no membership in the
  target organization is rejected, and a duplicate `storage_key` is
  rejected, downgraded one step (with all prior tables' data confirmed
  untouched), and re-applied cleanly. Contains no seeded documents,
  file bytes, or credentials of any kind — only schema DDL.

## 9. Migration Commands

Run from `backend/` (PowerShell):

```powershell
cd backend
# Generate a new migration from model changes:
alembic revision --autogenerate -m "add <thing>"

# Apply all pending migrations:
alembic upgrade head

# Roll back the most recent migration:
alembic downgrade -1

# Show current DB revision:
alembic current
```

All commands require `DATABASE_URL` to be set (via `backend/.env` or the
environment) and pointing at a real, reachable PostgreSQL instance —
Alembic itself is not designed to run against nothing.

**After `--autogenerate`, review the generated file before committing it**:
check the detected operations actually match intent (autogenerate can
miss things like check constraints depending on type), and run
`ruff check .` — Alembic's generated style (old-style typing imports,
unwrapped long `sa.Column(...)` lines) does not pass this repository's
lint gate as-is and needs reformatting, same as any other file.

## 10. Environment Configuration

`DATABASE_URL` is read the same way as every other setting (see
`docs/ARCHITECTURE.md` Section 8): via `Settings`
(`app/core/config.py`), sourced from the environment or a local
`backend/.env` file (never committed). It is stored as `SecretStr`, so it
is masked in `repr()`/`str()` output and cannot be casually leaked through
logs.

`DATABASE_URL` remains **optional** at the configuration (`Settings`)
layer in every environment — `Settings` itself never requires it, so the
application is always importable and testable without it (see Section
7). Whether an unconfigured database is *operationally* acceptable is
instead enforced at the readiness layer, per-environment (Section 7):
`development`/`test` tolerate it, `staging`/`production` do not (503).
This keeps `Settings` simple and universally safe to construct, while
still giving staging/production a hard, visible failure signal when
`DATABASE_URL` is missing.

Example (see `.env.example`):

```
DATABASE_URL=postgresql+asyncpg://agentcare_user:changeme@localhost:5432/agentcare_dev
```

## 11. Testing Strategy

- **Unit/isolated tests** (`backend/tests/db/`) exercise real SQLAlchemy
  behavior — engine creation, session creation/closing, the connectivity
  check — against a real, if different, database: SQLite via the
  `aiosqlite` driver. These tests do not mock SQLAlchemy itself.
- **API-level readiness tests** (`backend/tests/api/test_readiness_database.py`)
  exercise the real FastAPI app and `/api/v1/ready` endpoint, using
  `app.dependency_overrides` on the dedicated `get_database_readiness_check`
  dependency to simulate `ok` / `unavailable` / `not_configured` states
  without needing any real database connection. This also verifies no
  connection details leak into the response.
- **Environment-dependent readiness tests**
  (`backend/tests/api/test_readiness_environment.py`) verify the
  production-readiness-safety correction: `not_configured` is
  parametrized across all four `Environment` values, asserting `ready`/200
  in `development`/`test` and `not_ready`/503 in `staging`/`production`;
  `ok` and `unavailable` are each parametrized across all four
  environments too, asserting they're unaffected by environment (`ok`
  always ready, `unavailable` never ready); and a dedicated test confirms
  `/api/v1/health` stays 200 in every environment even when the database
  is `unavailable`.
- **Optional PostgreSQL integration test**
  (`backend/tests/db/test_postgres_integration.py`) is skipped unless
  `AGENTCARE_TEST_POSTGRES_URL` is set to a real, reachable PostgreSQL
  instance. It is not a prerequisite for running the test suite
  day-to-day (though it — and the domain model tests below — were run
  against a real local PostgreSQL 16 instance as part of STORY-003).
- **Domain model tests** (`backend/tests/models/` — STORY-003, extended
  STORY-004 with `test_user.py`/`test_membership.py`, STORY-005 with
  `test_patient.py`, and STORY-006 with `test_department.py`,
  `test_practitioner.py`, `test_practitioner_department.py`,
  `test_practitioner_availability.py`) run exclusively against real
  PostgreSQL, same skip condition (`AGENTCARE_TEST_POSTGRES_URL`),
  because nearly everything meaningful about these models (uniqueness,
  composite unique constraints including `NULL`-distinctness, every FK's
  `ON DELETE RESTRICT` — including the STORY-006 composite
  ownership-integrity FKs — and enum/`CHECK` constraints) is
  database-enforced behavior SQLite doesn't replicate faithfully — see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 15 and Section 17. Each
  test runs inside a savepoint that's always rolled back
  (`tests/conftest.py`), so no synthetic data persists in the shared
  development database.
- **Auth tests** (`backend/tests/auth/`, `backend/tests/api/test_auth_endpoints.py`
  — STORY-004) also run against real PostgreSQL under the same savepoint
  isolation: password hashing/verification, JWT creation/decoding,
  `authenticate_user`, `get_current_user`/`get_current_membership`/
  `require_roles`, and the `POST /auth/token`/`GET /auth/me` endpoints
  end-to-end. HTTP-level tests use `httpx.AsyncClient` +
  `ASGITransport` rather than `starlette.testclient.TestClient`, because
  `TestClient` dispatches requests through a separate anyio event-loop
  thread that cannot safely share the asyncpg connection the
  `db_session` fixture opens in the test's own event loop.
- **Patient repository/service/API tests** (`backend/tests/repositories/`,
  `backend/tests/services/`, `backend/tests/api/test_patient_endpoints.py`
  — STORY-005) also run against real PostgreSQL under the same savepoint
  isolation: tenant-scoped get/list, cross-tenant isolation, that
  `app.repositories.patient.create()` flushes without committing,
  `PatientService`'s business rules (patient-number conflict, all
  user-linkage rejection reasons), and the full patient API authorization
  matrix end-to-end (`httpx.AsyncClient`, same reasoning as the auth
  tests above).
- **Department/practitioner/availability repository/service/API tests**
  (`backend/tests/repositories/test_department.py`/`test_practitioner.py`/
  `test_practitioner_department.py`/`test_availability.py`,
  `backend/tests/services/test_department.py`/`test_practitioner.py`/
  `test_availability.py`,
  `backend/tests/api/test_department_endpoints.py`/
  `test_practitioner_endpoints.py` — STORY-006) also run against real
  PostgreSQL under the same savepoint isolation: tenant-scoped reads, no
  hidden commits, facility-ownership validation, duplicate-assignment
  rejection, the assignment/time-range/timezone/overlap business rules
  (including that adjacent windows are allowed and inactive windows
  don't block new ones), and the full department/practitioner/
  availability authorization matrices end-to-end.
- **Appointment repository/service/API tests, and the mandatory real-
  concurrency proof** (`backend/tests/repositories/test_appointment.py`,
  `backend/tests/services/test_appointment.py`/`test_availability_query.py`,
  `backend/tests/api/test_appointment_endpoints.py` — STORY-007) also run
  against real PostgreSQL under the same savepoint isolation. Separately,
  `backend/tests/db/test_appointment_concurrency.py` deliberately does
  NOT use the shared savepoint-isolated `db_session` fixture — it opens
  its own dedicated engine and TWO genuinely independent connections,
  commits real (later explicitly deleted) synthetic setup data, and races
  two concurrently-executing `AppointmentService.book_appointment()`
  calls via `asyncio.gather`, asserting exactly one succeeds and the
  other fails with `AppointmentConflictError` — for both the
  practitioner-side and patient-side `EXCLUDE` constraint. This is the
  one test in the suite that cannot be satisfied by savepoint isolation,
  because the entire point is proving behavior across two SEPARATE,
  concurrently-open transactions, which a single shared connection
  cannot represent.
- **`PatientDocument` model/repository/service/API tests, plus storage
  and file-validation tests (STORY-008)**
  (`backend/tests/models/test_patient_document.py`,
  `backend/tests/repositories/test_patient_document.py`,
  `backend/tests/services/test_document.py`,
  `backend/tests/api/test_document_endpoints.py`) run against real
  PostgreSQL under the same savepoint isolation, PLUS a
  temporary-directory-backed `LocalDocumentStorage` (`local_storage`/
  `client_with_storage` fixtures, `tests/conftest.py`) — never the real
  configured `DOCUMENT_STORAGE_PATH`. `backend/tests/storage/` and
  `backend/tests/services/test_document_validation.py` need no database
  at all (pure filesystem-in-a-temp-dir and pure-function tests
  respectively) and run unconditionally.
- All prior stories' tests continue to pass unmodified by later stories,
  aside from two pre-existing tests (STORY-004) whose environment
  fixtures needed a synthetic `JWT_SECRET_KEY` added once the new
  staging/production-requires-a-JWT-secret validator (Section 10) landed
  (`tests/api/test_readiness_environment.py`) — a legitimate contract
  change, not a weakened test.

## 12. SQLite Limitations (Testing Only)

SQLite is used **only** for isolated, fast automated tests. It is
important not to over-trust it:

- **SQLite is not PostgreSQL.** It does not prove PostgreSQL-specific
  compatibility — different SQL dialect, different type system, different
  concurrency model, no `asyncpg`-specific connection behavior.
- PostgreSQL remains the only production/system-of-record database.
- The SQLite-based tests validate that our SQLAlchemy *usage patterns*
  (engine creation, session lifecycle, a real `SELECT 1` round-trip) work
  against a real database — not that PostgreSQL-specific SQL or behavior
  is correct.
- Separate, explicitly-labeled real-PostgreSQL tests exist for the cases
  where that distinction matters (see Section 11): the opt-in integration
  smoke test, and — as of STORY-003 — the full `Organization`/`Facility`
  domain model test suite, which deliberately does **not** use SQLite at
  all, precisely because constraint-level enforcement is exactly what's
  under test.
- **`Appointment`'s GiST `EXCLUDE` constraints (STORY-007) have no SQLite
  equivalent at all** — `sqlalchemy.dialects.postgresql.ExcludeConstraint`
  is a PostgreSQL-specific construct with no generic-SQL fallback.
  Importing `app.models.appointment` (and therefore `app.models`, and
  therefore anything that imports the model package) never requires a
  PostgreSQL connection — the constraint object is just Python metadata
  until DDL is actually compiled against a real dialect — but no test in
  this codebase ever runs `Base.metadata.create_all()` against SQLite for
  the full domain model (Section 11's SQLite tests only exercise
  `app.db.session`/`app.db.health` in isolation), so this never comes up
  in practice. Genuine concurrency safety is provable ONLY against real
  PostgreSQL — see `tests/db/test_appointment_concurrency.py`.

## 13. Secret Handling

- `DATABASE_URL` and `JWT_SECRET_KEY` are both `SecretStr` in `Settings`
  — never printed in full via `repr()`/`str()`/accidental logging of the
  settings object. `JWT_SECRET_KEY` is never logged anywhere, including
  by `app/auth/jwt.py` on encode/decode failure — see
  [RBAC.md](RBAC.md) and [SECURITY.md](../SECURITY.md).
- `app/db/health.py` never returns or logs the raw connection string; on
  failure it logs a generic message with `logger.exception(...)` (full
  traceback goes to server-side logs only) and returns only `True`/
  `False` to its caller.
- The `/api/v1/ready` API response never includes `DATABASE_URL`, host,
  port, credentials, or raw exception/stack-trace text — only the
  documented `status` enum values (Section 7).
- `alembic.ini` contains no credentials; `migrations/env.py` resolves the
  URL from `Settings` at runtime (Section 8).

## 14. Future Multi-Tenancy Considerations (PLANNED)

Not implemented. As noted in `docs/ARCHITECTURE.md` Section 10, the
specific tenant-isolation strategy (row-level scoping vs.
schema-per-tenant vs. database-per-tenant) is an open question to be
resolved via a dedicated ADR before the domain model is implemented. This
story's engine/session design does not assume or preclude any particular
approach.

## 15. Backup / Restore (PLANNED)

Not implemented. Backup strategy, retention policy, and restore/DR
procedures for the production PostgreSQL instance are not decided and are
deferred to a later, infrastructure-focused story. Nothing in this story
should be read as implying a backup strategy exists.

## 16. Production Operational Considerations (PLANNED)

Not implemented. The following are explicitly deferred, not decided, and
not implied by anything in this story: connection pool sizing/tuning
beyond SQLAlchemy's defaults, read replicas, failover, managed-PostgreSQL
provider choice, monitoring/alerting on database health, and migration
rollout process in CI/CD (CI doesn't exist yet — see `docs/README.md`).
