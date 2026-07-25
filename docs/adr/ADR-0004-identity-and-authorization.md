# ADR-0004: Identity, Membership & Authorization Foundation

Status: Accepted
Date: 2026-07-25

## Context

ADR-0003 established `Organization` as AgentCare's tenant boundary and
explicitly deferred authorization enforcement to "a later story" once
identity/authentication existed. STORY-004 is that story. Before any
patient data, appointments, documents, workflows, or agents are
introduced, AgentCare needs a backend-enforced identity, organization-
membership, and role-based authorization foundation — this is
security-critical infrastructure, not a UI feature (no login page is
built here).

We need to decide: how identity relates to tenancy (does a user belong to
one organization or can they belong to many?), how authentication proves
identity, what a token is and is not trusted for, how role/authorization
decisions are made and by what's authoritative, and how much of this to
build now vs. defer — consistent with this project's established
discipline of building only what the current story needs.

## Decision

We will use:

1. **`User` as a global identity**, not scoped to any single
   organization. Email + Argon2id password hash + active flag — nothing
   else (no unrelated PII).
2. **Organization membership modeled separately from identity**, via
   `OrganizationMembership` — a join between `User` and `Organization`,
   constrained to at most one membership per (organization, user) pair,
   carrying its own `Role` and `is_active` flag. A `User` may hold
   memberships in multiple organizations.
3. **`Role` stored on the membership**, not on the user — because role is
   inherently per-organization (the same person could be `STAFF` at one
   organization and have no relationship to another).
4. **JWTs identify the user only.** Access tokens carry exactly `sub`
   (user UUID), `iat`, `exp`, and `jti` — no email, no role, no
   organization membership, no permissions, no patient/medical data.
5. **The database remains authoritative for membership and role, always
   re-checked, never cached in or trusted from the token.**
   `get_current_membership` queries `OrganizationMembership` fresh on
   every request. A JWT is never treated as a permanent or even
   short-term source of truth for "what can this request do."
6. **Backend-enforced RBAC** via `require_roles(*allowed_roles)`, a
   dependency factory — not scattered, hand-rolled role checks inside
   individual route handlers.
7. **Argon2id for password hashing** (via `argon2-cffi`), **PyJWT** for
   tokens — both established, audited libraries; neither cryptographic
   primitive is implemented by hand.
8. **Stateless, short-lived access tokens for now.** No refresh tokens,
   no server-side session/revocation store in this story. `jti` is
   included specifically so a future revocation mechanism has something
   to key off of — nothing currently checks it.
9. **Patient self-access rules are deferred**, not designed here — there
   is no `Patient` entity yet for such rules to constrain access to.

## Rationale

- **Global `User` + separate `OrganizationMembership`**, rather than a
  single `organization_id` column on `User`, is the only design that
  correctly represents a user belonging to more than one organization —
  explicitly required by this story. Putting tenancy on the identity
  record itself would have made multi-organization membership
  structurally impossible without a schema change later.
- **Role on the membership, not the user**: makes "admin at Org A, no
  relationship to Org B" directly representable, and makes a future
  cross-organization permission model (should one ever be needed) an
  additive change rather than a restructuring.
- **JWT carries only `sub`**: the alternative — embedding role/membership
  in the token — would create a real security hazard: a role change or
  membership deactivation would not take effect until the token expired,
  meaning a deactivated staff member (or a downgraded admin) could
  continue acting with their old privileges for up to
  `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`. Re-resolving from the database on
  every request closes that gap entirely — a deactivation is effective on
  the very next request, not "eventually."
- **Uniform, generic failure responses** (Section 7 of
  `docs/RBAC.md`): both login failures (unknown email vs. wrong password)
  and authorization failures (no membership vs. inactive membership vs.
  wrong role vs. nonexistent organization) collapse to one indistinguishable
  response per category. This is a deliberate anti-enumeration measure —
  the alternative (distinct error messages/codes per case) would let an
  attacker map out which emails exist, which organizations exist, or
  which users have access to what, purely from response shape.
- **Argon2id, not bcrypt/PBKDF2/a hand-rolled scheme**: Argon2id is the
  Password Hashing Competition winner and OWASP's current recommendation,
  with configurable memory-hardness that makes GPU/ASIC-accelerated
  cracking meaningfully more expensive than older algorithms. `argon2-cffi`
  handles salting internally — there is no code in this repository that
  generates or manages a salt directly, removing an entire class of
  implementation mistakes.
- **Stateless tokens now, not because revocation doesn't matter, but
  because building a half-considered revocation/session store under time
  pressure would likely need to be redone once the real requirements
  (logout UX, "log out of all devices," admin-forced session termination)
  are understood. Short expiry is a real, if partial, mitigation in the
  meantime, and `jti`'s presence means the eventual mechanism doesn't
  require a token-format migration to add.
- **No permission engine yet**: `Role` is a small, closed enum, not a
  many-to-many permission graph. Building a general permission engine
  before there's a second real use case beyond "admin vs. staff vs.
  patient" would be speculative complexity this project's stated
  discipline (`docs/ARCHITECTURE.md` Section 1: "build what the current
  story needs, not what a future story might") explicitly argues against.

## Alternatives Considered

- **`organization_id` directly on `User`**: rejected — cannot represent a
  user belonging to multiple organizations, which this story explicitly
  requires supporting.
- **Embedding role/membership/permissions in the JWT** (a common pattern
  elsewhere): rejected for the staleness/security reason above. Some
  systems accept this tradeoff in exchange for not needing a DB lookup
  per request; we judged the security cost (delayed deactivation/role
  changes) too high for a healthcare-administration system, especially
  before any actual performance problem has been observed.
- **Session-based authentication (server-side session store, session
  cookie) instead of JWT**: would give free, immediate revocation, but
  introduces a stateful session store as infrastructure before this
  project has decided on/needs one, and doesn't obviously fit an eventual
  API-consumed-by-multiple-clients (web frontend, and potentially
  service-to-service or agent-initiated calls later) shape as cleanly as
  a bearer token. Deferred, not rejected outright — worth revisiting
  alongside the future revocation story.
- **A full permission-engine / policy table now** (permissions
  independently assignable per membership, not just a role): rejected as
  premature — no concrete requirement beyond "admin/staff/patient" exists
  yet, and this project does not build ahead of demonstrated need.
- **bcrypt or PBKDF2 instead of Argon2id**: both are acceptable,
  widely-used alternatives, but Argon2id is the more modern
  recommendation (OWASP, PHC winner) with better resistance to
  hardware-accelerated attacks; no reason to choose an older algorithm
  for a new system.
- **Refresh tokens now**: rejected as explicitly out of scope for this
  story — adds meaningful complexity (rotation, storage, revocation of
  refresh tokens themselves) that isn't justified before the base
  access-token flow is proven end-to-end.

## Consequences

- Every future organization-scoped route gets tenant enforcement "for
  free" by depending on `get_current_membership`/`require_roles` — the
  pattern is established and tested even though no such route exists yet
  in this story (mirroring how `app.db.session.get_db_session` was built
  ahead of its first consumer in STORY-002).
- Every request that resolves membership/role costs one additional
  database query beyond authenticating the user. This is a deliberate,
  accepted tradeoff (Rationale) — no caching of membership/role exists,
  and none should be added without explicitly revisiting the staleness
  argument above.
- Because there is no revocation store, an operator's only lever to end a
  user's access is deactivating the user (`is_active=False`), which takes
  effect immediately (`docs/RBAC.md` Section 9) but is a blunt instrument
  — it ends *all* of that user's sessions at once, not one specific
  session. There is no way to individually revoke a single leaked-but-
  still-valid token for an otherwise legitimate, still-active user; its
  exposure is bounded only by `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`. This is
  a known, accepted gap until a future revocation story closes it.
- The first story that introduces `Patient` must explicitly design
  patient self-access rules (`docs/RBAC.md` Section 10) — this ADR
  deliberately does not pre-decide that design.
- The first story that needs finer-grained permissions than
  "admin/staff/patient" must make that decision explicitly, via its own
  ADR, rather than incrementally overloading `Role`.
