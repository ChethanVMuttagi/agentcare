# AgentCare Documentation

This directory is the home for AgentCare's project documentation. It is
being built incrementally, story by story, alongside the implementation —
documentation is not backfilled after the fact.

## Status

As of STORY-009 (Persistent Workflow Engine & Audit Trail), this index,
the [Architecture Decision Records](adr/README.md) process,
[ARCHITECTURE.md](ARCHITECTURE.md), [DATABASE.md](DATABASE.md),
[DOMAIN_MODEL.md](DOMAIN_MODEL.md), [RBAC.md](RBAC.md),
[PATIENTS.md](PATIENTS.md), [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md),
[APPOINTMENTS.md](APPOINTMENTS.md), [DOCUMENTS.md](DOCUMENTS.md), and
[WORKFLOWS.md](WORKFLOWS.md) exist. The remaining documents listed below
are **reserved and intentionally not yet created**. Each will be added
when the story that needs it is implemented, so that documentation never
describes functionality that doesn't exist yet.

## Planned Documents

| Document | Purpose |
|---|---|
| `PRODUCT.md` | Product vision, scope, and user-facing functionality |
| `API.md` | API surface, conventions, versioning |
| `AGENTS.md` | Agentic AI components: roles, responsibilities, boundaries |
| `TOOLS.md` | Tools exposed to agents and how they're governed |
| `SAFETY.md` | Safety constraints for an agentic healthcare-adjacent system |
| `SECURITY.md` | See [SECURITY.md](../SECURITY.md) at the repository root |
| `PRIVACY.md` | Data privacy handling and boundaries |
| `AUDIT.md` | A general-purpose security/compliance audit log, distinct from `WorkflowEvent`'s own workflow-lifecycle audit trail (see [WORKFLOWS.md](WORKFLOWS.md) Section 18) |
| `ERROR_HANDLING.md` | Error handling and failure-mode conventions |
| `OBSERVABILITY.md` | Logging, metrics, tracing strategy |
| `TESTING.md` | Test strategy across unit/integration/e2e layers |
| `DEPLOYMENT.md` | Deployment topology and process |
| `DEMO.md` | How to run/present the demo |
| `REQUIREMENTS_TRACEABILITY.md` | Mapping of stories/requirements to implementation and tests |

`RBAC.md` (identity, authentication, and authorization model) was
implemented in STORY-004, `PATIENTS.md` (administrative patient domain,
tenant ownership, self-access) in STORY-005,
`SCHEDULING_RESOURCES.md` (department/practitioner/availability
foundation) in STORY-006, `APPOINTMENTS.md` (appointment booking,
rescheduling, cancellation, and genuinely race-safe double-booking
prevention) in STORY-007, `DOCUMENTS.md` (secure administrative
document upload, storage abstraction, and lifecycle management) in
STORY-008, and `WORKFLOWS.md` (durable workflow-run/step/event state,
centralized lifecycle transitions, `SELECT ... FOR UPDATE` concurrency
safety, and an append-only audit trail — persistence and lifecycle
mechanics only, no LLM/agent/LangGraph integration yet) in STORY-009 —
see [RBAC.md](RBAC.md), [PATIENTS.md](PATIENTS.md),
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md),
[APPOINTMENTS.md](APPOINTMENTS.md), [DOCUMENTS.md](DOCUMENTS.md), and
[WORKFLOWS.md](WORKFLOWS.md) directly rather than the table above.

**Note on the `WORKFLOWS.md` filename**: this document was originally
reserved (in an earlier revision of this index) for "LangGraph workflows
/ orchestration design." STORY-009 claimed the filename for the
PERSISTENCE layer instead — `WorkflowRun`/`WorkflowStep`/`WorkflowEvent`
and their lifecycle mechanics, deliberately built with NO LLM, agent
framework, or LangGraph integration. A future story that adds real
agent/LangGraph orchestration on top of this persistence layer should
extend [WORKFLOWS.md](WORKFLOWS.md) itself (it already documents the
foundation that orchestration will build on) rather than introducing a
second, confusingly-named document.

## Documentation Principles

- Documentation is written when the corresponding capability is implemented,
  not speculatively ahead of it.
- Documents describe what **is**, distinguishing clearly from what is
  **planned**, mirroring the same discipline applied in the root
  [README.md](../README.md).
- Architecturally significant decisions are recorded as ADRs — see
  [docs/adr/README.md](adr/README.md) — rather than only being described
  in prose docs, so the reasoning behind a decision isn't lost over time.
