# AgentCare Documentation

This directory is the home for AgentCare's project documentation. It is
being built incrementally, story by story, alongside the implementation —
documentation is not backfilled after the fact.

## Status

As of STORY-001 (architecture & Python backend foundation), this index, the
[Architecture Decision Records](adr/README.md) process, and
[ARCHITECTURE.md](ARCHITECTURE.md) exist. The remaining documents listed
below are **reserved and intentionally not yet created**. Each will be
added when the story that needs it is implemented, so that documentation
never describes functionality that doesn't exist yet.

## Planned Documents

| Document | Purpose |
|---|---|
| `PRODUCT.md` | Product vision, scope, and user-facing functionality |
| `DOMAIN_MODEL.md` | Core domain entities and their relationships |
| `DATABASE.md` | Schema design, migrations strategy, data lifecycle |
| `API.md` | API surface, conventions, versioning |
| `AGENTS.md` | Agentic AI components: roles, responsibilities, boundaries |
| `TOOLS.md` | Tools exposed to agents and how they're governed |
| `WORKFLOWS.md` | LangGraph workflows / orchestration design |
| `RBAC.md` | Roles, permissions, and access control model |
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

## Documentation Principles

- Documentation is written when the corresponding capability is implemented,
  not speculatively ahead of it.
- Documents describe what **is**, distinguishing clearly from what is
  **planned**, mirroring the same discipline applied in the root
  [README.md](../README.md).
- Architecturally significant decisions are recorded as ADRs — see
  [docs/adr/README.md](adr/README.md) — rather than only being described
  in prose docs, so the reasoning behind a decision isn't lost over time.
