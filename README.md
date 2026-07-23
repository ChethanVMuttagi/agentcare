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
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and the Architecture
  Decision Record process ([docs/adr/](docs/adr/README.md))
- PR template with security/safety checks
- **FastAPI backend application foundation** (`backend/app/`): an
  application factory, versioned API routing under `/api/v1`, health and
  readiness endpoints, typed environment-based configuration
  (`pydantic-settings`), a structured logging foundation, and a
  standardized API error/exception-handling architecture
- Backend test suite (`backend/tests/`) exercising the real FastAPI app

**Not yet implemented** (planned, across future stories):
- PostgreSQL schema and SQLAlchemy 2.x models
- Alembic migrations
- Domain models (patients, appointments, referrals, etc.)
- Authentication and RBAC
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

## Planned Technology Stack (High-Level)

| Layer | Planned Technology |
|---|---|
| Backend API | Python, FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy 2.x |
| Migrations | Alembic |
| Configuration | Pydantic Settings |
| Agent Orchestration | LangGraph |
| LLM Access | Provider-abstracted (Groq / OpenAI / Anthropic) |
| Frontend | Next.js |
| Containerization | Docker |

None of the above is installed or implemented yet — this table describes
direction, not current state.

## Repository Structure

```
agentcare/
├── backend/             # FastAPI application foundation
│   ├── app/
│   │   ├── main.py       # Application factory: create_app() -> FastAPI
│   │   ├── api/v1/       # Versioned API routing + endpoints (health/ready)
│   │   ├── core/         # Settings, logging, exceptions
│   │   └── schemas/      # Shared Pydantic schemas (errors, health)
│   ├── tests/            # Backend test suite
│   └── pyproject.toml    # Backend dependencies + tool configuration
├── frontend/            # Next.js application (not yet implemented)
├── docs/                # Project documentation, see docs/README.md
│   ├── ARCHITECTURE.md   # Current + planned backend architecture
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

The app starts without any database or LLM credentials configured — none
are integrated yet in this story. Once running:

- Health: `http://127.0.0.1:8000/api/v1/health`
- Readiness: `http://127.0.0.1:8000/api/v1/ready`
- Interactive API docs: `http://127.0.0.1:8000/docs`

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
