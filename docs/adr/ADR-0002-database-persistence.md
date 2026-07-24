# ADR-0002: Database & Persistence Foundation

Status: Accepted
Date: 2026-07-24

## Context

ADR-0001 established PostgreSQL as the planned system of record but
deferred the actual database foundation. STORY-002 needs to establish that
foundation — engine/session lifecycle, migrations, connectivity health
checking — without yet introducing any healthcare domain model, agent, or
authentication logic (those remain out of scope per STORY-002's explicit
boundaries).

We need to decide: the database driver and access pattern, how the app
manages engine/session lifecycle without eagerly connecting at import
time, how migrations are configured and kept credential-free, how
readiness reporting reflects real (not faked) database state, and how
automated tests exercise real database behavior without requiring every
contributor to run a local PostgreSQL instance.

## Decision

We will use:

1. **PostgreSQL** as the production relational database — the system of
   record for all future domain data.
2. **SQLAlchemy 2.x**, using its modern async ORM/Core APIs
   (`DeclarativeBase`, `AsyncEngine`, `AsyncSession`) — not the legacy 1.x
   style.
3. **Async database access end-to-end**, via the `asyncpg` driver
   (`postgresql+asyncpg://`), consistent with FastAPI's async request
   handling.
4. **Alembic** for schema migrations, configured under `backend/`
   (`alembic.ini`, `migrations/`), reading `DATABASE_URL` from the same
   `Settings`/environment mechanism the application uses — never
   embedding credentials in `alembic.ini` or any committed file.
5. **Application-controlled transaction boundaries**: the database-session
   FastAPI dependency (`get_db_session`) creates a session, yields it, and
   closes it — it never auto-commits. Services/workflows (introduced in
   later stories) will own the commit/rollback decision explicitly.
6. **No direct agent database access.** As already established in
   ADR-0001/`docs/ARCHITECTURE.md`, agents will interact with data only
   through tools; this ADR does not change that boundary, it builds the
   database layer those tools will eventually call into via a repository
   layer (not yet implemented).
7. **SQLite, only for isolated automated tests**, via the `aiosqlite`
   driver — never for production, and not treated as proof of
   PostgreSQL-specific compatibility. A separate, opt-in PostgreSQL
   integration test exists for that purpose (skipped unless a real
   instance is provided via an environment variable).

## Rationale

- **SQLAlchemy 2.x + asyncpg** keeps the whole request path async,
  matching FastAPI, and gives us typed `Mapped[...]` model definitions
  when domain models are eventually introduced, without a second ORM or
  framework.
- **Lazy, cached engine creation** (see `app/db/session.py`) means
  importing application modules — or running the test suite — never
  requires a live PostgreSQL connection. This is essential for STORY-002's
  own requirement that the app remain importable/testable without a
  production database.
- **No auto-commit in the session dependency** is a deliberate choice to
  avoid a footgun: once multi-step agent workflows exist, an implicitly
  auto-committing session would make partial-failure behavior surprising
  and hard to reason about. Making the transaction boundary an explicit,
  later decision (owned by services/workflows) keeps that behavior
  predictable from day one.
- **Alembic reading `DATABASE_URL` from `Settings` at runtime** (rather
  than a hardcoded `sqlalchemy.url` in `alembic.ini`) keeps the migration
  tool credential-free and consistent with the rest of the app's
  configuration strategy (ADR-0001, `docs/ARCHITECTURE.md` Section 8).
- **SQLite for fast, isolated tests** avoids forcing every contributor to
  install and run PostgreSQL locally just to run the test suite, while
  still exercising real SQLAlchemy execution (not mocks) for the
  session/health-check code paths. We explicitly do not treat this as
  proof that PostgreSQL-specific behavior works — see `docs/DATABASE.md`
  for the documented limitation and the separate, opt-in PostgreSQL
  integration test.
- **Truthful readiness reporting**: the `/api/v1/ready` database check
  performs a real `SELECT 1`, never a faked result, and reports
  `not_configured` (a benign, non-failure state) when no `DATABASE_URL` is
  set, distinct from `unavailable` (configured but unreachable — which is
  what actually flips overall readiness to `not_ready`, returned with
  HTTP 503). This lets local/test environments run without a database
  while still making a real, misconfigured production deployment
  detectable.

## Alternatives Considered

- **Synchronous SQLAlchemy (`psycopg2`/`psycopg`) instead of async**:
  simpler in some respects, but would mean blocking database calls inside
  an otherwise async FastAPI app (or routing every DB call through a
  thread pool), and would diverge from the async direction the rest of the
  stack (LangGraph, LLM calls) is expected to need. Rejected in favor of
  async end-to-end.
- **An auto-committing session dependency** (commit on successful request,
  rollback on exception): simpler for basic CRUD, but decided against
  because it hands the transaction boundary to the web-framework layer
  instead of the service/workflow layer — which becomes actively wrong
  once a single request can span multiple logical steps (e.g. an agent
  workflow). Rejected; transaction ownership is deferred to
  services/workflows explicitly.
- **Embedding `sqlalchemy.url` directly in `alembic.ini`**: the
  conventional Alembic quickstart default, but this would either require
  committing a placeholder that developers forget to change, or a
  developer-specific uncommitted `alembic.ini`, which is messier than
  reading `DATABASE_URL` the same way the application already does.
  Rejected.
- **Requiring a real PostgreSQL instance for the test suite** (e.g. via
  Docker Compose in CI): more faithful to production, but Docker isn't
  introduced until a later story, and forcing a local PostgreSQL
  dependency for `pytest` today would make the test suite unusable for
  contributors without it installed. Rejected for now; SQLite-based
  isolated tests plus an opt-in PostgreSQL integration test (skipped by
  default) is the interim strategy, revisited once Docker/CI exist.
- **Introducing a repository layer or domain models now** to "prove" the
  database layer end-to-end: rejected as out of scope for STORY-002,
  which is explicitly infrastructure-only — consistent with ADR-0001's
  decision to not stub out layers ahead of need.

## Consequences

- Future stories that add domain models have a working, tested engine and
  session foundation to build on immediately, and a documented pattern
  (`migrations/env.py`) for registering new model modules with Alembic
  autogenerate.
- Because transaction ownership is deferred, the first story that
  introduces a service or repository layer must explicitly decide and
  document commit/rollback behavior — it is not already decided by this
  ADR beyond "not automatic in the session dependency."
- The test suite's reliance on SQLite for most database tests means a
  PostgreSQL-only behavior regression (dialect-specific SQL, asyncpg-only
  connection edge cases) could pass locally without a real PostgreSQL
  check. The opt-in integration test mitigates this but is not run by
  default; this is a known, accepted gap until CI/Docker (a later story)
  make a real PostgreSQL instance available by default.
- Alembic migration infrastructure exists with zero migrations. The first
  actual migration (and the domain model it accompanies) belongs to the
  story that introduces the first domain model, not this one.
