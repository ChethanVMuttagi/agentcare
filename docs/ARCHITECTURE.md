# AgentCare Architecture

This document describes AgentCare's backend architecture as of STORY-001
(Architecture & Python Backend Foundation). It clearly separates **CURRENT**
(what exists in the repository today) from **PLANNED** (direction only —
not implemented). See [README.md](../README.md) for the same distinction
applied to the project as a whole.

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

## 2. Current Architecture (STORY-001)

The backend is a single Python package (`backend/app/`) — a **modular
monolith**, not yet split into services or layers beyond what's needed
today:

```
backend/app/
├── main.py              # Application factory: create_app() -> FastAPI
├── api/v1/
│   ├── router.py         # Aggregates versioned API routes
│   └── endpoints/
│       └── health.py     # GET /api/v1/health, GET /api/v1/ready
├── core/
│   ├── config.py         # Settings (pydantic-settings), Environment enum
│   ├── logging.py        # Logging configuration
│   └── exceptions.py     # AppException base + global exception handlers
└── schemas/
    └── common.py          # ErrorResponse, HealthResponse, ReadinessResponse
```

What exists today:
- A FastAPI application built via an **application factory**
  (`create_app()`), not constructed at import time, so tests can create
  independent app instances and future startup logic has a clean place to
  live.
- A versioned API mounted under `/api/v1`, currently exposing only health
  and readiness endpoints.
- Typed, environment-driven configuration (`Settings`), with no database or
  LLM integration — those fields exist as optional configuration only.
- A structured logging foundation (level, timestamp, logger name, message)
  with an explicit rule against logging secrets or patient data.
- A standardized error response shape and global exception handling that
  never leaks stack traces or internal detail to clients.

What does **not** exist yet: any domain model, any database connection, any
LLM call, any agent, any authentication, any frontend.

## 3. Planned Architecture (NOT Implemented)

Everything in this section is direction, not current behavior.

- **PostgreSQL** as the system of record, accessed via SQLAlchemy 2.x
  models and versioned with Alembic migrations.
- **A service layer** between API routes and data access, encapsulating
  business rules so route handlers stay thin.
- **A repository layer** encapsulating database access behind an
  interface, so services (and, indirectly, agents) don't issue raw queries.
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
  that hold credentials use Pydantic's `SecretStr`, which masks the value
  in `repr()`/`str()` output.
- The application must be able to start, and pass health checks, **without**
  any LLM API key or database connection configured — those are optional at
  the configuration layer in STORY-001 because no integration exists yet
  that needs them.
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
- Fields that don't yet have a real integration (database, LLM providers,
  JWT signing) are optional, so the app can start for tests and health
  checks without them. They will become required, per-environment, once the
  corresponding integration is built.
- `get_settings()` is cached (`lru_cache`) and used as a FastAPI dependency,
  so settings are read once per process rather than re-parsed per request.

## 9. Testing Strategy

- Tests exercise the real FastAPI application (via `TestClient`), not
  mocks of the endpoint under test.
- Coverage as of STORY-001: application factory behavior, health/readiness
  endpoint responses and schema, configuration defaults and validation
  (including the production/debug safety rule), and the standardized error
  shape for an unhandled route.
- Test values (e.g. a JWT secret used only to test that `SecretStr` masking
  works) are synthetic and clearly non-production.
- As services, repositories, and workflows are introduced, this section
  will describe how unit, integration, and end-to-end layers divide.

## 10. Multi-Tenancy Direction (Planned)

Not implemented in STORY-001. AgentCare is expected to eventually serve
multiple healthcare organizations. The specific isolation strategy (e.g.
row-level tenant scoping vs. schema-per-tenant) is an open architectural
question to be resolved via an ADR before the domain model is implemented,
not assumed here.

## 11. Observability Direction (Planned)

STORY-001 provides only a structured logging foundation (level, timestamp,
logger name, message — see `app/core/logging.py`). Metrics, tracing, and
any external observability platform integration are not implemented and
are deferred to a later story, to be documented in `docs/OBSERVABILITY.md`
when built.
