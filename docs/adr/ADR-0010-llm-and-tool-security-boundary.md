# ADR-0010: LLM and Tool Security Boundary

Status: Accepted

Date: 2026-07-27

## Context

Every story through STORY-009 built AgentCare's non-AI foundation:
tenancy, identity/RBAC, the patient/scheduling/appointment/document
domains, and — critically — a durable, race-safe workflow-persistence
layer with no LLM, agent, or tool-calling capability anywhere in the
codebase. STORY-010 is the first story to introduce an LLM. The central
risk this story exists to close, deliberately and structurally, before
any further agent work is built on top of it: an LLM is a component
that can be manipulated by the very text it is asked to interpret
(prompt injection), and whose output — however it is obtained — must
never be treated as a trusted instruction. Every design decision below
follows from one governing question: if the model is fully compromised
or successfully manipulated by hostile input, what can it actually do?
The answer this ADR commits to is: nothing more than invoke one of a
small, explicitly allowlisted set of tools, each of which enforces the
exact same tenant/RBAC/patient-self-scope rules every other route in
this codebase already enforces — and even that only after the model's
raw output has survived strict schema validation and a deterministic,
code-level healthcare-safety screen that does not depend on the model's
cooperation.

This story is also the first to introduce a real external dependency
(the Anthropic API) with a real secret (`LLM_API_KEY`), and the first
where "the request an LLM produces" and "the action the system takes"
are no longer the same step — creating a genuine question about how
much of that gap gets persisted, and in what form.

## Decision

1. **A provider-independent `LLMProvider` `Protocol`**
   (`app.ai.providers.base`), one method,
   `generate_structured(request) -> AdministrativeDecision`. Nothing
   outside `app.ai.providers` imports a vendor SDK.
2. **One real provider — Anthropic Claude** (`AnthropicProvider`, the
   official `anthropic` Python SDK), using Anthropic's tool-use feature
   (a single forced pseudo-tool whose input schema IS the decision
   schema) to obtain genuinely structured output, not prose parsed by
   regex.
3. **No LangChain/LangGraph in this story.** A direct provider adapter
   is the entire abstraction needed for "one provider, one structured
   call" — an orchestration framework would add real dependency and
   conceptual weight for a capability (multi-step, multi-agent
   orchestration) this story explicitly does not implement yet.
4. **The LLM is architecturally untrusted.** Model output is data,
   validated by Pydantic, gated by a deterministic safety policy, and
   dispatched only through an explicit tool allowlist — never treated
   as executable instruction at any layer.
5. **`AdministrativeDecision`**: a four-variant Pydantic discriminated
   union (`tool_call`/`clarification_required`/`safe_response`/
   `refusal`), every variant `extra="forbid"`. An unknown `kind` or an
   unexpected extra field (including a smuggled chain-of-thought-shaped
   field) fails validation entirely — fail-closed, not fail-open with
   the offending field silently dropped.
6. **An explicit, allowlisted `ToolRegistry`** — plain `dict` lookup by
   exact name, never `getattr`/`eval`/`exec`/dynamic import/shell
   execution. An unregistered tool name is a controlled rejection
   (`unknown_tool`), structurally incapable of resolving to real code
   no matter what a model supplies.
7. **A SERVER-CREATED `ToolExecutionContext`** — the model never
   constructs, sees as mutable, or influences `organization_id`,
   `user_id`, `role`, or (for `PATIENT` callers) `patient_id`. A tool
   handler's authorization rule is structural: for a `PATIENT` caller,
   `context.patient_id` always wins over any model-supplied
   `patient_id` argument.
8. **Every tool handler calls the EXISTING service layer**
   (`AppointmentService`, `AvailabilityQueryService`) — no new
   persistence logic is written inside `app.ai`. Authorization,
   tenant-ownership, and business-rule enforcement are inherited
   unchanged from stories already reviewed and approved.
9. **A deterministic, code-level `SafetyPolicy`** screens request text
   BEFORE the model is ever invoked, refusing symptom-based,
   diagnosis-seeking, or medication/dosage content deterministically —
   independent of what any provider (real or compromised) would have
   said about it.
10. **No chain-of-thought, anywhere, structurally enforced.** Every
    decision schema's `extra="forbid"` makes this a validation failure,
    not a documentation promise.
11. **No raw request/prompt/response persistence.** `WorkflowRun`/
    `WorkflowStep`/`WorkflowEvent` (STORY-009) record state, safe tool
    names, safe result codes, and bounded `safe_metadata` — never
    `request_text`, a system prompt, or a complete model response.
12. **Failure handling maps every known failure mode to a controlled
    outcome** — provider misconfiguration/unavailability/timeout/
    malformed response, unknown tool, invalid arguments, safety
    rejection, tool-internal failure, unexpected exception — never a
    leaked vendor exception or internal detail.
13. **No automatic retries of state-changing tool execution.** A
    caller that wants to retry issues a new request; this codebase
    never automatically re-attempts a tool call after an ambiguous
    result.
14. **One model decision, at most one tool execution — no autonomous
    loop.** Multi-step planning and multi-agent coordination are
    explicitly deferred to a future story (see Consequences).
15. **`WorkflowEvent` gains one new type, `tool_invoked`** (migration
    `5354c755424b`), carrying only a safe tool name in
    `safe_metadata`. The same migration adds `WorkflowEvent.sequence`
    — a database-assigned, strictly monotonic ordering column — fixing
    a genuine flaky-ordering bug this story's own dense, multi-event
    audit trail surfaced (two events created in rapid succession could
    previously tie on `created_at` at Python timestamp resolution, with
    a random UUID as an ineffective tiebreaker).

## Rationale

- **`Protocol`, not an ABC, for `LLMProvider`**: consistent with this
  codebase's existing `DocumentStorage` pattern (ADR-0008) — a small,
  structural interface a fake test double can satisfy trivially without
  inheriting from anything, which is exactly what `FakeLLMProvider`
  does.
- **Anthropic's tool-use for structured output, not prompted JSON**:
  asking a model to "please respond with valid JSON matching this
  schema" in prose is a soft constraint the model can violate under
  pressure (including adversarial pressure). Forcing a single named
  tool call with `tool_choice` pinned to it is a HARD constraint the
  provider's own API enforces before the response ever reaches this
  codebase — strictly stronger than the "do not parse prose with regex"
  requirement this story was given, and removes an entire class of
  "the model almost returned valid JSON" failure modes.
- **No LangChain/LangGraph**: this story needs exactly one capability —
  send a system prompt and user text, get back one structured decision.
  A general orchestration framework's value proposition (chains,
  memory, multi-tool planning loops, agent graphs) is precisely the
  capability STORY-010 was told NOT to build yet. Adopting the
  dependency now would mean carrying its abstractions, its own security
  surface, and its own upgrade/compatibility burden for a full story
  (STORY-011+) before any of that value is actually used — and would
  make it harder, not easier, to reason about exactly what the
  untrusted-input boundary is, since a framework's internals become
  part of the trust analysis. A thin, fully-audited direct adapter
  keeps that boundary exactly as large as this ADR describes and no
  larger.
- **`extra="forbid"` as the chain-of-thought enforcement mechanism**:
  a written rule ("we don't persist reasoning fields") is only as good
  as every future code change that remembers to honor it. A schema that
  structurally REJECTS an unrecognized field turns "we forgot to filter
  a new field the provider started returning" from a silent privacy/
  safety regression into an immediate, loud validation failure —
  exactly the fail-closed posture a healthcare-adjacent system should
  default to.
- **Plain `dict` lookup for tool resolution, not a decorator-based
  auto-discovery registry**: a `dict.get()` has no code path that could
  ever be tricked into resolving to something not explicitly
  `register()`-ed at startup. A fancier registry (scanning modules,
  matching by convention, supporting aliases) would each be one more
  surface a sufficiently unusual tool name might exploit. The story's
  own instructions were explicit on this point — no `getattr`,
  `eval`, dynamic import, or shell execution — and a plain dict is the
  simplest structure that makes those categorically impossible, not
  merely avoided by convention.
- **Server-created `ToolExecutionContext`, never model-influenced**:
  this is the single most important authorization boundary in the
  story. Every other control (schema validation, the tool allowlist,
  the safety policy) constrains WHAT the model can ask for; this
  control constrains WHOSE data it can ever act on, and it does so by
  construction — the model has no field, no argument, no code path that
  writes into this object. The `PATIENT`-self-scope override
  specifically (context always wins over a model-supplied
  `patient_id`) mirrors the identical, already-reviewed pattern
  `app.api.v1.endpoints.appointments`/`workflows` use for
  human-driven requests — extending a boundary this codebase already
  trusts, not inventing a new one for AI specifically.
- **Reusing the existing service layer instead of new AI-specific
  persistence logic**: `AppointmentService`/`AvailabilityQueryService`
  already carry STORY-007's reviewed, tested tenant-ownership and
  business-rule enforcement. Writing new booking logic inside a tool
  handler would either duplicate that enforcement (a maintenance and
  drift risk) or, worse, subtly weaken it. Calling the same functions
  every human-driven route calls means a tool can never be more
  permissive than the API a staff member already uses.
- **Deterministic pre-screen before the model is called, not only a
  system-prompt instruction**: the story's own instructions were
  explicit that "chest pain, which department" must never result in
  autonomous routing, and that this cannot rely solely on the system
  prompt. A regex/phrase pre-screen is crude but has a property a
  prompt instruction fundamentally lacks: it does not depend on the
  model's behavior at all. It runs identically whether the configured
  provider is fully cooperative, confused, or actively manipulated.
- **`tool_invoked` as one deliberate new event type, not a general
  metadata free-for-all**: the story's guidance was to add "one or two
  carefully justified" event types if needed, migrated correctly — not
  to reopen `WorkflowEvent`'s schema broadly. One addition, one
  migration, one clear justification (making a tool dispatch visible as
  its own audit-trail moment, distinct from a step merely starting).
- **Fixing the `sequence` ordering bug in the SAME migration**: this
  bug was discovered during this story's own mandatory pre-check
  baseline run (a flaky test failure), and is directly stressed by this
  story's own audit-trail shape (several events per tool call, created
  in rapid succession) — it is not speculative scope creep, it is a
  necessary correctness fix for the exact feature this story adds.
  Consolidating it into the migration this story already needed avoids
  a second, artificially-separated schema change for a closely related
  concern.
- **No automatic retries**: a booking tool call is not naturally
  idempotent (STORY-009's idempotency-key foundation is deliberately
  minimal — see ADR-0009). Automatically retrying after an ambiguous
  failure (e.g. a timeout where the booking may or may not have
  succeeded) risks a duplicate real-world booking, which is a much
  worse outcome than surfacing the ambiguity to a caller who can
  retry deliberately.

## Alternatives Considered

- **LangChain/LangGraph now**: rejected — see Rationale. Revisit in
  STORY-011+ once multi-step/multi-agent orchestration is actually the
  work being done; at that point a framework's value proposition
  (rather than its abstraction cost) becomes the dominant
  consideration.
- **Prompted-JSON structured output** (asking the model to emit JSON in
  free text, parsed with `json.loads`): rejected in favor of
  Anthropic's tool-use forcing — see Rationale. Would still have
  required the exact same Pydantic validation layer underneath, with a
  strictly weaker guarantee that a parseable response comes back at
  all.
- **A capability-token or scoped-permission object the model could
  request**: considered (letting the model ask for elevated scope
  explicitly) and rejected — this would still require the application
  to decide whether to grant it, so it adds a layer of indirection
  around the SAME `ToolExecutionContext` decision this ADR already
  makes server-side, without closing any additional risk.
- **A decorator-based tool auto-registration system** (`@tool` scanning
  a module): rejected — see Rationale; a plain, explicit
  `register()` call list is simpler to audit and has no discovery
  mechanism a name could accidentally or maliciously hook into.
- **Persisting `request_text` for debuggability**: considered (it would
  help operators understand what a workflow was actually asked to do)
  and rejected for this story — the story's instructions explicitly
  preferred not persisting it, and doing so would mean every future
  privacy/PHI review of this table has to additionally account for
  arbitrary natural-language patient input. A future story could make
  this a deliberate, separately-reviewed decision (with retention/
  redaction policy) if a concrete operational need justifies it — not
  a default this story should quietly establish.
- **Bounded automatic retries for provider timeouts**: considered
  (transient network blips are common) and deferred — the story's own
  guidance was to avoid this without careful idempotency guarantees,
  and this story's tools are not yet provably safe to retry
  automatically after an ambiguous outcome. A future story could add a
  narrowly-scoped retry ONLY for failures known to occur strictly
  before any tool dispatch (e.g. a connection error while awaiting the
  decision itself, with zero side effects yet).

## Consequences

- STORY-011 (multi-agent architecture) builds on `AgentOrchestrationService`,
  `ToolRegistry`, and `WorkflowService` as already-established
  primitives — it should extend the tool registry with more tools
  (following [TOOLS.md](TOOLS.md) Section 9) and introduce actual
  multi-step/multi-agent coordination, not redesign the trust boundary
  this ADR establishes. Any relaxation of "the model never constructs
  `ToolExecutionContext`" or "tools are an explicit allowlist" would
  need its own ADR, not an incremental change.
- Adding a second real provider (e.g. OpenAI, Groq) means implementing
  `LLMProvider` again in a new adapter module under `app/ai/providers/`
  and registering it in `build_llm_provider`'s supported-provider list
  — no change to `app.ai.decisions`, `app.ai.safety`, `app.ai.tools`,
  or `app.ai.orchestration`.
- Adding `reschedule_appointment`/`cancel_appointment` tools (deferred
  per [TOOLS.md](TOOLS.md) Section 6) is additive — two more
  `ToolDefinition`s, no contract change.
- If a future story needs to persist any form of request text or model
  response content, it must make that a deliberate, explicitly-reviewed
  decision (retention period, redaction, PHI implications) — not an
  incidental side effect of adding a feature that happens to have text
  available.
- `SafetyPolicy`'s keyword-based screening (Section 7 of
  [AI_SAFETY.md](AI_SAFETY.md)) is a known, documented limitation. A
  future story that wants stronger clinical-content detection (e.g. a
  dedicated classification model, or a curated, larger phrase corpus)
  should treat this as evolving `SafetyPolicy` in place — the
  pre-screen-before-the-model-is-called ARCHITECTURE established here
  should not need to change, only the classification logic inside it.
