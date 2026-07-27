# AgentCare Tool Contract

This document describes the explicit, allowlisted tool contract
implemented in STORY-010: `app/ai/tools/` (`base.py`, `registry.py`,
`appointment_tools.py`). See [AI_SAFETY.md](AI_SAFETY.md) for the full
LLM trust boundary this contract is one layer of, and
[adr/ADR-0010-llm-and-tool-security-boundary.md](adr/ADR-0010-llm-and-tool-security-boundary.md)
for the decision record.

## 1. The Tool Contract

Three types, `app/ai/tools/base.py`:

- **`ToolDefinition`** — one explicitly-registered, allowlisted tool:
  stable `name`, human-readable `description`, a `ToolCategory`, a
  Pydantic `input_schema` (untrusted-argument validation), and a
  `handler` (the actual async function). Constructed exactly once, at
  module load time (`build_default_registry`), never per-request and
  never from anything a model supplies.
- **`ToolExecutionContext`** — SERVER-CREATED trusted identity/
  authorization data: `organization_id`, `user_id`, `role`,
  `patient_id` (server-derived self-scope, see Section 3),
  `workflow_run_id`, `workflow_step_id`. The LLM never constructs this
  and has no way to influence any field on it.
- **`ToolResult`** — the outcome: `status` (`success`/`failure`), a
  short `code`, a `safe_message`, and optional bounded `data`. See
  Section 5.

## 2. Registry

`app.ai.tools.registry.ToolRegistry` is a plain, explicit allowlist —
`register()`, `get()`, `list_allowed()`, `execute()`. `get()` is a
`dict` lookup by exact string name: no `getattr`, no `eval`/`exec`, no
dynamic import, no shell execution, no fuzzy/partial matching anywhere.
An unrecognized name (`"os.system"`, `"admin.make_me_admin"`,
`"run_sql"`, anything a model invents) returns
`ToolResult(status=FAILURE, code="unknown_tool")` — a controlled
rejection, never an exception that could leak internal detail, and
never a lookup that could ever resolve to real code.

`execute(tool_name, raw_arguments, context, session)`:

1. Look up the tool. Unknown -> `unknown_tool` failure.
2. Validate `raw_arguments` against the tool's own `input_schema`
   (`extra="forbid"` — an unexpected/extra argument fails validation,
   not "field ignored"). Invalid -> `invalid_tool_arguments` failure.
3. Call the handler. Always returns a `ToolResult` — an unexpected
   exception inside a handler is caught by a last-resort safety net and
   converted to `tool_execution_failed`, never propagated with its
   original message.

`register()` rejects a duplicate name outright (`ValueError`) —
registration is a startup-time, code-reviewed operation, never a
runtime one.

## 3. Trusted vs. Untrusted Data

**Never merge them blindly.** A tool handler receives two separate
parameters with two entirely different trust levels:

- `arguments` (validated against the tool's `input_schema`) — UNTRUSTED
  model-supplied data. Example: `practitioner_id`, `department_id`,
  `start_at`, and (for `ADMIN`/`STAFF` callers only) `patient_id`.
- `context` (`ToolExecutionContext`) — TRUSTED, server-derived data.
  `organization_id`, `user_id`, `role`, and — critically —
  `patient_id`.

**The authorization rule every tool handler follows**: for a
`PATIENT`-role caller, `context.patient_id` (the caller's OWN linked
patient id, resolved server-side by
`app.api.v1.endpoints.agent._resolve_request_patient_id` — the exact
same pattern `app.api.v1.endpoints.workflows`/`appointments` already
use) is used, and a model-supplied `arguments.patient_id` is READ BUT
NEVER TRUSTED. For `ADMIN`/`STAFF`, `arguments.patient_id` is used —
the same "who is this booking for" choice the human-driven booking API
already lets `ADMIN`/`STAFF` make. See `app.ai.tools.appointment_tools._book_appointment`.

This is proven directly, not just documented: a `PATIENT` caller's
request, even when the (fake, test-controlled) model decision supplies
ANOTHER patient's UUID as an argument, results in a booking under the
caller's OWN patient id —
`tests/ai/test_tools.py::test_book_appointment_patient_role_uses_own_patient_id_never_argument`
and
`tests/api/test_agent_endpoints.py::test_patient_cannot_book_for_another_patient_even_via_model_argument`.

## 4. Authorization

The model NEVER decides `organization_id` authority, authenticated user
identity, membership role, or whether a patient may act on another
patient. Those all come from the authenticated request's own
already-verified membership (`get_current_membership`/`require_roles` —
see [RBAC.md](RBAC.md)), resolved into `ToolExecutionContext` by the
route handler BEFORE orchestration ever begins. No tool handler
re-implements or weakens any existing service-layer authorization
assumption — every handler calls the SAME `app.services.*` functions
every other route in this codebase calls, inheriting their existing
tenant/RBAC enforcement unchanged.

## 5. `ToolResult` Contract

```python
@dataclass(frozen=True)
class ToolResult:
    status: ToolResultStatus   # SUCCESS | FAILURE
    code: str                  # e.g. "appointment_booked", "appointment_conflict"
    safe_message: str          # human-readable, safe to show a caller
    data: dict[str, Any] | None  # bounded, pre-sanitized structured detail
```

`data`, when present, holds ONLY deliberately-chosen, already-safe
keys — e.g. `{"appointment_id": "...", "start_at": "...", "end_at":
"..."}`. **Never**: a raw SQL error, a stack trace, a connection
string, a storage key, a password hash, a JWT, any other secret, or an
ORM object's `repr()`. Every tool handler in this codebase is
responsible for this; `ToolRegistry.execute`'s outer exception handler
is the last-resort backstop if a handler ever fails to uphold it.

## 6. Initial Tools

Two tools, both calling the REAL existing service layer — never a
fake/hardcoded success path (Section 7):

| Tool | Category | Calls | Arguments |
|---|---|---|---|
| `check_availability` | `appointment_availability` | `AvailabilityQueryService.list_available_times` | `practitioner_id`, `department_id`, `on_date`, `duration_minutes` |
| `book_appointment` | `appointment_booking` | `AppointmentService.book_appointment` | `practitioner_id`, `department_id`, `start_at`, `duration_minutes`, `patient_id` (ADMIN/STAFF only — see Section 3) |

`check_availability` returns `{"available_times": [...]}` (bounded to
the first 10 slots) or `{"available_times": []}` — never an error for
"no times found," only for a genuine cross-tenant/inactive-resource
rejection reflected through the underlying service's own privacy rules.

`book_appointment` maps every `AppointmentService` exception to a safe
`ToolResult` code: `appointment_conflict`, `resource_not_found`
(covers a missing OR cross-tenant patient/practitioner/department —
deliberately non-disclosing, mirroring
`app.api.v1.endpoints.appointments`'s existing rule),
`resource_inactive`, `practitioner_not_assigned`, `invalid_duration`,
`appointment_in_past`, `outside_availability`.

### Deliberate Scope Decision: Depth Over Breadth

**Reschedule and cancel tools are NOT implemented in this story.** The
story's own instructions favored depth (genuine, fully-wired, tested
tools) over breadth (many shallow ones). `check_availability` and
`book_appointment` prove the full chain end-to-end
(model decision -> registry -> tool -> real service -> real database ->
workflow persistence); adding `reschedule_appointment`/
`cancel_appointment` later means adding two more `ToolDefinition`s to
`build_default_registry`, following the EXACT same pattern as
`book_appointment` — no change to the contract itself, no new
architectural work.

## 7. No Fake Success

**A tool result may say "booked" only because
`AppointmentService.book_appointment` genuinely persisted a row. A
result may say availability exists only because
`AvailabilityQueryService.list_available_times` genuinely computed it.**
No tool handler in this codebase contains a hardcoded success path.
Proven directly: `tests/ai/test_tools.py` calls each tool against real
PostgreSQL and re-queries the database afterward via the repository
layer to confirm the row genuinely exists (never trusting the
`ToolResult` alone); the mandatory end-to-end test
(`tests/api/test_agent_endpoints.py::test_full_chain_patient_request_to_persisted_appointment_and_workflow`)
does the same at the full HTTP-request level.

## 8. Workflow Integration

`app.ai.orchestration.AgentOrchestrationService` calls
`WorkflowService.record_tool_invocation` immediately before dispatching
a tool (appending a `tool_invoked` event, linked to the current step)
and `complete_step`/`fail_step` immediately after, based on the tool's
own `ToolResultStatus` — see [AI_SAFETY.md](AI_SAFETY.md) Section 10
for the full event sequence and persistence policy.

## 9. How Future Tools Must Be Added Safely

1. Define a Pydantic `input_schema` with `model_config =
   ConfigDict(extra="forbid")` — every field the model may supply,
   nothing else.
2. Write an async handler: `(arguments, context, session) -> ToolResult`.
   Call an EXISTING `app.services.*` function — never write new
   persistence logic directly in a tool handler. Map every known
   service exception to a safe `ToolResult` code; never let a raw
   exception's message reach `safe_message`.
3. For any argument representing "which patient this acts on," follow
   Section 3's rule exactly: a `PATIENT`-role caller's own
   `context.patient_id` always wins over anything model-supplied.
4. Choose a `ToolCategory` — if the new tool's capability doesn't fit
   an existing administrative category, that itself is a signal to stop
   and reconsider whether it belongs in this codebase at all (see
   [AI_SAFETY.md](AI_SAFETY.md) Section 7's healthcare safety
   boundary — a tool must never expose a clinical-decision capability).
5. Register it in `build_default_registry` (or the equivalent registry
   builder for a future tool category). No other wiring is needed —
   `AgentOrchestrationService` and the API route are already generic
   over whatever the registry contains.
6. Write tests mirroring `tests/ai/test_tools.py`: valid execution
   against real PostgreSQL (re-querying the database to confirm genuine
   persistence, never trusting the `ToolResult` alone), cross-tenant
   rejection, patient self-scope (if applicable), invalid/malformed
   arguments, and an unexpected-exception safety-net case.

## 10. Current vs. Planned

**Current**: the full contract (`ToolDefinition`/
`ToolExecutionContext`/`ToolResult`/`ToolRegistry`); two real tools
(`check_availability`, `book_appointment`); full workflow integration;
comprehensive tests including cross-tenant and patient-self-scope
adversarial cases.

**Explicitly not implemented** (later stories): `reschedule_appointment`/
`cancel_appointment` tools (Section 6); document-related tools (checking
document administrative status, requesting document collection); a
department-name-to-`department_id` resolution tool (this story's tools
take resolved UUIDs as arguments, not human-readable names); role-aware
tool visibility (`ToolRegistry.list_allowed()` currently returns the
same set to every caller — authorization is enforced inside each
handler, not by hiding tools from the model); any tool exposing a
clinical-decision capability, which must never exist.
