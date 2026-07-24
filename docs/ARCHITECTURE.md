# AgentCare Architecture

This document describes AgentCare's backend architecture as of STORY-003
(Organization & Facility Tenancy Foundation), building on STORY-002
(PostgreSQL & Database Foundation) and STORY-001 (Architecture & Python
Backend Foundation). It clearly separates **CURRENT** (what exists in the
repository today) from **PLANNED** (direction only — not implemented).
See [README.md](../README.md) for the same distinction applied to the
project as a whole, [DATABASE.md](DATABASE.md) for the full
database-layer detail, and [DOMAIN_MODEL.md](DOMAIN_MODEL.md) for the
domain model itself — this document only summarizes both.

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

## 2. Current Architecture (STORY-001 + STORY-002 + STORY-003)

The backend is a single Python package (`backend/app/`) — a **modular
monolith**, not yet split into services or layers beyond what's needed
today:

```
backend/app/
├── main.py              # Application factory: create_app() -> FastAPI
│                         # + lifespan (disposes the DB engine on shutdown)
├── api/v1/
│   ├── router.py         # Aggregates versioned API routes
│   └── endpoints/
│       └── health.py     # GET /api/v1/health, GET /api/v1/ready
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
│   ├── organization.py    # Organization, OrganizationType
│   └── facility.py        # Facility, FacilityType
└── schemas/
    └── common.py          # ErrorResponse, HealthResponse, ReadinessResponse
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
- A structured logging foundation (level, timestamp, logger name, message)
  with an explicit rule against logging secrets or patient data.
- A standardized error response shape and global exception handling that
  never leaks stack traces or internal detail to clients — this also
  covers database errors (never exposing connection strings or driver
  exception text via the API).

What does **not** exist yet: any repository or service layer, any entity
beyond `Organization`/`Facility`, any CRUD API for those entities, any
LLM call, any agent, any authentication, any frontend.

## 3. Planned Architecture (NOT Implemented)

Everything in this section is direction, not current behavior.

- **A service layer** between API routes and data access, encapsulating
  business rules so route handlers stay thin.
- **A repository layer** encapsulating database access behind an
  interface (built on top of the `app/db/` session foundation from
  STORY-002 and the `Organization`/`Facility` models from STORY-003), so
  services (and, indirectly, agents) don't issue raw queries.
- **Further healthcare domain models** below the tenant hierarchy
  (patients, appointments, referrals, staff, documents, etc.), as
  SQLAlchemy 2.x `Mapped[...]` classes subclassing `app.db.base.Base` —
  `Organization`/`Facility` (STORY-003) are the first, not the last; see
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 12.
- **A CRUD API, service, and/or repository layer for `Organization` and
  `Facility` themselves** — STORY-003 established persistence only.
- **Tenant-access enforcement** (repository/service/auth-context level —
  see ADR-0003) once authentication exists.
- **LangGraph-based agent workflows** for coordination tasks (scheduling,
  intake, referrals, follow-ups), invoked through the service layer.
- **An LLM provider abstraction** so agents/workflows are not hardcoded to
  a single vendor (Groq / OpenAI / Anthropic).
- **RBAC** (role-based access control) for authenticated users and staff.
- **Docker** packaging for consistent local/dev/prod environments.
- **A Next.js frontend** consuming the versioned API.

## 4. Backend Layering Philosophy

The intended request flow, once later stories build it out, is:

```
Route (API layer)
  → Service (business rules / orchestration)
    → Workflow (LangGraph, for agentic coordination tasks)
      → Agent (a bounded, tool-using participant in a workflow)
        → Tool (a narrow, explicit capability an agent is allowed to invoke)
    → Repository (data access abstraction)
      → Database (PostgreSQL)
```

Routes stay thin: they parse/validate input, call a service, and shape the
response. Services hold business rules and decide whether a task needs a
workflow at all. Workflows orchestrate multi-step agent coordination.
Agents never bypass their tools to act directly on the system.

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
  belong under human supervision (see Section 8). Constraining agents to
  tools is what makes that boundary enforceable rather than aspirational.

## 6. Security Boundaries

- Secrets are never hardcoded and never logged (see
  [SECURITY.md](../SECURITY.md) and `app/core/logging.py`). Settings fields
  that hold credentials — including `DATABASE_URL` — use Pydantic's
  `SecretStr`, which masks the value in `repr()`/`str()` output.
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
  — see [DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 10 for what's covered
  and why SQLite isn't used here.
- Test values (e.g. a JWT secret used only to test that `SecretStr` masking
  works, or synthetic organization/facility names) are synthetic and
  clearly non-production.
- As services, repositories, and workflows are introduced, this section
  will describe how unit, integration, and end-to-end layers divide.

## 10. Multi-Tenancy Direction

**Decided (STORY-003, ADR-0003)**: `Organization` is AgentCare's tenant
boundary; `Facility` belongs to exactly one Organization via a required
FK. Every future tenant-owned entity is expected to carry
`organization_id`, directly or through an explicit ownership path. See
[DOMAIN_MODEL.md](DOMAIN_MODEL.md) Section 5 and ADR-0003 for the full
decision, including what is deliberately **not** yet enforced.

**Still planned**: there is no global, automatic tenant-access filter at
the query level, and none is assumed. That enforcement is deferred,
deliberately, to a future repository/service/auth-context layer — not
implicitly relied upon, not silently skipped. Row-level security and a
schema-per-tenant/database-per-tenant alternative were both considered
and deferred (not rejected) in ADR-0003, pending the authentication
design they'd depend on.

## 11. Observability Direction (Planned)

STORY-001 provides only a structured logging foundation (level, timestamp,
logger name, message — see `app/core/logging.py`). Metrics, tracing, and
any external observability platform integration are not implemented and
are deferred to a later story, to be documented in `docs/OBSERVABILITY.md`
when built.
