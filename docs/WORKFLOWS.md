# AgentCare Persistent Workflow Engine & Audit Trail

This document describes the durable workflow-state foundation
implemented in STORY-009: `WorkflowRun`, `WorkflowStep`, `WorkflowEvent`
(`app/models/workflow.py`), their repositories
(`app/repositories/workflow_run.py`, `workflow_step.py`,
`workflow_event.py`), `WorkflowService`
(`app/services/workflow.py`), and the administrative workflow API
(`app/api/v1/endpoints/workflows.py`). It follows the same CURRENT vs.
PLANNED discipline as [ARCHITECTURE.md](ARCHITECTURE.md): everything
described here as implemented exists in the repository today; anything
marked PLANNED does not yet. See
[adr/ADR-0009-durable-workflow-state.md](adr/ADR-0009-durable-workflow-state.md)
for the decision record.

## 1. Why This Story Exists

Future AgentCare agents (LLM-driven or otherwise) will need to plan and
execute multi-step administrative tasks — book an appointment, collect a
document, route a request — across multiple calls, potentially with
retries, waits, and failures in between. That execution state must not
live in a Python process's memory, a browser session, or an in-memory
dictionary: a process restart, a load-balanced second instance, or a
deploy must never destroy in-flight workflow history. `WorkflowRun`,
`WorkflowStep`, and `WorkflowEvent` are the durable, PostgreSQL-backed
foundation this requires — persistence and lifecycle mechanics only.

**This story does NOT implement**: an LLM client, prompts, an agent
framework, LangGraph, tool calling, autonomous decision-making, medical
reasoning, background workers/schedulers, reminders, or a frontend. See
Section 15 ("Healthcare Safety Boundary") and Section 16 ("Current vs.
Planned").

## 2. Three Concepts, Not a BPM Platform

Deliberately three tables, not a general-purpose workflow/BPM engine:

- **`WorkflowRun`** — one administrative request from creation to a
  terminal outcome.
- **`WorkflowStep`** — one unit of work within a run (an agent/tool
  invocation, in future stories).
- **`WorkflowEvent`** — an append-only audit/history record of what
  happened, when, and by whom (or what).

No generic "workflow definition" DSL, no branching/parallel-execution
graph model, no speculative scheduler/queue. Future stories may extend
this if a concrete need justifies it; this story does not anticipate one.

## 3. The `WorkflowRun` Model

`app/models/workflow.py` — table `workflow_runs`.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key, application-generated |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id` (`ON DELETE RESTRICT`), indexed |
| `patient_id` | UUID | NULLABLE, indexed — see Section 6 for ownership FK |
| `initiated_by_user_id` | UUID | NOT NULL, indexed — see Section 6 |
| `request_type` | VARCHAR(32) | NOT NULL, CHECK constrained to `WorkflowRequestType` |
| `status` | VARCHAR(16) | NOT NULL, CHECK constrained to `WorkflowStatus`, indexed, default `pending` |
| `current_step` | INTEGER | NULLABLE — see Section 10 |
| `correlation_id` | VARCHAR(32) | NOT NULL, UNIQUE — see Section 9 |
| `idempotency_key` | VARCHAR(255) | NULLABLE, UNIQUE per organization — see Section 11 |
| `failure_code` | VARCHAR(64) | NULLABLE — see Section 13 |
| `failure_message_safe` | VARCHAR(500) | NULLABLE — see Section 13 |
| `started_at` / `completed_at` | TIMESTAMPTZ | NULLABLE |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL |

`WorkflowRequestType` (`enum.StrEnum`): `appointment_booking`,
`appointment_rescheduling`, `appointment_cancellation`,
`document_collection`, `administrative_routing`, `follow_up` — a
deliberately small, administrative allowlist matching the current/
planned roadmap. No diagnosis/treatment category exists or may be added
without a fresh, explicit design decision (Section 15).

## 4. The `WorkflowStep` Model

Table `workflow_steps`.

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id`, indexed |
| `workflow_run_id` | UUID | NOT NULL, indexed — composite FK, see Section 6 |
| `sequence_number` | INTEGER | NOT NULL, UNIQUE per run |
| `step_type` | VARCHAR(64) | NOT NULL — a free-text label (e.g. `"lookup_availability"`) |
| `status` | VARCHAR(16) | NOT NULL, CHECK constrained to `StepStatus`, indexed, default `pending` |
| `agent_name` | VARCHAR(100) | NULLABLE — reserved for future agent actors, see Section 8 |
| `tool_name` | VARCHAR(100) | NULLABLE — reserved for future tool actors, see Section 8 |
| `attempt_count` | INTEGER | NOT NULL, CHECK `>= 0`, default 0 |
| `failure_code` | VARCHAR(64) | NULLABLE — see Section 13 |
| `failure_message_safe` | VARCHAR(500) | NULLABLE — see Section 13 |
| `started_at` / `completed_at` | TIMESTAMPTZ | NULLABLE |
| `created_at` / `updated_at` | TIMESTAMPTZ | NOT NULL |

No unrestricted structured input/output JSON column exists on
`WorkflowStep` in this story — see Section 12 for exactly what
`safe_metadata` on `WorkflowEvent` may and may not hold, and why a
step-level equivalent was deliberately NOT added yet.

## 5. The `WorkflowEvent` Model (Append-Only Audit Trail)

Table `workflow_events`. Deliberately does **not** use the codebase's
standard `TimestampMixin` — only `created_at` exists, no `updated_at` —
because a `WorkflowEvent` is architecturally guaranteed to never be
updated after creation (Section 7).

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | primary key |
| `organization_id` | UUID | NOT NULL, FK → `organizations.id`, indexed |
| `workflow_run_id` | UUID | NOT NULL, indexed — composite FK, see Section 6 |
| `workflow_step_id` | UUID | NULLABLE, indexed — composite FK, see Section 6 |
| `event_type` | VARCHAR(32) | NOT NULL, CHECK constrained to `WorkflowEventType`, indexed |
| `actor_type` | VARCHAR(16) | NOT NULL, CHECK constrained to `ActorType` — see Section 8 |
| `actor_identifier` | VARCHAR(100) | NOT NULL, CHECK length ≤ 100 — see Section 8 |
| `safe_metadata` | JSONB | NULLABLE, CHECK `octet_length(safe_metadata::text) <= 2000` — see Section 12 |
| `created_at` | TIMESTAMPTZ | NOT NULL |

`WorkflowEventType` (`enum.StrEnum`): `workflow_created`,
`workflow_started`, `step_started`, `step_completed`, `step_failed`,
`step_skipped`, `workflow_waiting`, `workflow_resumed`,
`workflow_completed`, `workflow_failed`, `workflow_cancelled`.
(`step_skipped` is one addition beyond the story's suggested list, added
for symmetry with `StepStatus.SKIPPED` — see Section 7.)

## 6. Tenant & Ownership Integrity (Database-Enforced)

The same composite-FK technique used throughout this codebase since
STORY-006 (see [DATABASE.md](DATABASE.md)):

- **`WorkflowRun` → `Organization`**: plain FK, `organization_id ->
  organizations.id`.
- **`WorkflowRun` → `Patient`** (when `patient_id` is set): composite FK
  `(organization_id, patient_id) -> patients(organization_id, id)`.
  `patient_id` is nullable — under PostgreSQL's default `MATCH SIMPLE`
  FK semantics, the whole constraint is trivially satisfied when
  `patient_id IS NULL` (not every workflow is patient-tied), so no
  separate `CHECK` is needed to handle that case.
- **`WorkflowRun` → initiator membership**: composite FK
  `(organization_id, initiated_by_user_id) ->
  organization_memberships(organization_id, user_id)` — proves the
  initiator was (at some point) a genuine member of the organization.
  This is a database-level guarantee; it does **not**, and cannot,
  guarantee the membership is currently ACTIVE — `WorkflowService`
  re-verifies active membership at creation time, the same
  existence-vs-activity split established in
  [DOCUMENTS.md](DOCUMENTS.md) Section 5 and
  [adr/ADR-0005-patient-identity-and-access.md](adr/ADR-0005-patient-identity-and-access.md).
- **`WorkflowStep` → `WorkflowRun`**: composite FK `(organization_id,
  workflow_run_id) -> workflow_runs(organization_id, id)`. `WorkflowStep`
  also carries its own `UNIQUE(organization_id, workflow_run_id, id)` —
  purely so `WorkflowEvent` can hold a composite FK against it (below).
- **`WorkflowEvent` → `WorkflowRun`**: composite FK `(organization_id,
  workflow_run_id) -> workflow_runs(organization_id, id)`.
- **`WorkflowEvent` → `WorkflowStep`** (when `workflow_step_id` is set):
  a **3-column** composite FK `(organization_id, workflow_run_id,
  workflow_step_id) -> workflow_steps(organization_id, workflow_run_id,
  id)` — this is what guarantees an event's linked step belongs to the
  SAME run (not merely the same organization) as the event itself.
  `workflow_step_id` is nullable (many events are run-level, not
  step-level), and `MATCH SIMPLE` again means the constraint is
  trivially satisfied when it's `NULL`.

**Raw SQL cannot attach Org A's steps/events to Org B's workflow** —
verified directly against real PostgreSQL (Section 14) and in
`tests/models/test_workflow.py`.

## 7. Lifecycle State Machines (Centralized)

Both state machines live in `app/services/workflow.py` as explicit
`dict[Status, frozenset[Status]]` tables — **not** scattered `if`
statements across routes — mirroring the transition-table discipline
`AppointmentService` established in STORY-007.

### `WorkflowStatus`

`pending`, `running`, `waiting`, `completed`, `failed`, `cancelled`.

```
PENDING   -> {RUNNING, CANCELLED}
RUNNING   -> {WAITING, COMPLETED, FAILED, CANCELLED}
WAITING   -> {RUNNING, FAILED, CANCELLED}
COMPLETED -> {}   (terminal)
FAILED    -> {}   (terminal)
CANCELLED -> {}   (terminal)
```

### `StepStatus`

`pending`, `running`, `waiting`, `completed`, `failed`, `skipped`.

```
PENDING   -> {RUNNING, SKIPPED}
RUNNING   -> {WAITING, COMPLETED, FAILED}
WAITING   -> {RUNNING, FAILED}
COMPLETED -> {}   (terminal)
FAILED    -> {}   (terminal)
SKIPPED   -> {}   (terminal)
```

**Known, deliberate limitation**: `StepStatus.WAITING` is reachable in
the enum and the database `CHECK` constraint (reserved for future
tool-call-retry support), but **no `WorkflowService` method currently
moves a step into or out of `WAITING`** — there is no `step_waiting`/
`step_resumed` event type in `WorkflowEventType` to pair with it yet. A
future story may add step-level waiting once a concrete agent/tool
retry design needs it; this story does not speculatively build it.

Any transition not listed in these tables is rejected as
`WorkflowConflictError` (409) — see Section 9.

## 8. Actor Model

`ActorType` (`enum.StrEnum`): `user`, `system`, `agent`, `tool` —
preparing for future non-human actors without changing the schema.

`actor_identifier` (`WorkflowEvent`, ≤ 100 chars) is a **safe logical
identifier only** — never a credential, token, email address, patient
name, or prompt/response content:

- `ActorType.USER`: this story's API always passes the authenticated
  user's own UUID (`str(current_user.id)`) — an internal identifier,
  not PII, consistent with `initiated_by_user_id`/`uploaded_by_user_id`
  already being stored as plain UUIDs elsewhere in this schema.
- `ActorType.SYSTEM`: a short, fixed label (e.g.
  `"synthetic-concurrency-worker"` in tests).
- `ActorType.AGENT` / `ActorType.TOOL` (future use): short logical
  names, e.g. `"appointment_agent"`, `"appointment.book"` — never a
  serialized prompt or tool payload.

`WorkflowStep.agent_name`/`tool_name` (≤ 100 chars each) exist for the
same future purpose and are subject to the same rule.

## 9. Concurrency Strategy

**Requirement**: two workers must not both incorrectly advance the same
workflow's state (e.g. two callers both attempting to `start` the same
`pending` run).

**Mechanism**: every lifecycle transition (`WorkflowService._transition_run`
and `_transition_step`) locks its row with `SELECT ... FOR UPDATE`
(`app.repositories.workflow_run.get_by_id_for_update`,
`app.repositories.workflow_step.get_by_id_for_update`) before checking
the transition against Section 7's table. A second concurrent
transaction locking the SAME row **blocks** until the first commits or
rolls back, then re-reads the now-current (possibly already-transitioned)
status — so a losing caller deterministically observes its own
transition rejected, rather than silently double-applying it or
overwriting the winner.

**Alternative considered**: an optimistic version column
(`compare-and-swap` on a version integer). Rejected in favor of
row-level locking — see
[adr/ADR-0009-durable-workflow-state.md](adr/ADR-0009-durable-workflow-state.md)
for the full comparison. This mirrors the concurrency-safety-by-design
philosophy STORY-007 established with PostgreSQL `EXCLUDE` constraints
for appointment overlap, adapted here to a single-row state-transition
problem rather than an overlap-detection problem.

**A losing transition raises `WorkflowConflictError` (409)** — one
exception class covers both a genuine invalid transition and a lost
race, deliberately: once the row lock is held, the current status is
authoritative, and whatever it turns out to be, this transition either
is or isn't allowed from it — the caller does not need (and cannot
usefully distinguish) "you made a logic error" from "you lost a race."

**Proven, not merely designed**: `tests/db/test_workflow_concurrency.py`
opens two genuinely independent database connections/transactions (its
own dedicated engine, not the shared savepoint-isolated test fixture)
and races two real, concurrently-executing `start_workflow()` calls
against the SAME `pending` run via `asyncio.gather`. Exactly one
succeeds; the other receives `WorkflowConflictError`. Verified against
real PostgreSQL.

## 10. `current_step`

An **informational pointer**, not a field the state machine gates
correctness on. `WorkflowService.create_step` advances it to the newly
created step's `sequence_number` via
`app.repositories.workflow_run.set_current_step` — a single atomic
`UPDATE ... WHERE organization_id = ... AND id = ...` statement, not a
Python-level read-modify-write. A single-statement `UPDATE` is
race-safe by itself for this purpose; no additional row lock is taken
for it (unlike Section 9's status transitions, which genuinely need
one).

## 11. Correlation ID & Idempotency

**Correlation ID**: `correlation_id` (`VARCHAR(32)`, `UNIQUE`) is always
server-generated — `uuid.uuid4().hex` (`WorkflowService._new_correlation_id`),
exactly 32 hex characters. **A client can never choose, see-before-creation,
or influence a correlation id** — no request schema in this story accepts
one as input (`WorkflowRunCreate` has no such field). Never derived from
a patient's name, email, or date of birth. Intended to let a future
caller trace "API request → workflow → steps → events" without ever
exposing patient information through the identifier itself.

**Idempotency**: `idempotency_key` (`VARCHAR(255)`, nullable,
optional) is client-supplied and unique **per organization**
(`UNIQUE(organization_id, idempotency_key)`) — the same key is allowed
across different organizations (proven in
`tests/models/test_workflow.py::test_run_idempotency_key_allowed_across_organizations`),
rejected within the same one
(`WorkflowIdempotencyKeyConflictError`, 409). This story does **not**
implement a full idempotency framework (no automatic "return the
existing row instead of erroring" behavior, no request-body hashing/
replay-detection) — only the foundation: a bounded, tenant-scoped,
deliberately-unique column, with no PII requirement, and never treated
as an authentication credential. A future story can build a fuller
idempotent-retry behavior on top of this column without a schema change.

## 12. Safe Metadata & the Audit/Prompt Boundary

`WorkflowEvent.safe_metadata` (`JSONB`, nullable) is bounded to 2000
bytes at the database level:
`CHECK (safe_metadata IS NULL OR octet_length(safe_metadata::text) <= 2000)`
— verified to correctly treat `NULL` as passing (PostgreSQL `CHECK`
semantics), not merely assumed (proven via raw SQL in
`tests/models/test_workflow.py`).

**What `safe_metadata` is for**: small, structured, already-sanitized
facts about an event — e.g. `{"appointment_id": "..."}`. **Nothing in
this story's API accepts client-supplied `safe_metadata`** —
`WorkflowRunCreate` has no such field, so there is currently no HTTP
input surface that writes arbitrary JSON into it at all; only
`WorkflowService` call sites (today, only test code) can pass it, and
each future caller must deliberately construct a small, bounded,
pre-sanitized payload.

**What `safe_metadata` must NEVER hold** (documented now, before any
future LLM story adds real writers):

- A raw LLM prompt or response, in full or in part.
- **Chain-of-thought or any hidden model reasoning.** Future agent
  integrations must persist and expose only actions, statuses, and
  results — never a model's internal reasoning trace. This is a
  standing architectural rule, not merely a STORY-009 scope limit.
- Full patient request text (see Section 15's "no raw conversation
  storage" rule).
- Credentials, JWTs, API keys, or any other secret.
- An arbitrary/unbounded tool call payload.
- A raw exception, stack trace, or SQL statement (see Section 13).

## 13. Safe Failure Metadata

`failure_code` (≤ 64 chars) and `failure_message_safe` (≤ 500 chars) on
both `WorkflowRun` and `WorkflowStep`, each bounded by a database
`CHECK (... IS NULL OR length(...) <= N)`.

**`fail_workflow`/`fail_step` accept ONLY these two pre-sanitized
strings** — the method signatures do not accept an exception object,
and nothing in `WorkflowService` calls `str(exc)`/`repr(exc)` into
either field (verified by direct code inspection as part of this
story's security review; see the final report). A caller (a future
orchestrator) is responsible for translating whatever it caught into a
short, safe code (e.g. `"downstream_timeout"`) and a short, safe,
human-readable message — **never**: a raw stack trace, a database
connection URL, a SQL statement containing data, an API key, a full LLM
prompt, a full patient request, or an exception's `repr()`.

## 14. Real PostgreSQL Schema (Verified)

```
workflow_runs
  Check constraints:
      ck_workflow_runs_workflow_request_type
      ck_workflow_runs_workflow_status
      ck_workflow_runs_failure_code_length
      ck_workflow_runs_failure_message_length
  Foreign-key constraints:
      fk_workflow_runs_organization_id_organizations
      fk_workflow_runs_org_patient_patients          (composite: org+patient ownership)
      fk_workflow_runs_org_initiator_memberships      (composite: initiator membership)
  Unique constraints:
      uq_workflow_runs_organization_id_id
      uq_workflow_runs_correlation_id
      uq_workflow_runs_org_idempotency_key            (tenant-scoped)

workflow_steps
  Check constraints:
      ck_workflow_steps_workflow_step_status
      ck_workflow_steps_attempt_count_non_negative
      ck_workflow_steps_failure_code_length
      ck_workflow_steps_failure_message_length
  Foreign-key constraints:
      fk_workflow_steps_organization_id_organizations
      fk_workflow_steps_org_run_workflow_runs         (composite: run ownership)
  Unique constraints:
      uq_workflow_steps_org_run_id                    (target for workflow_events' 3-col FK)
      uq_workflow_steps_run_sequence

workflow_events
  Check constraints:
      ck_workflow_events_workflow_event_type
      ck_workflow_events_workflow_actor_type
      ck_workflow_events_actor_identifier_length
      ck_workflow_events_safe_metadata_size
  Foreign-key constraints:
      fk_workflow_events_organization_id_organizations
      fk_workflow_events_org_run_workflow_runs        (composite: run ownership)
      fk_workflow_events_org_run_step_workflow_steps  (composite, 3-column: run+step ownership)
```

Validated end-to-end against a real PostgreSQL instance: schema
inspection (`\d` on all three tables), a full raw-SQL constraint smoke
test (cross-tenant patient/initiator/step/event rejection, the 3-column
event↔step FK, `safe_metadata` size bound, correlation/idempotency
uniqueness — synthetic rows, all rolled back), an `alembic downgrade
-1` / `upgrade head` round-trip (confirming STORY-008's schema survives
untouched), and the mandatory concurrency and persistence proofs
(Sections 9 and 17).

## 15. Repository & Service Layers

`app/repositories/workflow_run.py`, `workflow_step.py`,
`workflow_event.py` — every read requires an explicit
`organization_id`; no unrestricted UUID-only lookup exists anywhere.
Never commit. Perform no RBAC and no lifecycle validation.
`workflow_event.py` exposes **only** `create` and `list_by_run` — no
`update`, no `delete` (verified structurally in
`tests/repositories/test_workflow.py`).

`WorkflowService` (`app/services/workflow.py`) owns:

- **Creation**: `create_workflow` — validates active initiator
  membership, validates patient ownership/activity if `patient_id` is
  given, generates the correlation id server-side, creates the run
  (`pending`) and its `workflow_created` event **atomically** (Section
  16).
- **Run transitions**: `start_workflow`, `mark_waiting`,
  `resume_workflow`, `complete_workflow`, `fail_workflow`,
  `cancel_workflow` — each a thin wrapper over the shared
  `_transition_run` (Sections 7 and 9).
- **Step lifecycle**: `create_step`, `start_step`, `complete_step`,
  `fail_step`, `skip_step` — `create_step` also advances
  `current_step` (Section 10); the rest wrap the shared
  `_transition_step`.
- **Read**: `get_workflow`, `list_workflows`, `list_steps`,
  `list_events` — all tenant-scoped, `get_workflow`/`list_workflows`
  optionally patient-scoped for self-service callers.

## 16. Transition + Event Atomicity

Every transition method updates the row's status **and** appends the
corresponding `WorkflowEvent` within the same database transaction,
`flush()`-ed and `commit()`-ed together in `_transition_run`/
`_transition_step`/`create_workflow`. A transition is never left
partially recorded — "workflow running + `workflow_started` event"
either both persist or neither does. Proven directly:
`tests/db/test_workflow_concurrency.py` confirms the LOSING side of a
race left no partial event trace (exactly one `workflow_started` event
exists after the race, not zero and not two), and
`tests/db/test_workflow_persistence.py` confirms a full run+step+event
history survives a genuine engine disposal/recreation.

## 17. Persistence & Restart Proof

**Requirement**: workflow state must survive a process restart — it
must not depend on any particular database connection, session, or
engine's lifetime.

**Proof**: `tests/db/test_workflow_persistence.py` creates a full
workflow (run, step, five events, through to `completed`) using one
engine/session, **fully disposes that engine** (`await
engine.dispose()` — its connection pool is torn down, nothing is cached
in Python memory that could paper over a real reconnect), then builds a
**brand new, independent engine/session from nothing but the connection
URL** and confirms it retrieves the identical run status, correlation
id, timestamps, step, and full ordered event history. Verified against
real PostgreSQL.

## 18. Audit Service Boundary

**`WorkflowEvent` is this workflow's own lifecycle audit trail — it is
NOT a general-purpose security/compliance audit log**, and persisting
these events does not, by itself, constitute HIPAA compliance or
satisfy any broader regulatory audit requirement. It records "what
happened to this workflow, when, and by which actor" for operational
visibility and future-agent debuggability. A future story that needs a
genuine compliance/security audit trail (covering, e.g., authentication
events, permission changes, or PHI access outside the workflow
lifecycle) must design that separately and explicitly — it is not
something this table already provides.

## 19. API Surface

Base path: `/api/v1/organizations/{organization_id}/workflows`.

| Method & Path | Purpose |
|---|---|
| `POST /workflows` | Create a new (`pending`) workflow run |
| `GET /workflows` | List workflow runs |
| `GET /workflows/{workflow_id}` | Retrieve one workflow run |
| `GET /workflows/{workflow_id}/steps` | List a run's steps, in order |
| `GET /workflows/{workflow_id}/events` | List a run's audit events, oldest first |
| `POST /workflows/{workflow_id}/cancel` | Cancel a run (ADMIN/STAFF only) |

**Deliberately does NOT expose**: `start`/`mark_waiting`/`resume`/
`complete`/`fail`, or any step-level transition, as an HTTP endpoint.
Those are internal lifecycle operations a future agent/orchestration
process calls directly through `WorkflowService` in-process — never
something an API client triggers. Exposing them would let a future
agent (or any API caller) bypass whatever orchestration logic is
supposed to own those transitions. This is the same "administrative
inspection/testing surface only, not an arbitrary internal-mutation
surface" principle the story's specification required.

## 20. RBAC (Authorization Matrix)

| Action | ADMIN | STAFF | PATIENT |
|---|---|---|---|
| Create workflow | allowed (any patient, or none) | allowed (any patient, or none) | allowed (self only) |
| List workflows | allowed (organization-wide) | allowed (organization-wide) | allowed (self only) |
| Get workflow | allowed (any) | allowed (any) | allowed (self only) |
| List steps/events | allowed (any) | allowed (any) | allowed (self only) |
| Cancel workflow | allowed | allowed | **never** |

**Patient cancellation policy (deliberate, conservative choice)**: a
`PATIENT` caller cannot cancel a workflow in this story —
`POST .../cancel` requires `ADMIN`/`STAFF` (`require_roles`), rejecting
`PATIENT` with 403 before any workflow is even looked up. The same
conservative rationale [DOCUMENTS.md](DOCUMENTS.md) Section 15 applies
to patient-initiated document deletion.

## 21. Patient Self-Access: Never Trusted From The Client

`app.api.v1.endpoints.workflows._resolve_creation_patient_id` and
`_restriction_patient_id` are the single places every route resolves
patient identity through, mirroring
[DOCUMENTS.md](DOCUMENTS.md) Section 16 /
[APPOINTMENTS.md](APPOINTMENTS.md)'s established pattern:

- **Creation**: for a `PATIENT`-role caller, `WorkflowRunCreate.patient_id`
  (if supplied) is **ignored, not merely validated** — the route always
  substitutes the caller's own linked `Patient` record
  (`PatientService.get_own_patient_record`, keyed off the authenticated
  `User.id`). Proven in
  `tests/api/test_workflow_endpoints.py::test_patient_cannot_create_workflow_for_another_patient`.
- **Read** (get/list/steps/events): a `PATIENT` caller's results are
  always additionally restricted to `patient_id == <their own patient
  id>`; any other workflow — including one belonging to a different
  patient in the SAME organization — 404s, identically to a truly
  nonexistent one.
- **Derived server-side, always**: `patient_id` is never trusted from a
  request body or URL for a `PATIENT`-role caller.

## 22. Privacy

Workflow API responses (`WorkflowRunResponse`, `WorkflowStepResponse`,
`WorkflowEventResponse` — `app/schemas/workflow.py`) never expose:
storage keys or any object-storage path (none exist on these models),
database identifiers unrelated to the response, credentials, JWTs, raw
exception detail (only `failure_code`/`failure_message_safe`, Section
13), prompts, hidden chain-of-thought (Section 12), or another
patient's/organization's workflow details (Sections 20–21). Verified
directly in
`tests/api/test_workflow_endpoints.py::test_event_response_never_leaks_internal_fields`,
which asserts the EXACT field set of a returned event, and by the
cross-tenant/cross-patient 404 tests throughout that file.

## 23. Healthcare Safety Boundary

Workflow categories (`WorkflowRequestType`, Section 3) are
**administrative only**: booking/rescheduling/cancelling an appointment,
collecting a requested document, routing an administrative request, and
coordinating a follow-up. **Never in scope, in this story or as a
category this schema anticipates**: diagnosing a condition, selecting a
treatment, prescribing or adjusting medication, or independently
deciding medical urgency. Administrative department routing (a future
story's use of `administrative_routing`) is permitted only when driven
by explicit workflow rules or user-provided context — never by
autonomous diagnosis of what a patient's request "really means"
medically.

**No raw patient conversation/request-text storage in this story.** A
`WorkflowRun` records structured facts (request type, status,
timestamps, safe failure metadata) — it does not, and in this story
cannot, store unrestricted natural-language patient request content.
Workflow state functions completely without it. A future LLM-integration
story must deliberately decide, with its own explicit design review,
what request text (if any) is necessary, its retention period,
redaction requirements, and PHI implications — this story neither
builds that capability nor assumes it will look like anything specific.

## 24. Current vs. Planned

**Current (this story):** `WorkflowRun`, `WorkflowStep`, `WorkflowEvent`
and their enums; database-enforced tenant/patient/initiator-membership/
step/event ownership integrity (including the 3-column event↔step
composite FK); centralized run and step state machines; `SELECT ... FOR
UPDATE` row-locking concurrency, proven under real concurrent
transactions; transition+event atomicity; server-generated correlation
ids; a tenant-scoped idempotency-key uniqueness foundation; bounded,
structurally-limited `safe_metadata` and failure-metadata fields; the
full create/read/cancel administrative API and RBAC matrix above;
patient self-access with the same structurally-enforced identity
boundary as appointments/documents; persistence proven across a genuine
engine disposal/recreation.

**Explicitly not implemented in this story** (later stories): an LLM
client, prompts, an agent framework, LangGraph, tool calling, autonomous
routing/decision-making, medical reasoning, background workers/
schedulers, reminders, step-level `waiting`/resume support, a fuller
idempotent-retry framework, a general-purpose security/compliance audit
log (Section 18), a frontend.
