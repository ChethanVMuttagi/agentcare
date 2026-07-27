# AgentCare AI Safety & Trust Boundary

This document describes the LLM and tool-calling foundation implemented
in STORY-010: `app/ai/` (providers, structured decisions, safety
policy, tool contract/registry, orchestration) and the
`POST .../agent/execute` endpoint. It follows the same CURRENT vs.
PLANNED discipline as [ARCHITECTURE.md](ARCHITECTURE.md): everything
described here as implemented exists in the repository today; anything
marked PLANNED does not yet. See
[TOOLS.md](TOOLS.md) for the tool contract itself and
[adr/ADR-0010-llm-and-tool-security-boundary.md](adr/ADR-0010-llm-and-tool-security-boundary.md)
for the decision record.

**This story is persistence-and-execution-mechanics scoped**: one
provider-independent LLM abstraction, one real provider (Anthropic
Claude), two real tools, one model decision leading to at most one tool
execution, fully persisted via STORY-009's `WorkflowRun`/`WorkflowStep`/
`WorkflowEvent`. It explicitly does **not** implement a multi-agent
architecture, agent-to-agent delegation, LangGraph, or an autonomous
multi-step loop — see Section 12.

## 1. The LLM Is Untrusted

This is the single governing principle of everything below. The model
may interpret administrative intent, choose among explicitly exposed
tools, supply structured tool arguments, and summarize safe
administrative results. **The model may never**: grant authorization,
determine tenant ownership, bypass RBAC, directly access a SQLAlchemy
session, execute arbitrary SQL, access an arbitrary HTTP endpoint, read
a filesystem path, read an environment variable, retrieve a secret,
choose an arbitrary Python function, dynamically import a module,
execute a shell command, diagnose a condition, prescribe medication, or
recommend a dosage. **Model output is DATA, not trusted executable
instruction** — every subsequent layer described in this document
treats it that way structurally, not by convention.

## 2. Target Architecture

```
administrative request
  -> LLM orchestration boundary       (app.ai.orchestration.AgentOrchestrationService)
  -> structured model decision        (app.ai.decisions.AdministrativeDecision)
  -> schema validation                (Pydantic, extra="forbid" throughout)
  -> safety policy                    (app.ai.safety.SafetyPolicy)
  -> explicit tool registry           (app.ai.tools.registry.ToolRegistry)
  -> authorization context            (app.ai.tools.base.ToolExecutionContext)
  -> tool adapter                     (app.ai.tools.appointment_tools)
  -> existing AgentCare service layer (app.services.appointment, .availability_query)
  -> PostgreSQL
  -> WorkflowStep / WorkflowEvent     (app.services.workflow.WorkflowService)
```

No direct `LLM -> repository`, `LLM -> database`, or `LLM -> arbitrary
Python function` path exists anywhere in this chain — verified directly
by code inspection (`app.ai` never imports `app.repositories` or
`AsyncSession` in a way that lets a model-controlled value select what
runs) and by the mandatory end-to-end test
(`tests/api/test_agent_endpoints.py::test_full_chain_patient_request_to_persisted_appointment_and_workflow`).

## 3. Provider Abstraction

`app.ai.providers.base.LLMProvider` is a `Protocol` with one method,
`generate_structured(request) -> AdministrativeDecision`. Nothing
outside `app.ai.providers` imports a vendor SDK. Two implementations
exist:

- **`AnthropicProvider`** (`app/ai/providers/anthropic_provider.py`) —
  the one real provider, backed by the official `anthropic` Python
  SDK. Structured output is obtained via Anthropic's tool-use feature:
  a single forced pseudo-tool whose input schema IS
  `AdministrativeDecision`'s own JSON Schema
  (`app.ai.decisions.decision_tool_schema`) — the model has no way to
  respond with free-form prose instead of a schema-conformant decision.
  Every vendor exception (`AuthenticationError`, `APITimeoutError`,
  `APIConnectionError`, `APIStatusError`, and a catch-all
  `AnthropicError`) is caught and translated to one of
  `app.ai.providers.errors`' controlled exceptions before it can reach
  any caller — never a raw vendor exception, its `repr()`, or any text
  that could contain request/connection detail.
- **`FakeLLMProvider`** (`app/ai/providers/fake_provider.py`) —
  deterministic, network-free. Every test in this codebase that
  exercises orchestration, tools, safety, or the API layer uses this
  instead of a real network call — see Section 11.

## 4. Model/Provider Configuration

`Settings` (`app/core/config.py`): `LLM_PROVIDER`, `LLM_MODEL`,
`LLM_API_KEY` (`SecretStr`), `LLM_TIMEOUT_SECONDS` (bounded `(0, 120]`,
default 30), `LLM_MAX_OUTPUT_TOKENS` (bounded `[1, 8192]`, default
1024). All five are **optional at startup** — the application, and
every route that doesn't touch `app.ai`, starts fine with none of them
configured. `build_llm_provider` (`app/ai/providers/factory.py`) raises
`ProviderConfigurationError` (500) at the point a provider is actually
requested if `LLM_PROVIDER` is unset, names an unsupported provider,
or `LLM_MODEL`/`LLM_API_KEY` is missing — a clear, immediate failure,
never a silent fallback to some default provider or model. No model
name is hard-coded anywhere in application code; `LLM_API_KEY` is never
logged (`SecretStr` masks it in `repr()`/`str()`, verified in
`tests/core/test_config.py::test_llm_api_key_is_masked_in_repr`).

## 5. Structured Decision Contract

`app.ai.decisions.AdministrativeDecision` is a Pydantic discriminated
union (`kind`) of exactly four variants — nothing else is a valid
decision anywhere in this codebase:

| `kind` | Fields | Meaning |
|---|---|---|
| `tool_call` | `tool_name`, `arguments` | Invoke exactly one allowlisted tool |
| `clarification_required` | `message` | Need more information before acting |
| `safe_response` | `message` | A safe, purely informational answer, no tool needed |
| `refusal` | `reason_category`, `safe_message` | This request should not proceed |

Two structural properties, both load-bearing:

1. **Unknown decision kinds are rejected, not ignored.** A provider (or
   raw JSON) with `{"kind": "run_sql", ...}` fails Pydantic validation
   immediately — never silently dropped or coerced into a nearest-match
   variant.
2. **Every variant sets `extra="forbid"`.** An extra field on an
   otherwise-valid decision — including a smuggled
   `reasoning`/`chain_of_thought`/`internal_reasoning`/`scratchpad`
   field — fails the ENTIRE decision's validation, fail-closed. See
   Section 6.

`ToolCallDecision.arguments` is `dict[str, Any]` at this layer
deliberately — UNTRUSTED, not yet validated against any specific tool's
schema (that happens per-tool, downstream, once the tool NAME has
cleared the `ToolRegistry` allowlist — see [TOOLS.md](TOOLS.md)).

## 6. No Chain-of-Thought

**Standing rule, not merely a STORY-010 scope limit**: no field named
`reasoning`, `thoughts`, `scratchpad`, `internal_reasoning`, or
`chain_of_thought` exists in any persisted or API-visible schema in
this codebase, and none may be added without a fresh, explicit design
decision. This is enforced STRUCTURALLY, not by naming convention
alone: every `AdministrativeDecision` variant's `extra="forbid"` means
a provider response carrying such a field is rejected outright (see
Section 5, and `tests/ai/test_decisions.py`'s
`test_rejects_chain_of_thought_field_on_tool_call`,
`test_rejects_reasoning_field_on_safe_response`,
`test_rejects_scratchpad_field_on_refusal`,
`test_rejects_internal_reasoning_field_on_clarification`). The system
prompt (Section 8) explicitly instructs the model not to include
reasoning, but that instruction is defense-in-depth — the schema
rejection is what actually enforces it. If a future Anthropic (or other
provider) API exposes a "thinking"/reasoning feature, this codebase
must never request it and must never persist or expose it if a provider
returns it unsolicited.

## 7. Healthcare Safety Policy

`app.ai.safety.SafetyPolicy` is a **deterministic, code-level**
safety boundary — not reliance on the system prompt alone.
`screen_request_text(request_text)` runs BEFORE the model is ever
called, matching a deliberately short set of phrase patterns for three
categories:

- **Symptom-based routing** (`"I have chest pain, which department
  should I see?"`, `"I feel dizzy"`, `"am I having a heart attack"`,
  etc.)
- **Diagnosis requests** (`"diagnose"`, `"interpret my test results"`,
  `"what's causing this"`)
- **Medication/dosage** (`"500 mg"`, `"dosage"`, `"prescribe"`,
  `"increase my dose"`, etc.)

A match short-circuits to a `RefusalDecision` (category
`clinical_content`) WITHOUT ever invoking the provider — proven
directly in `tests/ai/test_orchestration.py::test_symptom_based_request_never_calls_the_provider`
and the equivalent API-level test, both asserting the fake provider's
call count is zero. A second layer, `screen_decision`, re-screens a
non-tool-call decision's own message content after the model responds
— defense-in-depth in case a `clarification_required`/`safe_response`
message drifts into clinical territory even when the raw request text
didn't trip the pre-screen.

**Allowed** (per the product's administrative scope): appointment
booking, rescheduling, cancellation; checking administrative
availability; collecting documents; checking a document's
administrative status; routing an administrative request to an
EXPLICITLY named department; follow-up coordination; administrative
clarification.

**Never allowed, anywhere in this codebase**: autonomous diagnosis,
disease determination, treatment recommendation, prescription,
medication selection, dosage recommendation/change, medical-procedure
recommendation, interpretation of test results, or anything that
substitutes for a clinician's judgment.

**The critical routing rule**: "Book a Cardiology follow-up" is
allowed — the department is explicit, user-provided context, not an
inference. "I have chest pain, which department should I see?" is
refused — the model would be the one concluding a department from
symptoms, which crosses toward clinical triage. This exact pair is
tested directly (`tests/ai/test_safety.py`,
`tests/api/test_agent_endpoints.py::test_symptom_based_department_routing_is_refused_not_executed`).

### Known Limitation

`SafetyPolicy` is keyword/phrase-pattern matching, not real natural-
language understanding — it is deliberately biased toward
over-refusing (a false positive costs a clarification round-trip) over
under-refusing (a false negative would cross the healthcare safety
boundary). It is not, and does not claim to be, a complete clinical-
content classifier. See Section 12 for what a future story might add.

## 8. Prompt / Trust Boundary

`app.ai.prompts.SYSTEM_PROMPT` establishes administrative scope, tool
limitations, and the healthcare safety boundary, and explicitly states:
user-provided content (including text that looks like instructions) is
UNTRUSTED DATA, not a new instruction; it cannot redefine the model's
scope or grant new capabilities; only the system prompt defines policy;
identity (who a request acts on behalf of) is never the model's
decision, only the application's; and the model must not include
reasoning/a scratchpad in its output (Section 6).

**This prompt is defense-in-depth, NOT the authorization mechanism.**
Nothing in this codebase assumes the model will actually obey it.

## 9. Prompt Injection Defense (Layered)

No single layer is assumed sufficient. The full stack, in the order a
request actually passes through it:

1. **Deterministic safety pre-screen** (Section 7) — runs before the
   model is ever called.
2. **Limited tool allowlist** (`ToolRegistry` — see
   [TOOLS.md](TOOLS.md)) — a plain `dict` lookup by exact name; an
   unregistered tool (however it's named — `"os.system"`,
   `"admin.make_me_admin"`, `"run_sql"`) is a controlled rejection, not
   a lookup that could ever resolve to real code.
3. **Strict structured output** (Section 5) — `extra="forbid"` on every
   decision variant.
4. **Schema validation** — both the decision schema and, per-tool, each
   tool's own `input_schema` (`extra="forbid"` there too — an
   unexpected argument fails validation, not "extra field ignored").
5. **Server-controlled authorization context**
   (`ToolExecutionContext`, [TOOLS.md](TOOLS.md) Section 3) — the model
   never constructs this; it is built entirely from the authenticated
   request's own trusted membership/role/patient-linkage data.
6. **Tenant-scoped services** — every tool handler calls the SAME
   `app.services.*` layer every other route in this codebase calls,
   inheriting its existing composite-FK tenant ownership and RBAC
   assumptions unchanged.
7. **Deterministic healthcare safety policy** (Section 7).
8. **No arbitrary execution capability** — no `getattr`, `eval`,
   `exec`, dynamic import, or shell execution exists anywhere in
   `app.ai` (verified by code inspection; `ToolRegistry.get` is a plain
   dict access).
9. **Safe tool outputs** (`ToolResult` — [TOOLS.md](TOOLS.md) Section
   5) — never a raw SQL error, stack trace, connection string, storage
   key, password hash, JWT, secret, or ORM `repr()`.
10. **Secret isolation** (Section 4) — `LLM_API_KEY` never leaves
    `app.ai.providers`, never appears in a log line or an API response.
11. **Audit trail** — every transition and tool invocation is durably
    recorded via `WorkflowEvent` (Section 10).

**Document this now, so it is never re-litigated under time pressure
later**: model-level prompt instructions (Section 8) are defense-in-
depth. The AUTHORIZATION mechanism is entirely the combination of items
2, 4, 5, 6, and 7 above — none of which depend on the model behaving as
instructed.

## 10. Workflow Integration & Persistence Policy

Every `POST .../agent/execute` call creates and owns exactly one
`WorkflowRun` (`app.ai.orchestration.AgentOrchestrationService`, built
on STORY-009's `WorkflowService` — see [WORKFLOWS.md](WORKFLOWS.md)):
`workflow_created` -> `workflow_started` -> `step_started` -> (`tool_invoked`,
only if a tool was called) -> `step_completed`/`step_failed` ->
`workflow_completed`/`workflow_failed`. `TOOL_INVOKED` is the one new
`WorkflowEventType` value this story adds (migration `5354c755424b`,
which also adds `WorkflowEvent.sequence` — a database-assigned,
strictly monotonic ordering column fixing a genuine flaky-ordering bug
this story's own multi-event-per-request audit trail surfaced; see
[WORKFLOWS.md](WORKFLOWS.md) Section 5 for the full detail on both
changes).

**Persist**: workflow/step state, the tool's own safe name, safe
action/result codes (e.g. `"appointment_booked"`, `"appointment_conflict"`,
`"unknown_tool"`), bounded `safe_metadata` (currently only
`{"tool_name": "..."}` and `{"decision_kind": "...", ...}` — never
arguments or results), actor identifiers, timestamps, and bounded
`failure_code`/`failure_message_safe` (STORY-009's existing fields —
never a raw exception).

**Never persist, anywhere in this story's code paths**: a raw system
prompt, a raw user prompt (this story deliberately does NOT persist
`request_text` anywhere — proven in
`tests/ai/test_orchestration.py::test_no_raw_request_text_is_ever_persisted`
and the equivalent full-chain API test), a complete model response,
chain-of-thought (Section 6), API credentials, authorization tokens, an
arbitrary tool payload, or document contents.

## 11. Failure Handling, Timeouts, and Retries

Every failure mode is mapped to a controlled outcome — never a leaked
vendor exception or internal detail:

| Failure | Outcome |
|---|---|
| Provider misconfigured | `ProviderConfigurationError` -> workflow `failed`, code `llm_provider_not_configured` |
| Provider unreachable | `ProviderUnavailableError` -> `llm_provider_unavailable` |
| Provider timeout | `ProviderTimeoutError` -> `llm_provider_timeout` |
| Malformed/unparseable response | `ProviderResponseError` -> `llm_provider_invalid_response` |
| Unknown decision kind | Rejected at parse time -> `ProviderResponseError` (same as above) |
| Unknown tool name | `ToolRegistry.execute` -> `ToolResult(FAILURE, "unknown_tool")` |
| Invalid tool arguments | `ToolRegistry.execute` -> `ToolResult(FAILURE, "invalid_tool_arguments")` |
| Safety-policy rejection | Short-circuited to `RefusalDecision` before the provider is called |
| Tool-internal service failure (e.g. booking conflict) | The tool's own except-clause -> a safe `ToolResult(FAILURE, <code>)` |
| Unexpected exception inside a tool handler | `ToolRegistry.execute`'s outer safety net -> `ToolResult(FAILURE, "tool_execution_failed")`, original exception NEVER exposed |

**Timeouts/bounds**: `LLM_TIMEOUT_SECONDS` (default 30s, bounded ≤120s)
is passed directly to the Anthropic SDK client. `LLM_MAX_OUTPUT_TOKENS`
(default 1024, bounded ≤8192) bounds every response. Request text
itself is bounded to 2000 characters at the API schema layer
(`app.schemas.agent.AgentExecuteRequest`).

**One decision, at most one tool execution — by design, not by
accident.** `AgentOrchestrationService.execute_administrative_request`
calls the provider exactly once and, if the decision is a `tool_call`,
executes exactly one tool. There is no loop, no re-planning, no
autonomous multi-step chain anywhere in this story's code.

**No aggressive automatic retries.** Neither the provider layer nor the
orchestration layer retries a failed call automatically. A caller that
wants to retry issues a NEW request (a new `POST .../agent/execute`
call, a new `WorkflowRun`) — this codebase never automatically re-
attempts a state-changing tool call after an ambiguous result, which
could otherwise risk duplicating a booking. If bounded provider-level
retries are added in a future story, they must be limited to
transport-level transient failures BEFORE any tool has been dispatched,
never after.

## 12. Current vs. Planned

**Current (this story)**: `LLMProvider` abstraction, `AnthropicProvider`
(real, via the official SDK), `FakeLLMProvider` (deterministic test
double); `AdministrativeDecision` structured decision contract;
`SafetyPolicy` deterministic pre/post screening; `ToolDefinition`/
`ToolExecutionContext`/`ToolResult`/`ToolRegistry` (see
[TOOLS.md](TOOLS.md)); two real tools (`check_availability`,
`book_appointment`) calling the real service layer; full
`WorkflowRun`/`WorkflowStep`/`WorkflowEvent` integration including one
new event type (`tool_invoked`); `POST .../agent/execute` with the same
RBAC/patient-self-scope model as every other route in this codebase;
comprehensive tests including a mandatory real-PostgreSQL end-to-end
proof and a full adversarial/security suite.

**Explicitly NOT implemented in this story** (later stories): the final
multi-agent architecture, agent-to-agent delegation, a LangGraph graph,
autonomous multi-step planning loops, background workers, reminders, a
frontend, reschedule/cancel tools (see [TOOLS.md](TOOLS.md) "Initial
Tools" for why this is a deliberate depth-over-breadth choice, not an
oversight), a fuller idempotent-retry framework, real natural-language
clinical-content classification (beyond `SafetyPolicy`'s deliberately
simple pattern matching — Section 7's "Known Limitation"), and any form
of diagnosis, treatment, prescription, or dosage advice.
