# ADR-0013: Human-in-the-Loop (HITL) Approvals

Status: Accepted

Date: 2026-07-28

## Context

Every agent decision through STORY-011 resolves autonomously: a handoff,
a clarification, a refusal, or a tool call — the Coordinator and each
specialist always reach a terminal outcome on their own, even when the
correct outcome is genuinely uncertain (low confidence, an ambiguous or
policy-restricted action, or missing information the model should not
guess at). STORY-014 is the first story where the SAFE answer to
uncertainty is neither "guess" nor "refuse outright," but "pause and ask
a human" — and the story is explicit that this must become part of the
Workflow Engine itself (reusing `WorkflowRun`/`WorkflowStep`'s existing
pause/resume vocabulary), not a new, parallel CRUD module with its own
disconnected audit trail.

## Decision

1. **`CoordinatorRequiresApprovalDecision` is a FOURTH `CoordinatorDecision`
   variant** (`app.ai.coordinator_decisions`), alongside handoff/
   clarification/refusal — structurally, exactly like every other
   Coordinator variant, it STILL cannot carry a tool call (no such
   variant exists in the union). It carries only a bounded `reason` and
   an `approval_type` (reusing `app.models.approval.ApprovalType`
   directly, not a second independently-defined mirror of it — an
   approval type must always mean the same thing whether read from the
   database or emitted by the model). Only the Coordinator gets this
   capability in this story — a specialist's own decision space
   (`AdministrativeDecision`) is untouched.
2. **An `ApprovalRequest` is never a parallel audit trail.** It always
   references an EXISTING `WorkflowRun`/`WorkflowStep` via composite FKs
   (mirroring `app.models.reminder.Reminder`'s identical discipline), and
   requesting one ALWAYS pauses both in the SAME call
   (`ApprovalService.create_approval_request`) — a `PENDING` approval and
   a `WAITING` step/run are always the same fact, never two independently
   maintained ones.
3. **"Workflow paused"/"workflow resumed" REUSE STORY-009's existing
   `WorkflowStatus.WAITING`/`WORKFLOW_WAITING`/`WORKFLOW_RESUMED`
   primitives** — built in STORY-009 but never previously driven by any
   service. Two NEW STEP-level counterparts were added
   (`WorkflowService.mark_step_waiting`/`resume_step`, with matching
   `STEP_WAITING`/`STEP_RESUMED` event types) so a single step — not the
   whole run — can pause, mirroring the existing `STEP_STARTED`/
   `WORKFLOW_STARTED` separate-value pattern. Three genuinely new event
   types complete the picture: `APPROVAL_REQUESTED`/`APPROVAL_GRANTED`/
   `APPROVAL_REJECTED`, recorded via a `WorkflowService.record_approval_event`
   method that directly mirrors `record_reminder_event`'s shape.
4. **`create_approval_request` pauses the step/run BEFORE creating the
   `ApprovalRequest` row** — the reverse of `ReminderService.schedule_reminder`'s
   "durable record first" ordering, and a deliberate concurrency-safety
   choice: `mark_step_waiting`'s row lock is this method's serialization
   point. If a concurrent caller already paused the same step, this call
   fails with `WorkflowConflictError` at that lock and NO `ApprovalRequest`
   row is ever created for the loser — an orphaned `PENDING` approval
   with no real paused step behind it is structurally impossible, not
   merely made unlikely by a race window. See "A Concurrency Bug Found
   and Fixed" below for why this ordering matters in practice, not just
   in theory.
5. **Terminal-state mapping**, all inside `ApprovalService`:
   - `approve`: resume (step+run `WAITING` -> `RUNNING`, `STEP_RESUMED`/
     `WORKFLOW_RESUMED`) then complete (step+run -> `COMPLETED`).
   - `reject`: resume, then fail the step
     (`failure_code="approval_rejected"`) and cancel the run.
   - expire (checked lazily at the top of `approve`/`reject`, or
     explicitly via `expire_approval`): fail the step and run DIRECTLY
     from `WAITING` (`failure_code="approval_expired"`) — no resume,
     since there is no decision to act on. `StepStatus` has no
     `CANCELLED` value, so a rejected/expired step ends `FAILED` while
     its run ends `CANCELLED`/`FAILED` respectively — an accepted,
     already-established asymmetry (see `app.models.workflow.StepStatus`).
6. **Lazy expiration, no background sweep worker.** `ApprovalRequest.expires_at`
   is checked inside `approve`/`reject` themselves: a still-`PENDING` but
   overdue approval is auto-transitioned to `EXPIRED` (failing its
   workflow) and the approve/reject attempt is rejected with
   `ApprovalExpiredError` — never silently honored. A genuinely stale
   approval that NO ONE ever tries to act on stays `PENDING` until
   someone does; this story ships no scheduled sweep, mirroring the
   reminder engine's own "no speculative infrastructure beyond what this
   story needs" discipline.
7. **`approved_by_user` records the resolving user for EITHER terminal
   outcome** (approve OR reject) — there is deliberately no separate
   `rejected_by_user` column. A single `approval_status_actor_consistency`
   CHECK constraint is the one source of truth for which columns must/
   must not be set for each `ApprovalStatus`.
8. **A new `Role.SUPERVISOR`**, authorized (alongside `ADMIN`) to
   approve/reject — `STAFF` may raise a request via `POST .../approvals`
   but not resolve one, and `PATIENT` cannot reach any approval route at
   all. RBAC is enforced entirely at the API layer
   (`require_roles(Role.ADMIN, Role.SUPERVISOR)`), never inside
   `ApprovalService` — the service has no knowledge of HTTP or roles.
9. **"Resume exactly where paused" is scoped to the WORKFLOW's own state
   transitions completing/cancelling — never an automatic tool-call
   replay.** `ApprovalRequest`'s field list (per this story's
   specification) carries no tool name/arguments payload, so there is
   nothing to replay; approving a request that gated, say, a future
   tool-calling specialist step is a scope decision left to whichever
   future story adds specialist-level approval gating.

## A Concurrency Bug Found and Fixed

Writing the mandatory concurrency test for `create_approval_request`
(two Coordinator-triggered approval requests racing for the same
running step) surfaced a real bug, not a test-authoring mistake: with
the "create the row, then pause" ordering originally implemented, BOTH
concurrent callers succeeded — no `WorkflowConflictError` at all, even
though `WorkflowService.mark_step_waiting` alone, called directly,
correctly serialized two racing calls under `SELECT ... FOR UPDATE`.

The cause: `create_approval_request`'s initial unlocked precondition
read (`workflow_step_repository.get_by_id`) populated the session's
SQLAlchemy identity map with a `WorkflowStep` object. A LATER locked
read of the SAME row (`get_by_id_for_update`, inside `mark_step_waiting`)
correctly executed `SELECT ... FOR UPDATE` and correctly blocked until
the winning transaction committed — but SQLAlchemy's identity map, by
default, does NOT overwrite an already-loaded object's attributes from a
new query result. The loser's `step.status` in Python kept reading the
STALE `RUNNING` value observed before it ever blocked, even though the
row was genuinely, correctly locked and the query genuinely waited —
so both callers' in-memory status check passed.

Two fixes, both applied:
- `get_by_id_for_update` in `app.repositories.workflow_run`,
  `workflow_step`, `reminder`, and `approval` now all pass
  `.execution_options(populate_existing=True)`, forcing a "for update"
  read to ALWAYS overwrite the Python object's attributes with the
  freshly locked row — the entire point of a locked read is to observe
  the current, contention-safe state, never a stale cached one.
- `create_approval_request` was restructured to pause FIRST (see
  Decision 4 above), so the orphaned-row failure mode is eliminated
  structurally, not merely masked by the identity-map fix.

Both fixes are logically independent and both are load-bearing: the
`populate_existing` fix protects every OTHER caller of these
`get_by_id_for_update` functions that might do an unlocked read first
(a general, latent risk this bug exposed, not unique to approvals); the
reordering protects THIS specific flow even if a future refactor
reintroduces an early unlocked read. `tests/db/test_approval_concurrency.py`
proves both properties directly against real PostgreSQL.

## Rationale

- **A fourth `CoordinatorDecision` variant, not a specialist-level one**:
  the Coordinator is the ONE place in this architecture that decides
  "what should handle this," so it is the natural, single place to also
  decide "no automated path should handle this — ask a human." Adding
  it to `AdministrativeDecision` instead would mean every specialist
  needs its own approval-request plumbing for no benefit this story
  actually needs.
- **Reusing `WorkflowStatus.WAITING` instead of inventing a `PAUSED`
  status**: STORY-009 already built and documented this exact state,
  anticipating future pause/resume use — using it is the direct
  fulfillment of that design, not a coincidence. Inventing a
  second, parallel "paused" concept would fragment the state machine
  `WorkflowService` already centralizes.
- **Pausing before creating the `ApprovalRequest` row**: see "A
  Concurrency Bug Found and Fixed" above — this is not a stylistic
  preference, it is what makes an orphaned, undecidable `PENDING`
  approval impossible by construction.
- **`approved_by_user` doubles as the rejecting user's column**: this
  story's specified `ApprovalRequest` field list has no
  `rejected_by_user` field, and adding one anyway would only complicate
  the state-consistency CHECK constraint for zero additional guarantee —
  a rejection is still one accountable human decision, exactly like an
  approval.
- **Lazy expiration over a background sweep**: this story's actual
  requirement is "a decision made after the deadline must not be
  silently honored," which lazy checking satisfies completely. A
  sweep worker would additionally make a NEVER-decided approval
  eventually fail on its own — a real but distinct capability this
  story does not require, matching the reminder engine's own
  no-speculative-infrastructure precedent.

## Alternatives Considered

- **A new `WorkflowStatus.PAUSED` distinct from `WAITING`**: rejected —
  see Rationale; `WAITING` already means exactly this.
- **A background expiry sweep worker**: rejected for this story's
  scope — see Rationale and the reminder engine's own equivalent
  decision in [ADR-0012](ADR-0012-reminder-engine.md).
- **A separate `rejected_by_user` column**: rejected — see Rationale.
- **Approval-request capability on specialists, not just the
  Coordinator**: rejected — this story's specification scopes the
  capability to the Coordinator only; a specialist wanting to pause
  mid-tool-call (e.g. "confirm this booking override") is a larger
  scope change (it would need a real payload/replay mechanism — see
  Decision 9) left to a future story.
- **Fixing only the concurrency bug's symptom (in `create_approval_request`)
  without the general `populate_existing=True` repository fix**:
  rejected — the identity-map staleness risk exists for ANY future
  caller that reads a row, then later locks it in the same session;
  fixing only the one call site that happened to trigger it would leave
  the same latent bug for the next one.

## Consequences

- A future story adding approval-gating to a SPECIALIST's tool call (not
  just the Coordinator) can reuse `ApprovalService` directly, but will
  need to extend `ApprovalRequest`'s payload (or a related table) to
  carry enough to genuinely replay the gated action — see Decision 9's
  scope boundary.
- A future notification story wiring `NotificationProvider` to alert a
  Supervisor that a request is waiting is a new call site inside
  `ApprovalService.create_approval_request`, not a redesign.
- If approval volume ever requires proactive (not lazily-checked)
  expiration, `ApprovalService.expire_approval` is already the exact
  primitive a scheduled sweep would call per stale row — this ADR
  should be superseded, not silently reinterpreted, if that sweep is
  added.
- Any future story needing `get_by_id_for_update` on a NEW model should
  follow the `populate_existing=True` convention established here by
  default, not just when a race is later observed.
