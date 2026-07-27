# ADR-0009: Durable Workflow State

Status: Accepted

Date: 2026-07-27

## Context

Every AgentCare capability built through STORY-008 (patients,
appointments, scheduling resources, documents) is a direct,
single-request CRUD-shaped operation: a client asks for one thing, the
service validates and persists it, done. STORY-009 is the first story
to anticipate MULTI-STEP execution — a future agent (LLM-driven or
otherwise) carrying out an administrative request across several calls,
potentially with retries, waits, and failures in between. That
execution state cannot live in Python process memory, a browser
session, or an in-memory dictionary: a process restart, a second
load-balanced instance, or a routine deploy must never destroy
in-flight workflow history.

This story is explicitly scoped to PERSISTENCE AND LIFECYCLE MECHANICS
ONLY — no LLM client, no agent framework, no LangGraph, no prompts, no
autonomous decision-making. The central design questions it had to
resolve: what is the minimal durable data model that supports a future
multi-step agent without over-building a general BPM platform today?
How is "two workers must not both incorrectly advance the same
workflow" actually prevented, not just documented? How does a
transition and its audit event get recorded atomically? What may — and,
more importantly, may NEVER — be written into a "metadata" field once
future stories start attaching agent/tool activity to these rows? And
what durability guarantee can this story actually prove, rather than
merely assert?

## Decision

1. **Three tables, not a BPM platform**: `WorkflowRun` (one
   administrative request), `WorkflowStep` (one unit of work within a
   run), `WorkflowEvent` (an append-only audit/history record). No
   generic workflow-definition DSL, no branching/parallel-execution
   graph, no speculative scheduler/queue.
2. **A deliberately small, administrative `WorkflowRequestType`
   allowlist** (`appointment_booking`, `appointment_rescheduling`,
   `appointment_cancellation`, `document_collection`,
   `administrative_routing`, `follow_up`) — no diagnosis/treatment
   category exists or may be added without a fresh, explicit design
   decision.
3. **Centralized, explicit state-machine tables** for both
   `WorkflowStatus` and `StepStatus`
   (`_ALLOWED_RUN_TRANSITIONS`/`_ALLOWED_STEP_TRANSITIONS` in
   `app/services/workflow.py`) — never scattered `if` logic across
   routes, mirroring the transition-table discipline
   `AppointmentService` established in STORY-007.
4. **Concurrency safety via `SELECT ... FOR UPDATE` row locking**, not
   an optimistic version column. Every transition locks its row before
   validating and applying the transition; a losing concurrent caller
   blocks on the lock, then observes the now-current status and is
   rejected deterministically as `WorkflowConflictError` (409).
5. **Transition and its corresponding audit event commit atomically**,
   in the same database transaction, every time — never a status change
   without its event, or vice versa.
6. **Correlation ids are always server-generated** (`uuid4().hex`,
   32 characters) — no request schema in this story accepts one as
   client input. **Idempotency keys, when supplied, are unique per
   organization** (not globally) — a foundation, not a full
   idempotent-retry framework.
7. **Database-enforced tenant/patient/initiator-membership/step/event
   ownership integrity**, using the composite-FK technique established
   since STORY-006 — including a novel **3-column composite FK**
   (`WorkflowEvent` → `WorkflowStep`, keyed on
   `organization_id + workflow_run_id + workflow_step_id`) proving an
   event's linked step belongs to the SAME run, not merely the same
   organization.
8. **`safe_metadata` (`WorkflowEvent`, JSONB) is bounded to 2000 bytes
   at the database level** and is explicitly documented as never
   permitted to hold: a raw LLM prompt or response, chain-of-thought or
   any hidden model reasoning, full patient request text, credentials/
   tokens, or an arbitrary/unbounded tool payload. No HTTP input surface
   in this story writes to it at all.
9. **Failure metadata is a bounded `(failure_code, failure_message_safe)`
   pair, never a raw exception.** `fail_workflow`/`fail_step`'s
   signatures do not accept an exception object; nothing in
   `WorkflowService` serializes `str(exc)`/`repr(exc)` into either
   field.
10. **`WorkflowEvent` is append-only by construction**: its repository
    (`app/repositories/workflow_event.py`) exposes only `create` and
    `list_by_run` — no `update`, no `delete` function exists for any
    caller to reach for. Enforced through application architecture, not
    a database trigger.
11. **The administrative API exposes creation, read, and cancellation
    only** — `start`/`mark_waiting`/`resume`/`complete`/`fail`, and
    every step-level transition, are internal `WorkflowService`
    operations a future orchestrator calls in-process, never HTTP
    endpoints a client (or a future agent bypassing intended
    orchestration) could trigger directly.

## Rationale

- **Three tables, not a platform**: the story's own instructions were
  explicit that a full BPM/workflow-engine model would be over-building
  for what's actually needed — durable state for sequential,
  human-or-agent-initiated administrative tasks. Run/Step/Event is the
  smallest model that supports "what is this request doing, what has it
  done, and why did it end up where it is" without inventing generic
  workflow-definition machinery nothing in this codebase's roadmap
  currently needs.
- **`SELECT ... FOR UPDATE` over an optimistic version column**: an
  optimistic (compare-and-swap) approach requires every caller to
  implement its own retry-on-conflict loop, and under contention wastes
  work computing a transition that then gets thrown away and redone.
  Row-level pessimistic locking makes the loser's rejection immediate
  and deterministic (no wasted recomputation, no retry loop required at
  the call site), and mirrors the concurrency-safety-by-design
  philosophy STORY-007 already established with PostgreSQL `EXCLUDE`
  constraints for appointment-overlap prevention — adapted here to a
  single-row state-transition problem instead of an overlap-detection
  problem. For the transaction volumes a workflow-lifecycle table
  experiences (state transitions, not high-frequency writes), the
  minor throughput cost of a row lock is the right trade for
  correctness that doesn't depend on every future caller getting a
  retry loop right.
- **One `WorkflowConflictError` for both a genuine invalid transition
  and a lost race**: once the row lock is held, the current status is
  authoritative. Distinguishing "you made a logic error" from "you lost
  a race" would require the caller to reason about timing that, from
  outside the transaction, it cannot actually observe — both cases
  reduce to the same fact ("this transition is not valid right now"),
  so a single 409 is the honest response, not an artificial refinement.
- **Server-generated correlation ids, tenant-scoped idempotency**: a
  client-chosen correlation id would let a client influence a
  security-adjacent tracing identifier, and a globally-unique
  idempotency key would leak information across tenant boundaries
  (whether "this key is already used" is observable cross-tenant).
  Both risks are closed by construction: correlation ids never accept
  client input at all, and idempotency uniqueness is scoped exactly to
  where the resource itself is scoped — the organization.
- **The 3-column event↔step composite FK**: without it, an event could
  be constructed (by raw SQL, or a future bug) pointing at a step that
  belongs to a DIFFERENT run within the same organization — a subtle
  cross-run data-integrity bug that a mere 2-column
  `(organization_id, workflow_step_id)` FK would not catch. Extending
  the same composite-FK-ownership-integrity technique this codebase has
  used since STORY-006 by one more column was a small addition for a
  guarantee otherwise unavailable without an extra service-level check
  that raw SQL could still bypass.
- **`safe_metadata`'s explicit prohibition list, decided now**: this
  story deliberately writes down what must never go into this column
  BEFORE any future LLM-integration story starts attaching real agent/
  tool activity to it. Deciding this in the abstract, before there's
  pressure to "just log the prompt for debugging," is safer than
  relying on a future story under different priorities to remember the
  constraint unprompted.
- **Failure metadata as a bounded pair, not an exception**: a raw
  exception's `repr()`/`str()` can unpredictably contain a connection
  string, a SQL statement with bound parameter values, a file path, or
  other operational detail never meant for an API response or an audit
  row a `PATIENT`-role caller might eventually see reflected back
  through workflow inspection. Forcing every failure path through a
  deliberately short, pre-classified `(code, message)` pair makes "what
  could this field contain" a closed, auditable question instead of "it
  depends on what exception happened to be raised."
- **Append-only via architecture, not a database trigger**: the story's
  own guidance was explicit — enforce immutability structurally where
  practical, and do not overengineer database triggers unless strongly
  justified. A repository module that simply never defines `update`/
  `delete` functions achieves the practical goal (no application code
  path can mutate or remove an event) with zero additional database
  objects to maintain, migrate, or reason about; a trigger would add
  defense against a threat model (a determined actor with direct SQL
  access bypassing the ORM entirely) this story's scope does not
  target.
- **No HTTP transition endpoints**: exposing `start`/`complete`/etc. as
  routes would let ANY authorized API caller — not just the intended
  future orchestrator — drive a workflow's lifecycle directly,
  defeating whatever sequencing/business logic a future agent framework
  is supposed to own. Keeping those as `WorkflowService` methods only,
  callable in-process, keeps the HTTP surface honestly scoped to what
  this story described: administrative creation/inspection/testing.

## Alternatives Considered

- **A general-purpose workflow/BPM engine model** (states-and-transitions
  defined as configurable data, branching/parallel step graphs):
  considered and rejected — explicitly out of scope per the story's own
  instructions, and nothing in AgentCare's current or near-term roadmap
  needs more than sequential steps within one run.
- **Optimistic concurrency (a `version` integer column, compare-and-swap
  update)**: considered — would have worked, and is a legitimate pattern
  elsewhere. Declined in favor of `SELECT ... FOR UPDATE` for the
  reasons in Rationale (immediate deterministic rejection, no
  caller-implemented retry loop required, consistency with STORY-007's
  concurrency philosophy).
- **Client-supplied correlation ids**: rejected — a security-adjacent
  tracing identifier should never be something a client can choose,
  predict, or collide with another tenant's.
- **A full idempotent-retry framework** (automatic "return the existing
  resource instead of erroring" behavior, request-body hashing/replay
  detection): considered and explicitly deferred — the story's own
  instructions warned against building this prematurely. The
  tenant-scoped `UNIQUE(organization_id, idempotency_key)` constraint is
  the foundation a future story can build richer behavior on top of
  without a schema change.
- **A single `metadata: dict` field with no size bound or content
  policy**: rejected — an unbounded, unpoliced JSON column is exactly
  the shape that quietly accumulates prompts, tool payloads, or PII
  once a future story starts writing to it under time pressure. Bounding
  it at the database level, and writing down the prohibition list now,
  closes that failure mode structurally rather than relying on future
  code review to catch it every time.
- **Persisting raw exceptions/stack traces for debuggability**:
  considered (it would genuinely help operational debugging) and
  rejected — the risk of leaking connection strings, SQL with bound
  values, file paths, or other operational detail into a row a
  `PATIENT`-role caller might eventually see reflected back (directly or
  via a future support/inspection tool) outweighs the debugging
  convenience. Real stack traces belong in server-side application logs
  (already covered by `app.core.exceptions`'s `logger.exception` on
  unhandled errors), not in a persisted, potentially caller-visible
  audit row.
- **A database trigger enforcing `WorkflowEvent` immutability**:
  considered and declined for this story — see Rationale. Worth
  revisiting if a future threat model specifically includes actors with
  direct SQL access bypassing the application layer.
- **Exposing lifecycle transitions as HTTP endpoints "for convenience"**:
  considered and rejected — see Rationale; the story's own instructions
  explicitly warned against building a mutation surface future agents
  would bypass their own orchestration through.

## Consequences

- The first story that adds real agent/tool execution
  (LLM client, LangGraph, tool calling) builds on top of
  `WorkflowService`'s existing `create_step`/`start_step`/
  `complete_step`/`fail_step`/`skip_step` methods, calling them
  in-process from orchestration code — no new persistence layer, and no
  new HTTP endpoints, should be needed purely to support execution
  itself.
- That same future story must make its OWN explicit, documented decision
  about what (if any) request text or tool-call detail is safe to
  persist, honoring the `safe_metadata` prohibition list and the
  "no chain-of-thought persistence" rule this ADR establishes now — it
  is a standing constraint, not something scoped only to STORY-009.
- Step-level `waiting`/resume support (currently reachable in the
  `StepStatus` enum and database `CHECK` constraint, but not wired to
  any `WorkflowService` method or `WorkflowEventType`) can be added
  later by introducing `step_waiting`/`step_resumed` event types and two
  new service methods — no migration is needed for the status value
  itself, only for the new event type.
- A future idempotent-retry framework can be layered on top of the
  existing `UNIQUE(organization_id, idempotency_key)` constraint
  (e.g. "on conflict, return the existing row" instead of erroring)
  without altering the schema.
- If a future story needs a genuine security/compliance audit log
  (beyond this workflow's own lifecycle), that is a new, additive
  capability designed on its own terms — not an expansion of
  `WorkflowEvent`'s scope, which this ADR deliberately keeps bounded to
  "this workflow's own history" (see docs/WORKFLOWS.md Section 18).
