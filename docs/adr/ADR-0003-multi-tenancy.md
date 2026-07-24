# ADR-0003: Multi-Tenancy Foundation (Organization & Facility)

Status: Accepted
Date: 2026-07-25

## Context

AgentCare is multi-tenant by design: it will eventually serve multiple
healthcare organizations from one deployment. STORY-002 built the
database infrastructure (engine, sessions, migrations) but introduced no
domain model. STORY-003 needs to introduce AgentCare's first real
persistence model and decide, concretely, what the tenant boundary is,
how ownership is represented, and — critically — what is and is not
enforced yet, so that decision doesn't get made implicitly or
inconsistently by whichever future story happens to add the next entity.

`docs/ARCHITECTURE.md` (Section 10, "Multi-Tenancy Direction") already
flagged that the isolation strategy was an open question to be resolved
via an ADR before the domain model was implemented. This is that ADR.

## Decision

We will use:

1. **Organization as AgentCare's tenant boundary.** An Organization
   represents the customer (a hospital, clinic, hospital group, or other
   healthcare provider organization).
2. **Facility belongs to exactly one Organization**, via a required,
   indexed foreign key (`facilities.organization_id`) with
   `ON DELETE RESTRICT`.
3. **Application-generated UUID primary keys** for both entities (see
   `docs/DOMAIN_MODEL.md` Section 4), consistent with the general
   preference for portable, app-controlled identifiers over
   database-generated ones absent a compelling PostgreSQL-specific
   reason.
4. **Explicit tenant ownership, not implicit.** Every future tenant-owned
   entity is expected to carry `organization_id`, directly or through an
   explicit ownership path (e.g. via a facility) — not inferred from
   context, session state, or convention.
5. **No global, "magical" tenant filter in STORY-003 or by default.** We
   are not introducing a query-level mechanism that automatically scopes
   every query to "the current organization." No such mechanism exists
   yet, and none is silently assumed.
6. **Future authorization/repository layers will enforce tenant access
   deliberately**, once they exist. Tenant-scoping is treated as a
   first-class responsibility of the repository/service/auth-context
   layers introduced in later stories — not something developers are
   expected to remember to bolt onto every query by hand indefinitely,
   and not something retrofitted as an afterthought either.
7. **Database constraints provide the foundational ownership integrity
   available today**: the FK makes an orphaned facility structurally
   impossible; `ON DELETE RESTRICT` makes deleting an organization that
   still owns facilities fail loudly instead of cascading silently or
   succeeding incorrectly. This is real, present-day enforcement — it is
   just not the same thing as request-level tenant-access authorization,
   which doesn't exist yet.

## Rationale

- **Organization (not Facility, not some other unit) as the tenant
  boundary** matches how AgentCare is actually expected to be sold and
  operated: a hospital group signs up as one customer and may have many
  physical facilities under it, not the reverse.
- **Explicit `organization_id` over implicit context** keeps ownership a
  visible, queryable, testable property of the data itself. A row's
  tenant is a fact you can read off the row (or its FK path), not
  something that only exists in whatever session/request context happened
  to create it.
- **No global tenant filter yet, deliberately**: implementing one now,
  before there is any authentication, authorization, or even a second
  entity that needs it, would mean guessing at a cross-cutting mechanism
  (session-variable-based row-level security? a SQLAlchemy query event
  that injects a `WHERE organization_id = ...`? something else?) without
  the authorization model that should actually drive that design. Getting
  this wrong early would be expensive to unwind. We would rather build it
  once, deliberately, when the authorization/repository layers that need
  to police it actually exist (a later story), than retrofit or guess now.
- **Not relying on developers to "remember" tenant filters forever
  either**: this ADR explicitly does not conclude "just be careful to add
  `WHERE organization_id = ...` everywhere." That is not a sustainable
  security posture for a healthcare-administration system. The intended
  resolution is a deliberate repository/service-layer mechanism in a
  later story — this ADR names that as the destination so it doesn't get
  silently skipped.
- **`ON DELETE RESTRICT` over `CASCADE`**: an accidental or automated
  organization deletion must never be able to silently take an unknown
  number of facilities (and, later, everything under them) with it.
  Requiring an explicit, separate decision to remove dependent facilities
  first is the safer default for a foundational multi-tenant system,
  consistent with `docs/DOMAIN_MODEL.md` Section 8's cascade discussion.

## Alternatives Considered

- **Row-level security (RLS) via PostgreSQL policies, enforced now**:
  would give strong database-level tenant isolation, but requires a
  concrete way to communicate "current tenant" to the database session
  (e.g. `SET app.current_organization_id`), which in turn requires an
  authentication/session model that doesn't exist yet. Building RLS
  policies against a guessed-at session mechanism now would likely need
  to be redone once real auth exists. Deferred, not rejected outright —
  worth revisiting once authentication is designed.
- **A SQLAlchemy-level automatic tenant filter** (e.g. a `Query`/`Select`
  event that injects `organization_id` filtering globally): rejected for
  STORY-003 for the same reason as RLS — there is no authenticated
  "current tenant" concept yet to filter by, and building the filter
  mechanism before the thing it depends on exists risks a mismatch. This
  remains a candidate for the later authorization story, alongside RLS.
- **Schema-per-tenant or database-per-tenant isolation**: much stronger
  physical isolation, but a significantly heavier operational model
  (migration fan-out across N schemas/databases, connection routing) that
  isn't justified at this stage and isn't precluded by anything in this
  ADR — `docs/ARCHITECTURE.md` Section 10 keeps this open for a future
  ADR if/when it's needed.
- **Facility as the tenant boundary instead of Organization**: rejected —
  doesn't match the intended commercial/operational model (one customer,
  potentially many facilities).
- **`ON DELETE CASCADE` on the Organization → Facility FK**: rejected as
  unsafe by default for a foundational multi-tenant table; see Rationale.

## Consequences

- Every future tenant-owned entity's design must answer "how does this
  reach `organization_id`?" as part of its own definition — either a
  direct column or a documented ownership path through an existing
  owned entity (e.g. via `Facility`). This ADR does not automatically
  make that correct for future entities; each new one still needs its own
  deliberate ownership design.
- Until the authorization/repository layer described in Decision #6 is
  built, **no code in this repository enforces tenant access at the
  query level** — the only enforcement that exists today is structural
  (FK integrity, `ON DELETE RESTRICT`). Any future code that queries
  these tables directly (there is none yet — see `docs/DOMAIN_MODEL.md`
  Section 12) is responsible for its own filtering until that layer
  exists. This is a known, accepted, temporary gap, not an oversight.
- The choice not to implement RLS or a query-level filter now means the
  first story that introduces authentication/authorization must also
  explicitly decide how tenant-scoping is enforced — that decision is
  deferred to that story's own ADR, not pre-made here.
