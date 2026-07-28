# ADR-0011: Multi-Agent Coordination

Status: Accepted

Date: 2026-07-28

## Context

STORY-010 established a single-decision AI flow and, in Consequences,
explicitly deferred "actual multi-step/multi-agent coordination" to a
future story without redesigning the trust boundary it built. The
hackathon requirement STORY-011 exists to satisfy is specific and
falsifiable: AgentCare must genuinely coordinate work across "multiple
genuinely distinct agents," not merely rename one universal agent
several times, share one prompt with unrestricted tool access across
several labels, or route every request through the same execution
function under different display names. The bar is proof, not
description — automated tests must demonstrate that a Coordinator
cannot execute a domain tool, that each specialist has a SEPARATE
capability allowlist enforced in application code, that handoffs are
durably persisted, and that a specialist cannot invoke another
specialist's restricted tool.

This is also the first story where one HTTP request causes more than
one model decision, and the first where "which agent decided this" has
to be an intelligible, persisted, and truthful fact — not something
inferred after the fact from which tool happened to run.

## Decision

1. **Exactly four agents**: one Coordinator (`app/ai/agents/definitions.py`)
   and three specialists — Scheduling, Document, Routing. No fifth agent,
   no generic "worker" agent, no agent added speculatively for future
   stories.
2. **`CoordinatorDecision`** (`app/ai/coordinator_decisions.py`): a
   THREE-variant Pydantic discriminated union —
   `handoff`/`clarification_required`/`refusal` — structurally excluding
   any `tool_call`-shaped variant. The Coordinator's inability to
   execute a domain tool is therefore a fact about the TYPE SYSTEM, the
   strongest guarantee available, not a runtime permission check that a
   future change could accidentally bypass.
3. **`HandoffDecision` carries only `target_agent` (a closed
   `TargetAgent` enum) plus an OPTIONAL, small, bounded
   `task_category`** — never a coordinator-composed prompt, never hidden
   reasoning, never anything resembling chain-of-thought passed between
   agents.
4. **A specialist receives the SAME original `request_text`** the
   Coordinator was given, re-supplied directly by
   `AgentOrchestrationService` — never a Coordinator-synthesized prompt.
   This closes an entire class of cross-agent prompt-injection surface
   by construction: there is no channel through which a handoff could
   carry new instructions.
5. **`LLMProvider` gains one new method, `generate_coordinator_decision`**
   (`app/ai/providers/base.py`), alongside the UNCHANGED
   `generate_structured`. `AnthropicProvider` shares its HTTP-call and
   vendor-exception-translation logic between both; `FakeLLMProvider`
   gains an independent second configuration trio
   (`coordinator_decision`/`coordinator_raw_response`/`coordinator_error`)
   so a single fake provider instance can drive both halves of one
   multi-agent test.
6. **`AgentDefinition`/`AgentRegistry`** (`app/ai/agents/base.py`,
   `app/ai/agents/registry.py`): a direct structural mirror of
   `ToolDefinition`/`ToolRegistry` (ADR-0010) — plain `dict` lookup by
   exact name, `register()` rejects duplicates, `get()` returns `None`
   for unknown names, no dynamic resolution of any kind. Deliberately an
   in-memory application concept, NOT a database table.
7. **Per-agent tool allowlist enforcement lives in application code**
   (`AgentOrchestrationService._execute_specialist_tool_call`), checked
   BEFORE `ToolRegistry.execute()` is ever called — even though a
   forbidden tool name may be perfectly valid and registered globally.
   `ToolRegistry` itself is UNCHANGED and stays agent-agnostic, exactly
   as ADR-0010 built it; the new authorization layer sits directly above
   it, not inside it.
8. **Two new tools, each calling REAL existing code**:
   `list_patient_documents` (`app/ai/tools/document_tools.py`, calling
   `PatientDocumentService.list_documents_for_patient`) and
   `resolve_department` (`app/ai/tools/routing_tools.py`, calling a new,
   smallest-necessary `department_repository.search_by_name` function).
   The document tool returns a deliberately narrow field set — never
   `storage_key`, file bytes, `sha256`, or `size_bytes`.
9. **`WorkflowEventType` gains one new value, `agent_handoff`**
   (migration `b5456329cbdd`, following ADR-0010's exact technique for
   `tool_invoked`), recorded by a new `WorkflowService.record_agent_handoff`
   method — a structural mirror of `record_tool_invocation`.
   `safe_metadata` carries only `{"from_agent": ..., "to_agent": ...}`.
10. **Two-step workflow shape**: a coordination step
    (`agent_name="coordinator"`) always exists; a specialist-execution
    step (`agent_name=<specialist>`) exists ONLY when a real handoff
    occurred. `WorkflowStep.agent_name` (unused since STORY-009) is used
    meaningfully for the first time.
11. **Hard execution caps, unchanged in spirit from ADR-0010, extended
    by exactly one hop**: at most one Coordinator decision, at most one
    handoff, at most one specialist decision, at most one tool
    execution. No specialist-to-specialist handoff (structurally
    impossible — `AdministrativeDecision`, which specialists reuse, has
    no handoff variant), no recursion, no autonomous retry loop.
12. **The single existing `POST .../agent/execute` endpoint is
    extended, not duplicated** — one endpoint per agent was explicitly
    rejected. The response gains one new field, `handled_by_agent` (the
    stable name of whichever agent produced the final outcome), and
    otherwise keeps ADR-0010's "never expose reasoning, prompts, raw
    provider responses, or internal authorization context" contract
    unchanged.

## Rationale

- **A second `LLMProvider` method, not a schema-parametrized single
  method**: preserves STORY-010's existing test suite and
  `FakeLLMProvider(decision=...)` call-site pattern with zero breaking
  changes, while still giving the Coordinator a fully distinct,
  independently-schema-enforced decision space. A single method taking
  a schema parameter would have made "which decision shape can this
  call even produce" a runtime fact instead of a type-level one.
- **No `tool_call` variant in `CoordinatorDecision`, rather than a
  runtime check that rejects `ToolCallDecision` from the Coordinator**:
  a runtime check is still correct, but it is exactly the kind of
  control a future refactor could silently weaken (e.g. by accidentally
  reusing `AdministrativeDecision` for the Coordinator "for convenience").
  Removing the variant from the union entirely makes that mistake a
  compile-time-adjacent (Pydantic-validation-time) impossibility rather
  than a discipline the codebase has to keep remembering to maintain.
- **The specialist reprocesses the ORIGINAL request text, not a
  Coordinator-composed one**: considered letting the Coordinator write a
  short instruction for the specialist ("book an appointment for
  Tuesday") and rejected it. That would require trusting the
  Coordinator's own (LLM-produced) text as an instruction to a second
  LLM — precisely the kind of LLM-to-LLM instruction channel ADR-0010's
  entire trust model treats as untrustworthy. Re-supplying the original,
  already-pre-screened text keeps every specialist subject to the exact
  same trust boundary the Coordinator was, with no new channel in
  between.
- **Two-layer tool enforcement (global registry + per-agent allowlist),
  not one merged check**: keeping `ToolRegistry` exactly as ADR-0010
  built it (agent-agnostic, a pure name -> handler map) means no
  regression risk to STORY-010's already-reviewed tool contract. Adding
  the allowlist as a SEPARATE check in orchestration means the "which
  agent may call which tool" policy lives in exactly one place
  (`AgentDefinition.allowed_tools`), readable and auditable without
  reading `ToolRegistry` at all.
- **No agents database table**: agent definitions are static
  developer-authored configuration — identical in kind to
  `ToolDefinition`, which ADR-0010 already established should not be a
  database table. A table would add a migration, a repository, a
  service, and a real question ("what happens if a row is edited or
  deleted at runtime, mid-request?") for zero behavioral benefit over a
  `frozenset` and a `dict`.
- **Reusing `WorkflowRun`/`WorkflowStep`/`WorkflowEvent` rather than new
  multi-agent-specific tables**: STORY-009's three-table model was
  already built anticipating agent/tool participation (`ActorType.AGENT`/
  `ActorType.TOOL`, `WorkflowStep.agent_name`) — this story is the first
  to actually exercise that anticipated shape, not a reason to replace
  it. One new event type and zero new tables is the minimal schema
  change that expresses "a handoff happened" durably.
- **Exactly four agents, no more**: every additional agent is additional
  attack surface (another prompt, another tool boundary to keep correct)
  and additional complexity in exactly the place a healthcare-adjacent
  system should stay conservative. Four is the smallest number that
  proves the required property (a Coordinator plus more than one
  genuinely distinct specialist) without adding anything the hackathon
  requirement doesn't need.

## Alternatives Considered

- **A single `AdministrativeDecision` extended with a `handoff` variant,
  used by both the Coordinator and specialists**: rejected — this would
  let a specialist ALSO emit a handoff (violating "no
  specialist-to-specialist handoff") unless a second, separate runtime
  check forbade it, reintroducing exactly the kind of "this must be
  remembered, not structurally guaranteed" risk `CoordinatorDecision`'s
  separate type avoids.
- **LangGraph or a similar multi-agent orchestration framework**: 
  considered, since this story is precisely the "multi-step/multi-agent
  coordination" ADR-0010 deferred to a framework-reconsideration point.
  Still rejected for this story's scope: the required coordination
  shape (one Coordinator decision, at most one handoff, at most one
  specialist decision) is a fixed, small, fully-specified graph, not an
  open-ended one. A framework's value (dynamic graphs, cycles, complex
  state machines) is not needed yet, and adopting one now would put a
  large new dependency's internals inside the trust analysis ADR-0010
  spent significant effort keeping small. Revisit if a future story
  needs genuinely dynamic multi-step planning.
- **Letting the Coordinator generate the specialist's user-facing
  prompt/instruction**: rejected — see Rationale.
- **A database table for agent definitions**: rejected — see Rationale.
- **One endpoint per agent** (`/agent/scheduling/execute`,
  `/agent/document/execute`, ...): rejected per this story's explicit
  requirement to extend the single existing endpoint; a per-agent
  endpoint would also leak an implementation detail (which agent
  handles what) into the API surface that callers should not need to
  know in advance — deciding that is the Coordinator's entire job.
- **Skipping the "allowlisted-but-unregistered tool" defense-in-depth
  test** (since the real registries are always consistent): considered
  and rejected — a deliberately misconfigured `AgentRegistry` in a
  dedicated test proves `ToolRegistry`'s own `unknown_tool` rejection
  remains a genuinely independent second safety net, not merely
  unreachable dead code now that the allowlist check exists.

## Consequences

- A future story adding a fifth agent (e.g. a follow-up/reminder agent,
  once that capability is in scope) should follow the exact pattern
  here: one new `AgentDefinition` with its own prompt and
  `allowed_tools`, registered in `build_default_agent_registry`, and
  (if it needs one) a new tool registered in
  `app.ai.tools.registry_builder.build_full_tool_registry` — no change
  to `CoordinatorDecision`'s structural guarantees, `ToolRegistry`, or
  `AgentRegistry` themselves. Adding it to `TargetAgent` is the only
  Coordinator-side change required.
- If a future story needs genuinely dynamic, open-ended multi-step
  planning or specialist-to-specialist collaboration, that is a new
  trust-boundary decision and needs its own ADR — this one deliberately
  does not authorize it, and the current type structure
  (`CoordinatorDecision` with no handoff-from-a-specialist path) makes
  it structurally impossible without a further, explicit design change.
- `AgentRegistry`/`ToolRegistry` remain separate registries scoped to
  separate concerns (which agents exist vs. which tools exist); a
  future story should not merge them even if convenient, since the
  per-agent allowlist check specifically depends on being able to
  reason about them independently.
- The two-layer tool enforcement pattern (global registry + per-agent
  allowlist) established here is the template any future
  agent-scoped capability restriction should follow, rather than adding
  agent-awareness directly into `ToolRegistry`.
