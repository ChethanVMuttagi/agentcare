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
  [docs/RBAC.md](docs/RBAC.md), [docs/PATIENTS.md](docs/PATIENTS.md),
  [docs/SCHEDULING_RESOURCES.md](docs/SCHEDULING_RESOURCES.md),
  [docs/APPOINTMENTS.md](docs/APPOINTMENTS.md),
  [docs/DOCUMENTS.md](docs/DOCUMENTS.md),
  [docs/WORKFLOWS.md](docs/WORKFLOWS.md),
  [docs/AI_SAFETY.md](docs/AI_SAFETY.md), [docs/TOOLS.md](docs/TOOLS.md),
  [docs/AGENTS.md](docs/AGENTS.md), and the Architecture Decision Record
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
  real PostgreSQL. See [docs/RBAC.md](docs/RBAC.md).
- **Patient domain, self-access & tenant-safe API**
  (`backend/app/models/patient.py`, `backend/app/repositories/patient.py`,
  `backend/app/services/patient.py`, `backend/app/api/v1/endpoints/patients.py`):
  `Patient`, an ADMINISTRATIVE record (no clinical/medical data) belonging
  to exactly one organization, with an optional, validated link to a
  `User` portal identity. The first tenant-scoped repository (every
  function requires an explicit `organization_id`) and the first service
  to own a real mutating transaction. The first route to actually enforce
  `get_current_membership`/`require_roles`: `ADMIN`/`STAFF` may
  create/list/get patients; a `PATIENT`-role membership may reach only
  its own linked record via a dedicated `GET .../patients/me`, never an
  arbitrary id. Cross-tenant patient lookups return the same "not found"
  response as a nonexistent id. Backed by AgentCare's third Alembic
  migration, applied to and validated against real PostgreSQL. See
  [docs/PATIENTS.md](docs/PATIENTS.md).
- **Department, practitioner & availability foundation**
  (`backend/app/models/department.py`, `practitioner.py`,
  `practitioner_department.py`, `practitioner_availability.py`; their
  repositories/services; `backend/app/api/v1/endpoints/departments.py`,
  `practitioners.py`): `Department` (belongs to a `Facility`, which MUST
  share its `Organization` — enforced at the DATABASE level via a
  composite foreign key, not just application validation),
  `Practitioner` (a schedulable healthcare professional — deliberately
  not named `Doctor`), a many-to-many practitioner-department assignment,
  and recurring weekly availability windows (not materialized
  appointment slots). Tenant/facility/assignment ownership integrity is
  database-enforced throughout. `ADMIN` may create departments/
  practitioners/assignments; `ADMIN`/`STAFF` may list/get and manage
  availability; `PATIENT` may not reach any of it in this story. Backed
  by AgentCare's fourth Alembic migration, applied to and validated
  against real PostgreSQL. This story does not implement appointments.
  See [docs/SCHEDULING_RESOURCES.md](docs/SCHEDULING_RESOURCES.md).
- **Appointment booking engine**
  (`backend/app/models/appointment.py`; its repository/services;
  `backend/app/api/v1/endpoints/appointments.py`, plus a new
  available-times route on the existing `practitioners.py` router):
  `Appointment` — a concrete, dated booking of a `Patient` against a
  `Practitioner`'s recurring availability, within a `Department`.
  Recurring availability is converted into concrete bookable UTC times
  on demand, never materialized as slot rows. **Double-booking (both
  practitioner-side and patient-side) is prevented at the DATABASE
  level via genuinely race-safe PostgreSQL `EXCLUDE` constraints
  (`btree_gist`)** — proven under real, independently-executing
  concurrent transactions (`asyncio.gather`, two separate database
  connections), not a SELECT-then-INSERT pre-check. `ADMIN`/`STAFF` may
  book/view/reschedule/cancel any organization appointment; `PATIENT`
  may do the same for their OWN appointments only, with their identity
  always derived server-side from their linked `Patient` record — a
  patient can never supply another patient's id to act on their behalf.
  Backed by AgentCare's fifth Alembic migration (plus the `btree_gist`
  PostgreSQL extension), applied to and validated against real
  PostgreSQL. See [docs/APPOINTMENTS.md](docs/APPOINTMENTS.md) and
  [docs/adr/ADR-0007-appointment-concurrency.md](docs/adr/ADR-0007-appointment-concurrency.md).
- **Secure document management**
  (`backend/app/models/patient_document.py`; `backend/app/storage/`
  (the storage abstraction); its repository/service;
  `backend/app/api/v1/endpoints/documents.py`): `PatientDocument` —
  administrative document METADATA and a storage reference; **file bytes
  are never stored in PostgreSQL** (no `BLOB`/`bytea` column exists).
  Uploaded files are treated as **untrusted input**: validated by
  magic-byte signature against a small allowlist (PDF/JPEG/PNG — never
  file extension or client-declared `Content-Type` alone), size-bounded
  and SHA-256-hashed DURING streaming (never fully buffered first), and
  written to storage under a server-generated OPAQUE key a client can
  never choose or see. `app.storage.base.DocumentStorage` is a narrow
  interface the service depends on, never a concrete backend directly —
  `LocalDocumentStorage` (filesystem-backed, LOCAL DEVELOPMENT ONLY) is
  the only implementation this story provides. A deliberate three-phase
  upload state machine (`pending -> available`/`failed`) reconciles
  PostgreSQL and object storage not sharing a transaction; a database
  `CHECK` constraint makes "available without a persisted object" a
  schema-level impossibility. `ADMIN`/`STAFF` may
  upload/list/get/download/delete any patient's documents; `PATIENT` may
  upload/list/get/download only their OWN documents and can never
  delete one (a deliberate, conservative policy). Downloads are served
  with safe headers (`Content-Disposition: attachment`,
  `X-Content-Type-Options: nosniff`) and never expose a storage path.
  **Malware scanning is explicitly NOT implemented** — signature
  validation is not malware scanning, and this is documented as a
  deployment-integration boundary, not silently assumed away. Backed by
  AgentCare's sixth Alembic migration, applied to and validated against
  real PostgreSQL. See [docs/DOCUMENTS.md](docs/DOCUMENTS.md) and
  [docs/adr/ADR-0008-document-storage-and-security.md](docs/adr/ADR-0008-document-storage-and-security.md).
- **Persistent workflow engine & audit trail**
  (`backend/app/models/workflow.py`; its repositories/service;
  `backend/app/api/v1/endpoints/workflows.py`): `WorkflowRun` (one
  administrative request), `WorkflowStep` (one unit of work within a
  run), and `WorkflowEvent` (an append-only audit trail) — durable,
  PostgreSQL-backed state for future multi-step agent execution,
  **persistence and lifecycle mechanics only** (no LLM, no agent
  framework, no LangGraph, no autonomous decision-making yet). Run- and
  step-level lifecycle transitions follow centralized state machines;
  **two workers can never both incorrectly advance the same workflow's
  state** — enforced via `SELECT ... FOR UPDATE` row locking, proven
  under real, genuinely concurrent transactions (not a
  SELECT-then-UPDATE pre-check), with a losing caller deterministically
  rejected. Every transition and its corresponding audit event commit
  atomically. Tenant/patient/initiator-membership/step/event ownership
  is enforced at the DATABASE level via composite foreign keys,
  including a 3-column composite FK proving an event's linked step
  belongs to the SAME run. Correlation ids are always server-generated
  (never client-chosen); `safe_metadata` and failure fields are
  size-bounded and explicitly documented as never permitted to hold a
  raw prompt, chain-of-thought, or exception. Durability is proven
  directly: a workflow created via one database engine survives that
  engine being fully disposed and a brand-new one built from scratch.
  `ADMIN`/`STAFF` may create/list/get/cancel any organization workflow
  (and inspect its steps/events); `PATIENT` may do the same for their
  OWN workflows only, and can never cancel one. Backed by AgentCare's
  seventh Alembic migration, applied to and validated against real
  PostgreSQL. See [docs/WORKFLOWS.md](docs/WORKFLOWS.md) and
  [docs/adr/ADR-0009-durable-workflow-state.md](docs/adr/ADR-0009-durable-workflow-state.md).
- **Safe LLM & tool-calling foundation** (`backend/app/ai/`;
  `backend/app/api/v1/endpoints/agent.py`): the first story to
  introduce an LLM — a provider-independent `LLMProvider` abstraction
  and one real provider (**Anthropic Claude**, via the official SDK,
  using its tool-use feature for genuinely structured output, never
  prose parsed by regex). **The LLM is treated as fully untrusted**: its
  output is validated into one of four strongly-typed decisions
  (`tool_call`/`clarification_required`/`safe_response`/`refusal`),
  every variant structurally rejecting unknown fields — including a
  smuggled chain-of-thought/reasoning field, which fails the ENTIRE
  decision, not just that field. A deterministic, code-level safety
  policy refuses symptom-based or medication/dosage requests BEFORE the
  model is ever called (no "I have chest pain, which department should
  I see?" ever reaches the model, or results in autonomous clinical
  routing). An explicit, allowlisted `ToolRegistry` (a plain dict lookup
  — never `getattr`/`eval`/`exec`/dynamic import/shell execution) backs
  two real tools (`check_availability`, `book_appointment`), each
  calling the REAL existing service layer — no fake success path. A
  SERVER-CREATED execution context (never model-constructed) carries
  the only trusted identity data any tool acts on: for a `PATIENT`
  caller, their own linked patient id ALWAYS wins over anything a model
  decision supplies as an argument, even if asked to book for another
  patient. One model decision leads to AT MOST one tool execution — no
  autonomous multi-step loop. Every execution is fully persisted via the
  workflow engine above (one new event type, `tool_invoked`); no raw
  request text, prompt, or provider response is ever persisted. Backed
  by AgentCare's eighth Alembic migration. See
  [docs/AI_SAFETY.md](docs/AI_SAFETY.md), [docs/TOOLS.md](docs/TOOLS.md),
  and
  [docs/adr/ADR-0010-llm-and-tool-security-boundary.md](docs/adr/ADR-0010-llm-and-tool-security-boundary.md).
- **Genuine multi-agent coordination** (`app/ai/agents/`,
  `app/ai/coordinator_decisions.py`): a Coordinator agent that decides
  whether to hand off to ONE of three genuinely distinct specialists
  (Scheduling, Document, Routing) — not a renamed copy of a single
  universal agent. The Coordinator's decision type structurally CANNOT
  express a tool call (no such variant exists in its schema); each
  specialist has its own system prompt and its own tool allowlist,
  enforced in application code BEFORE the tool registry is ever
  consulted — proven directly by tests that a Document specialist
  cannot call `book_appointment`, a Scheduling specialist cannot call
  `list_patient_documents`, and a Routing specialist cannot call
  `book_appointment`. Two new tools (`list_patient_documents`,
  `resolve_department`), each calling the real existing service/
  repository layer. A successful handoff is durably persisted (a new
  `agent_handoff` audit event plus a second `WorkflowStep`) — never
  fabricated. Still ONE Coordinator decision, AT MOST one handoff, AT
  MOST one specialist decision, AT MOST one tool execution — no
  recursion, no specialist-to-specialist handoff, no autonomous loop.
  Backed by AgentCare's ninth Alembic migration. See
  [docs/AGENTS.md](docs/AGENTS.md) and
  [docs/adr/ADR-0011-multi-agent-coordination.md](docs/adr/ADR-0011-multi-agent-coordination.md).
- Backend test suite (`backend/tests/`) exercising the real FastAPI app,
  real (SQLite-backed, for isolation) infrastructure-level database
  behavior, all fourteen domain models, password hashing, JWT handling,
  the auth API, the full patient/department/practitioner/availability/
  appointment/document/workflow/AI-tool/multi-agent repository/service/API
  layers end-to-end against real PostgreSQL, dedicated real-concurrency
  tests proving both the appointment double-booking guarantee AND the
  workflow-transition race guarantee under genuinely concurrent
  transactions, a dedicated persistence/restart proof for workflow
  state, TWO mandatory end-to-end proofs that an AI-assisted patient
  request genuinely persists real state and a full multi-agent workflow
  audit trail — one via a Scheduling handoff, one via a Document
  handoff, so the architecture is proven not to be hardwired only for
  scheduling — a full adversarial/security test suite (hostile tool
  names, prompt-injection attempts including cross-agent injection
  phrases, cross-tenant/cross-patient rejection, malformed model output,
  cross-agent permission-denial cases), and filesystem-storage/
  file-signature tests using only temporary directories (never a
  tracked path) — every AI-related test uses a deterministic fake LLM
  provider, never a real network call or API key

**Not yet implemented** (planned, across future stories):
- A CRUD API, service, and repository layer for `Organization`/`Facility`
- Appointment completion workflow (clinical-encounter concept), waitlists
- Malware scanning and a production (e.g. S3-compatible) object-storage
  backend for documents — see [docs/DOCUMENTS.md](docs/DOCUMENTS.md)
  Section 20
- Race-proof (database exclusion constraint) AVAILABILITY-WINDOW-overlap
  prevention (as opposed to appointment-overlap prevention, which IS
  race-proof — see [docs/APPOINTMENTS.md](docs/APPOINTMENTS.md));
  general-purpose patient-readable department/practitioner discovery
  (a scoped available-times discovery endpoint IS implemented)
- Further domain models below the tenant hierarchy (referrals, staff
  profiles, etc.)
- Reschedule/cancel AI tools (see [docs/TOOLS.md](docs/TOOLS.md)
  Section 6 for why this is a deliberate depth-over-breadth choice)
- Patient update/delete; finer-grained permissions beyond `Role`; refresh
  tokens; token revocation; password reset; email verification; MFA;
  OAuth/social login; public user registration
- Unrestricted multi-step planning, specialist-to-specialist delegation,
  LangGraph-based orchestration, and autonomous decision loops — the
  multi-agent foundation above (STORY-011) implements ONE Coordinator
  decision leading to AT MOST one handoff and AT MOST one specialist
  tool execution; open-ended multi-step planning is a future story's
  work, built on top of this foundation, not a redesign of it
- A general-purpose security/compliance audit system (distinct from
  `WorkflowEvent`'s own workflow-lifecycle audit trail — see
  [docs/WORKFLOWS.md](docs/WORKFLOWS.md) Section 18)
- A second real LLM provider (Groq / OpenAI) — the provider abstraction
  supports adding one without touching the decision/safety/tool layers,
  but only Anthropic is implemented so far
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
| Database | PostgreSQL | Implemented (14 domain tables — see below) |
| ORM | SQLAlchemy 2.x | Implemented (14 models, incl. composite-FK ownership integrity + GiST `EXCLUDE` constraints) |
| Migrations | Alembic | Implemented (9 migrations, validated against real PostgreSQL) |
| Authentication | Argon2id password hashing + JWT (PyJWT) | Implemented (`POST /auth/token`, `GET /auth/me`) |
| Authorization | Backend-enforced RBAC (`require_roles`) | Implemented and enforced on the patient + scheduling-resource + appointment + document + workflow + agent APIs |
| Repository/Service layers | `app/repositories/`, `app/services/` | Implemented for `Patient`, scheduling resources, `Appointment`, `PatientDocument`, and `WorkflowRun`/`Step`/`Event`; not yet for `Organization`/`Facility` |
| Document storage | `app/storage/` (`DocumentStorage` abstraction) | Implemented — filesystem-backed `LocalDocumentStorage` (local dev only); production object-storage backend planned |
| Multi-Agent Coordination | `app/ai/agents/` (Coordinator + 3 specialists) | Implemented — see [docs/AGENTS.md](docs/AGENTS.md). No LangGraph adopted (see [ADR-0011](docs/adr/ADR-0011-multi-agent-coordination.md)) |
| LLM Access | Provider-abstracted (`app/ai/providers/`) | Implemented — Anthropic Claude; a second provider (Groq/OpenAI) needs a new adapter only, no contract change |
| Frontend | Next.js | Planned |
| Containerization | Docker | Planned |

The database layer is implemented and tested, but scoped narrowly:
`organizations`, `facilities`, `users`, `organization_memberships`,
`patients`, `departments`, `practitioners`, `practitioner_departments`,
`practitioner_availability`, `appointments`, and `patient_documents` are
the only tables that exist, and there is no CRUD API for
`Organization`/`Facility` — see
[docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md), [docs/RBAC.md](docs/RBAC.md),
[docs/PATIENTS.md](docs/PATIENTS.md),
[docs/SCHEDULING_RESOURCES.md](docs/SCHEDULING_RESOURCES.md),
[docs/APPOINTMENTS.md](docs/APPOINTMENTS.md), and
[docs/DOCUMENTS.md](docs/DOCUMENTS.md).

## Repository Structure

```
agentcare/
├── backend/             # FastAPI application foundation
│   ├── app/
│   │   ├── main.py       # Application factory (+ lifespan): create_app()
│   │   ├── api/v1/       # Versioned API routing + endpoints (health/ready/auth/
│   │   │                 # patients/departments/practitioners/appointments/documents)
│   │   ├── auth/          # Password hashing, JWT, auth service, auth dependencies
│   │   ├── core/         # Settings, logging, exceptions
│   │   ├── db/            # Async SQLAlchemy engine/session, DB health check
│   │   ├── models/         # Organization, Facility, User, OrganizationMembership,
│   │   │                   # Patient, Department, Practitioner, PractitionerDepartment,
│   │   │                   # PractitionerAvailability, Appointment, PatientDocument
│   │   ├── repositories/   # Tenant-scoped persistence/query only, per resource
│   │   ├── services/       # Business rules + transaction ownership, per resource
│   │   ├── storage/       # DocumentStorage abstraction + LocalDocumentStorage
│   │   └── schemas/      # Shared Pydantic schemas (errors, health, auth, patient,
│   │                     # department, practitioner, availability, appointment, document)
│   ├── migrations/        # Alembic migrations (org+facility; users+memberships;
│   │                       # patients; department+practitioner+availability;
│   │                       # appointments + btree_gist; patient_documents)
│   ├── alembic.ini
│   ├── tests/            # Backend test suite
│   └── pyproject.toml    # Backend dependencies + tool configuration
├── frontend/            # Next.js application (not yet implemented)
├── docs/                # Project documentation, see docs/README.md
│   ├── ARCHITECTURE.md   # Current + planned backend architecture
│   ├── DATABASE.md        # Database foundation: engine, sessions, migrations
│   ├── DOMAIN_MODEL.md    # Domain model, tenant hierarchy, identity
│   ├── RBAC.md            # Identity, authentication, authorization model
│   ├── PATIENTS.md        # Administrative patient domain, tenant ownership, self-access
│   ├── SCHEDULING_RESOURCES.md  # Department/Practitioner/availability domain
│   ├── APPOINTMENTS.md    # Appointment booking, concurrency safety, RBAC
│   ├── DOCUMENTS.md       # Secure document upload, storage abstraction, RBAC
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
(AgentCare's tenant hierarchy), the `users`/`organization_memberships`
tables (identity and role-based membership — see
[docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) and
[docs/RBAC.md](docs/RBAC.md)), the `patients` table (administrative
patient records — see [docs/PATIENTS.md](docs/PATIENTS.md)), the
`departments`/`practitioners`/`practitioner_departments`/
`practitioner_availability` tables (administrative scheduling resources
— see [docs/SCHEDULING_RESOURCES.md](docs/SCHEDULING_RESOURCES.md)), the
`appointments` table plus the `btree_gist` PostgreSQL extension
(genuinely race-safe booking, rescheduling, and cancellation — see
[docs/APPOINTMENTS.md](docs/APPOINTMENTS.md)), and the
`patient_documents` table (administrative document metadata — never
file bytes — plus a storage reference; see
[docs/DOCUMENTS.md](docs/DOCUMENTS.md)), the `workflow_runs`/
`workflow_steps`/`workflow_events` tables (durable workflow-lifecycle
state and an append-only audit trail — see
[docs/WORKFLOWS.md](docs/WORKFLOWS.md)), and two further migrations
extending `workflow_events` only (no new table): a `sequence` ordering
column and one new event type, `tool_invoked`, for the safe LLM &
tool-calling foundation (see
[docs/AI_SAFETY.md](docs/AI_SAFETY.md)/[docs/TOOLS.md](docs/TOOLS.md)),
and one more new event type, `agent_handoff`, for genuine multi-agent
coordination (see [docs/AGENTS.md](docs/AGENTS.md)).
See [docs/DATABASE.md](docs/DATABASE.md) for the full migration workflow
and testing strategy (SQLite is used only for isolated infrastructure-level
tests; PostgreSQL remains the production database and is what every
domain model, repository, service, and API test suite — including
dedicated real-concurrency tests for both appointment booking and
workflow transitions, and the two mandatory end-to-end multi-agent
proofs (Scheduling handoff and Document handoff) — runs against;
document storage tests use only pytest-managed temporary directories,
never the real configured `DOCUMENT_STORAGE_PATH`; AI/multi-agent tests
use a deterministic fake LLM provider, never a real network call or API
key).

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
