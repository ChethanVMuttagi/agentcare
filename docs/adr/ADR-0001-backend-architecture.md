# ADR-0001: Backend Architecture Foundation

Status: Accepted
Date: 2026-07-24

## Context

AgentCare needs a backend foundation established before any healthcare
domain logic (patients, appointments, referrals) or agentic workflows are
built. The project must be production-oriented — it is expected to keep
evolving after the AgentCare Build Challenge 2026 — while also staying
narrowly scoped: STORY-001 is architecture and application skeleton only,
not domain modeling or agent implementation.

We need to decide the backend language/framework, how the application is
constructed and configured, how the API is versioned, and what the
long-term layering and data-access shape should be, even though most of
that shape won't be built until later stories.

## Decision

We will use:

1. **Python 3.12 with FastAPI** as the backend web framework.
2. **An application factory** (`create_app() -> FastAPI`) rather than
   constructing the app at module import time, so app instances can be
   created independently (tests, future multi-config startup) and startup
   logic has one clear place to live.
3. **A versioned API** mounted under `/api/v1`, with routes aggregated in
   `app/api/v1/router.py` and grouped by resource under
   `app/api/v1/endpoints/`.
4. **A layered architecture**, introduced incrementally as later stories
   need each layer: routes → services → (workflows/agents, where
   applicable) → repositories → database. Only the routes/core/schemas
   layers exist as of this ADR; services, repositories, workflows, and
   agents do not exist yet and will not be stubbed out ahead of need.
5. **PostgreSQL, planned** as the system of record, accessed later via
   SQLAlchemy 2.x models and Alembic migrations. No database is connected
   as of this ADR.
6. **Agent/tool isolation, planned**: when agents are introduced, they will
   act only through explicit tools, never through direct database sessions
   or repository calls (see `docs/ARCHITECTURE.md` Section 5).
7. **A production-oriented modular monolith** as the starting shape: one
   deployable backend service, internally organized into clear modules
   (`api`, `core`, `schemas`, and later `services`, `repositories`,
   `agents`), rather than splitting into multiple services or introducing
   a plugin/microservice architecture prematurely.

## Rationale

- **FastAPI** gives us async support, typed request/response models via
  Pydantic, and automatic OpenAPI documentation — all useful for an API
  that will grow a versioned surface and eventually document agent-facing
  and human-facing endpoints alike.
- **Application factory** avoids import-time side effects, which matters
  once startup needs to do more (future: DB engine creation, LangGraph
  workflow registration) and keeps tests able to build isolated app
  instances.
- **Modular monolith over microservices, initially**: at this stage a
  single deployable service is simpler to build, test, and reason about.
  Splitting into services before there's a concrete scaling or ownership
  reason would add operational overhead without a corresponding benefit.
- **Layering deferred until needed**: creating empty `services/`,
  `repositories/`, or `agents/` packages now would misrepresent the
  project's actual state and invite dead scaffolding to drift out of date
  before it's ever used.
- **Agent/tool isolation decided early, even though agents don't exist
  yet**, because it is a safety-relevant boundary (see the healthcare
  safety boundary in `docs/ARCHITECTURE.md` Section 7) that is much easier
  to hold to if it's decided before any agent code is written, rather than
  retrofitted afterward.

## Alternatives Considered

- **Django / Django REST Framework**: more batteries-included, but heavier
  and more opinionated than needed for an API-first backend that will
  integrate with LangGraph and an LLM abstraction layer; FastAPI's async
  support and lighter footprint fit better.
- **Flask**: viable, but lacks FastAPI's built-in typed request/response
  validation and OpenAPI generation, which we'd otherwise have to add via
  extensions.
- **Constructing the FastAPI app at module level** (`app = FastAPI()`
  directly in `main.py`, no factory): simpler initially, but makes it
  harder to create isolated instances for testing and to extend startup
  behavior cleanly later; rejected in favor of the factory pattern.
- **Introducing empty `services/`/`repositories/`/`agents/` packages now**
  to "show the intended shape": rejected — this repository's convention
  (see `CONTRIBUTING.md` and `docs/README.md`) is to build documentation
  and structure alongside real implementation, not ahead of it.
- **Microservices from the start**: rejected as premature — no current
  requirement justifies the added operational complexity.

## Consequences

- Later stories that add services, repositories, or agents have a clear,
  already-decided place to put them and a already-documented flow to
  follow (`docs/ARCHITECTURE.md` Section 4), reducing the chance of
  inconsistent layering choices per-story.
- The agent/tool isolation boundary is now a committed architectural
  decision, not an afterthought — introducing an agent that bypasses tools
  to hit the database directly would be a deviation from this ADR, not a
  neutral implementation choice.
- Because no database or service layer exists yet, this ADR alone does not
  make the backend feature-complete; it only fixes the shape future stories
  build within. Significant new decisions (e.g. multi-tenancy strategy,
  specific LLM provider abstraction design) will be recorded as their own
  superseding or additive ADRs when those stories begin.
