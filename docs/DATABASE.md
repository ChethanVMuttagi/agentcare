# AgentCare Database

This document describes the database foundation implemented in STORY-002,
extended in STORY-003 (the first real domain tables and migration), and
further extended in STORY-004 (identity/membership tables and a second
migration) — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the model itself
and [RBAC.md](RBAC.md) for the identity/authorization model these tables
back. It follows the same CURRENT vs. PLANNED discipline as
[ARCHITECTURE.md](ARCHITECTURE.md): everything described here as
implemented exists in the repository today; anything marked PLANNED does
not yet.

Four domain tables exist so far: `organizations`, `facilities`, `users`,
and `organization_memberships` — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md).
No other healthcare domain tables exist yet.

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
  and `app/models/user.py` and `app/models/membership.py` (STORY-004),
  are the real `Mapped[...]`-typed model classes — SQLAlchemy 2.x's
  native typed mapping (`Mapped[str]`, `mapped_column(...)`), not the
  legacy 1.x style. See [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the models
  themselves and `app/db/mixins.py` for the shared `UUIDPrimaryKeyMixin`/
  `TimestampMixin` they're built on.
- Enum-backed columns (`OrganizationType`, `FacilityType`, and — as of
  STORY-004 — `Role` on `OrganizationMembership`) use SQLAlchemy's `Enum`
  type with `native_enum=False` (VARCHAR + a real `CHECK` constraint, not
  a native PostgreSQL `ENUM` type) and `values_callable` (persist each
  member's lowercase `.value`, not its uppercase Python name) — see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 9 for the full rationale.

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
- `get_db_session()` is the FastAPI dependency shape future routes will
  use: it creates a session, `yield`s it, and closes it via `async with`.
  No route currently depends on it (no domain endpoints exist yet), but it
  exists and is tested so later stories can adopt it directly.
- `dispose_engine()` disposes the engine's connection pool if one was ever
  created, and is a safe no-op otherwise. It's called from the FastAPI
  `lifespan` handler in `app/main.py` on shutdown.

## 5. Transaction Ownership Philosophy

`get_db_session()` **does not auto-commit or auto-rollback**. It creates a
session, yields it, and closes it; any exception raised while the session
is in use propagates to the caller unmodified.

This is deliberate (see ADR-0002): once services and, later, multi-step
agent workflows exist, an implicitly auto-committing session per request
would make partial-failure behavior surprising. Instead:

- **Repositories** (PLANNED, not implemented) will perform persistence
  operations but will not arbitrarily commit business transactions.
- **Services/workflows** (PLANNED, not implemented) will own the
  transaction boundary explicitly — deciding when a unit of work commits
  or rolls back, potentially spanning multiple repository calls.

This story does not implement repositories or services; it only
establishes the session dependency they will eventually use.

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
  STORY-004 with `test_user.py`/`test_membership.py`) run exclusively
  against real PostgreSQL, same skip condition
  (`AGENTCARE_TEST_POSTGRES_URL`), because nearly everything meaningful
  about these models (uniqueness, composite unique constraints, FK
  `ON DELETE RESTRICT`, enum `CHECK` constraints) is database-enforced
  behavior SQLite doesn't replicate faithfully — see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 10 and Section 12 below.
  Each test runs inside a savepoint that's always rolled back
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
- All STORY-001/002/003 tests continue to pass unmodified by STORY-004,
  aside from two pre-existing tests whose environment fixtures needed a
  synthetic `JWT_SECRET_KEY` added once the new
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
