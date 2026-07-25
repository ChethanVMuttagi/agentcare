# AgentCare

**Agentic AI for Patient Administration and Care Coordination**

AgentCare is a production-oriented SaaS foundation exploring how agentic AI
can support the *administrative and coordination* work around patient
care — scheduling, intake, referrals, follow-ups, and the communication
overhead that surrounds clinical work — without touching diagnosis or
treatment decisions.

> **AgentCare is NOT a diagnosis or treatment system.** It does not, and
> will not, generate medical diagnoses, treatment plans, or clinical
> decision-making output. Its scope is administrative and coordination
> support around healthcare delivery, not the practice of medicine.

## Project Vision

Healthcare administration involves a large amount of repetitive,
coordination-heavy work: scheduling, intake paperwork, referral
hand-offs, status follow-ups, and communication between patients, staff,
and providers. AgentCare's long-term vision is a system where agentic AI
workflows handle this coordination burden reliably, transparently, and
safely — with clear audit trails, human oversight where it matters, and
strict boundaries around what the system is and isn't allowed to do.

## Current Development Status

This project is being built **story by story**, starting from a secure,
well-documented repository foundation before any healthcare domain logic
or agent workflows exist.

**Implemented now:**
- Repository structure and directory layout (`backend/`, `frontend/`,
  `docs/`, `infrastructure/`, `scripts/`, `tests/`, `.github/`)
- Security-first `.gitignore` and safe `.env.example`
- Security policy ([SECURITY.md](SECURITY.md))
- Contribution guidelines ([CONTRIBUTING.md](CONTRIBUTING.md))
- Documentation foundation ([docs/README.md](docs/README.md)),
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
  [docs/DATABASE.md](docs/DATABASE.md),
  [docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md),
  [docs/RBAC.md](docs/RBAC.md), and the Architecture Decision Record
  process ([docs/adr/](docs/adr/README.md))
- PR template with security/safety checks
- **FastAPI backend application foundation** (`backend/app/`): an
  application factory with lifespan-managed resource cleanup, versioned
  API routing under `/api/v1`, health and readiness endpoints, typed
  environment-based configuration (`pydantic-settings`), a structured
  logging foundation, and a standardized API error/exception-handling
  architecture
- **PostgreSQL & database foundation** (`backend/app/db/`): async
  SQLAlchemy 2.x engine/session lifecycle (PostgreSQL via `asyncpg` in
  production), a real database connectivity check wired into
  `/api/v1/ready`, and Alembic migration infrastructure
  (`backend/alembic.ini`, `backend/migrations/`) — configured and
  credential-free
- **Organization & Facility tenancy foundation** (`backend/app/models/`):
  AgentCare's first real domain models — `Organization` (the tenant
  boundary) and `Facility` (belongs to one organization) — with UUID
  primary keys, enum-backed types enforced by real database `CHECK`
  constraints, a composite uniqueness constraint, and a foreign key with
  safe (`RESTRICT`) delete semantics. Backed by AgentCare's first real
  Alembic migration, applied to and validated against real PostgreSQL.
  See [docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md). No CRUD API,
  service, or repository layer exists for them yet.
- **Identity, membership & RBAC foundation** (`backend/app/models/user.py`,
  `backend/app/models/membership.py`, `backend/app/auth/`): a global
  `User` identity (Argon2id-hashed passwords), `OrganizationMembership`
  (a user's `Role` — `admin`/`staff`/`patient` — within one organization,
  `UNIQUE(organization_id, user_id)`), stateless JWT access tokens
  (`sub`/`iat`/`exp`/`jti` only — never role, email, or medical data), and
  backend-enforced authorization dependencies (`get_current_user`,
  `get_current_membership`, `require_roles`) that always re-resolve
  membership/role from the database, never from the token. A minimal auth
  API exists: `POST /api/v1/auth/token`, `GET /api/v1/auth/me`. Backed by
  AgentCare's second Alembic migration, applied to and validated against
  real PostgreSQL. See [docs/RBAC.md](docs/RBAC.md). No organization-scoped
  route uses these dependencies yet — no such route exists.
- Backend test suite (`backend/tests/`) exercising the real FastAPI app,
  real (SQLite-backed, for isolation) infrastructure-level database
  behavior, and the `Organization`/`Facility`/`User`/`OrganizationMembership`
  models, password hashing, JWT handling, and the auth API end-to-end
  against real PostgreSQL

**Not yet implemented** (planned, across future stories):
- A CRUD API, service, and repository layer for `Organization`/`Facility`
- Further domain models below the tenant hierarchy (patients,
  appointments, referrals, staff, documents, etc.), including patient
  self-access authorization rules
- Any organization-scoped route that actually depends on
  `get_current_membership`/`require_roles`; finer-grained permissions
  beyond `Role`; refresh tokens; token revocation; password reset; email
  verification; MFA; OAuth/social login; public user registration
- LangGraph-based agent workflows
- LLM provider abstraction (Groq / OpenAI / Anthropic)
- Next.js frontend
- Docker/containerization
- CI/CD workflows

Nothing in the sections below describes code that exists yet unless it's
explicitly listed above as implemented. Where this document discusses
agents, workflows, or features, it is describing **planned** direction, not
current behavior.

## Production-Oriented Architecture Philosophy

Although this project originates from the AgentCare Build Challenge 2026,
it is deliberately engineered as a foundation that can continue to exist
and evolve after the hackathon ends, not as disposable demo code. That
means:

- Security and secret hygiene are treated as first-class from commit one,
  not retrofitted later.
- Documentation is written alongside implementation, story by story, so the
  repository never silently drifts out of sync with what it describes.
- Architecturally significant decisions are recorded (see
  [docs/adr/](docs/adr/README.md)) instead of living only in someone's
  memory or a chat log.
- Features are built when their story is reached, not stubbed out ahead of
  time with placeholder implementations.

## Healthcare Administrative Scope

AgentCare's intended scope covers workflows such as:
- Patient administrative intake and record coordination
- Appointment scheduling and follow-up coordination
- Referral and hand-off tracking between care providers
- Status communication between patients, staff, and providers

It explicitly does **not** cover clinical diagnosis, treatment
recommendation, or any function that constitutes the practice of medicine.

## Technology Stack

| Layer | Technology | Status |
|---|---|---|
| Backend API | Python, FastAPI | Implemented |
| Configuration | Pydantic Settings | Implemented |
| Database | PostgreSQL | Implemented (4 tables — see below) |
| ORM | SQLAlchemy 2.x | Implemented (`Organization`, `Facility`, `User`, `OrganizationMembership`) |
| Migrations | Alembic | Implemented (2 migrations, validated against real PostgreSQL) |
| Authentication | Argon2id password hashing + JWT (PyJWT) | Implemented (`POST /auth/token`, `GET /auth/me`) |
| Authorization | Backend-enforced RBAC (`require_roles`) | Implemented (primitives only — not yet wired to any route) |
| Agent Orchestration | LangGraph | Planned |
| LLM Access | Provider-abstracted (Groq / OpenAI / Anthropic) | Planned |
| Frontend | Next.js | Planned |
| Containerization | Docker | Planned |

The database layer is implemented and tested, but scoped narrowly:
`organizations`, `facilities`, `users`, and `organization_memberships`
are the only tables that exist, there is no CRUD API for
`Organization`/`Facility`, and no further healthcare domain model
(patients, appointments, etc.) exists yet — see
[docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) and
[docs/RBAC.md](docs/RBAC.md).

## Repository Structure

```
agentcare/
├── backend/             # FastAPI application foundation
│   ├── app/
│   │   ├── main.py       # Application factory (+ lifespan): create_app()
│   │   ├── api/v1/       # Versioned API routing + endpoints (health/ready/auth)
│   │   ├── auth/          # Password hashing, JWT, auth service, auth dependencies
│   │   ├── core/         # Settings, logging, exceptions
│   │   ├── db/            # Async SQLAlchemy engine/session, DB health check
│   │   ├── models/         # Organization, Facility, User, OrganizationMembership
│   │   └── schemas/      # Shared Pydantic schemas (errors, health, auth)
│   ├── migrations/        # Alembic migrations (organizations+facilities; users+memberships)
│   ├── alembic.ini
│   ├── tests/            # Backend test suite
│   └── pyproject.toml    # Backend dependencies + tool configuration
├── frontend/            # Next.js application (not yet implemented)
├── docs/                # Project documentation, see docs/README.md
│   ├── ARCHITECTURE.md   # Current + planned backend architecture
│   ├── DATABASE.md        # Database foundation: engine, sessions, migrations
│   ├── DOMAIN_MODEL.md    # Domain model, tenant hierarchy, identity
│   ├── RBAC.md            # Identity, authentication, authorization model
│   └── adr/              # Architecture Decision Records
├── infrastructure/      # Deployment/infra config (not yet implemented)
├── scripts/             # Developer/operational scripts (not yet implemented)
├── tests/               # Cross-cutting/integration tests (not yet implemented)
├── .github/
│   ├── workflows/       # CI/CD workflows (not yet added)
│   └── pull_request_template.md
├── .env.example         # Safe, placeholder-only environment template
├── .gitignore
├── SECURITY.md
├── CONTRIBUTING.md
└── README.md
```

## Backend Setup (Local Development)

Requires **Python 3.12**. Commands below use PowerShell (current
development happens on Windows).

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy ..\.env.example .env    # creates backend\.env; then edit it with your own local values
uvicorn app.main:app --reload
```

The app starts, and its test suite runs, without any database or LLM
credentials configured — `DATABASE_URL` is optional for local development
and testing. In `staging`/`production`, however, an unconfigured database
makes `/api/v1/ready` report `not_ready` (HTTP 503) rather than silently
passing — see [docs/DATABASE.md](docs/DATABASE.md) for the full
per-environment readiness semantics. Once running:

- Health: `http://127.0.0.1:8000/api/v1/health`
- Readiness: `http://127.0.0.1:8000/api/v1/ready`
- Interactive API docs: `http://127.0.0.1:8000/docs`

### Database (PostgreSQL, optional for most local work)

Set `DATABASE_URL` in `backend/.env` to a real PostgreSQL instance (using
the async `asyncpg` driver scheme) to exercise the database layer:

```
DATABASE_URL=postgresql+asyncpg://agentcare_user:changeme@localhost:5432/agentcare_dev
```

Then, from `backend/`:

```powershell
alembic upgrade head              # apply all pending migrations
alembic revision --autogenerate -m "add <thing>"
alembic current                   # show current DB revision
```

`alembic upgrade head` creates the `organizations`/`facilities` tables
(AgentCare's tenant hierarchy) and the `users`/`organization_memberships`
tables (identity and role-based membership — see
[docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) and
[docs/RBAC.md](docs/RBAC.md)). See [docs/DATABASE.md](docs/DATABASE.md)
for the full migration workflow and testing strategy (SQLite is used only
for isolated infrastructure-level tests; PostgreSQL remains the
production database and is what the domain model and auth test suites run
against).

Run the test suite and quality checks from `backend/`:

```powershell
pytest
ruff check .
mypy app
```

## Security Warning

**This is a public repository.** Never commit `.env` files, API keys,
database credentials, tokens, or real patient data of any kind. See
[SECURITY.md](SECURITY.md) for the full policy, including what to do if a
secret is ever accidentally committed. All data used in development,
testing, and demos must be synthetic or fully anonymized.

## Documentation

Project documentation lives in [docs/](docs/README.md), including the
Architecture Decision Record process in [docs/adr/](docs/adr/README.md).
Most documents referenced there are intentionally not yet created — see
that index for what's planned and why.

## Development Approach

AgentCare is developed **story by story**: each story delivers a narrowly
scoped, complete slice of work (implemented, tested, documented, and
security-checked) before the next story begins. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the Definition of Done and
contribution workflow.

## Hackathon Context

This repository originates from the **AgentCare Build Challenge 2026**.
While the hackathon provides the initial occasion and timeline for this
work, the repository is deliberately structured and documented as a
production-oriented foundation rather than a throwaway hackathon
submission — the goal is for it to remain a sound base for continued
development afterward.
