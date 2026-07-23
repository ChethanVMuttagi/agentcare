# Architecture Decision Records (ADRs)

## What Is an ADR?

An Architecture Decision Record captures a single significant architectural
or technical decision: the context that prompted it, the options
considered, the decision made, and its consequences. It's a short,
timestamped record of *why* the system is built the way it is — not just
*what* was built.

## When We Create One

Create an ADR when a decision:

- Is hard or costly to reverse (choice of database, core framework, auth
  model, agent orchestration approach, etc.)
- Affects multiple parts of the system or multiple future stories
- Involves a meaningful tradeoff that later contributors will wonder about
  ("why didn't we just use X?")
- Establishes a convention the rest of the codebase is expected to follow

Small, easily reversible implementation choices (variable naming, a helper
function's shape, etc.) do not need an ADR.

## Naming Convention

```
ADR-0001-short-title.md
ADR-0002-another-decision.md
```

- Sequential, zero-padded 4-digit number.
- Short, kebab-case title describing the decision.
- Numbers are never reused, even if an ADR is superseded or rejected.

## Lifecycle

Each ADR has a status: `Proposed`, `Accepted`, `Superseded`, or `Rejected`.

**Decisions are not silently rewritten after acceptance.** If circumstances
change and a decision needs to be revisited, write a *new* ADR that
supersedes the old one, and update the old ADR's status to `Superseded`
with a link to the new one. This preserves the historical reasoning instead
of erasing it.

## Suggested Template

```markdown
# ADR-XXXX: Title

Status: Proposed | Accepted | Superseded by ADR-YYYY | Rejected
Date: YYYY-MM-DD

## Context
What problem or situation prompted this decision?

## Decision
What did we decide to do?

## Alternatives Considered
What else did we look at, and why wasn't it chosen?

## Consequences
What becomes easier or harder as a result of this decision?
```

No ADRs have been recorded yet — the first will be added when STORY-000's
successor stories begin making architecturally significant choices.
