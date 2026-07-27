# AgentCare Documentation

This directory is the home for AgentCare's project documentation. It is
being built incrementally, story by story, alongside the implementation —
documentation is not backfilled after the fact.

## Status

As of STORY-008 (Secure Document Management), this index, the
[Architecture Decision Records](adr/README.md) process,
[ARCHITECTURE.md](ARCHITECTURE.md), [DATABASE.md](DATABASE.md),
[DOMAIN_MODEL.md](DOMAIN_MODEL.md), [RBAC.md](RBAC.md),
[PATIENTS.md](PATIENTS.md), [SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md),
[APPOINTMENTS.md](APPOINTMENTS.md), and [DOCUMENTS.md](DOCUMENTS.md)
exist. The remaining documents listed below are **reserved and
intentionally not yet created**. Each will be added when the story that
needs it is implemented, so that documentation never describes
functionality that doesn't exist yet.

## Planned Documents

| Document | Purpose |
|---|---|
| `PRODUCT.md` | Product vision, scope, and user-facing functionality |
| `API.md` | API surface, conventions, versioning |
| `AGENTS.md` | Agentic AI components: roles, responsibilities, boundaries |
| `TOOLS.md` | Tools exposed to agents and how they're governed |
| `WORKFLOWS.md` | LangGraph workflows / orchestration design |
| `SAFETY.md` | Safety constraints for an agentic healthcare-adjacent system |
| `SECURITY.md` | See [SECURITY.md](../SECURITY.md) at the repository root |
| `PRIVACY.md` | Data privacy handling and boundaries |
| `AUDIT.md` | Audit logging and traceability of agent/user actions |
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
prevention) in STORY-007, and `DOCUMENTS.md` (secure administrative
document upload, storage abstraction, and lifecycle management) in
STORY-008 — see [RBAC.md](RBAC.md), [PATIENTS.md](PATIENTS.md),
[SCHEDULING_RESOURCES.md](SCHEDULING_RESOURCES.md),
[APPOINTMENTS.md](APPOINTMENTS.md), and [DOCUMENTS.md](DOCUMENTS.md)
directly rather than the table above.

## Documentation Principles

- Documentation is written when the corresponding capability is implemented,
  not speculatively ahead of it.
- Documents describe what **is**, distinguishing clearly from what is
  **planned**, mirroring the same discipline applied in the root
  [README.md](../README.md).
- Architecturally significant decisions are recorded as ADRs — see
  [docs/adr/README.md](adr/README.md) — rather than only being described
  in prose docs, so the reasoning behind a decision isn't lost over time.
