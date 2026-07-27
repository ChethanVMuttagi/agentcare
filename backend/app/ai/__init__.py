"""AgentCare's AI execution foundation (STORY-010).

This package is the ONLY place in the codebase that talks to an LLM
provider. Nothing outside `app.ai` imports a vendor SDK, and nothing
inside `app.ai` reaches into `app.repositories` or an `AsyncSession`
directly — the LLM decides WHAT the caller might want; the existing
service layer (`app.services.*`) decides whether it is ALLOWED and
performs it. See docs/AI_SAFETY.md and
docs/adr/ADR-0010-llm-and-tool-security-boundary.md for the full trust
model this package enforces.

Layout:

- `app.ai.providers` — the provider-independent `LLMProvider` interface,
  the real Anthropic adapter, and a deterministic fake/test provider.
- `app.ai.decisions` — the strongly-typed `AdministrativeDecision`
  schema a provider's raw output is parsed and validated into. Model
  output is DATA, never trusted executable instruction.
- `app.ai.safety` — a deterministic, code-level safety policy. Never
  the sole enforcement mechanism (that would be "a sentence in a
  prompt") — see docs/AI_SAFETY.md "Prompt Injection Defense".
- `app.ai.tools` — the explicit, allowlisted tool contract
  (`ToolDefinition`, `ToolExecutionContext`, `ToolResult`,
  `ToolRegistry`) and the initial real tools, each calling an existing
  AgentCare service.
- `app.ai.orchestration` — ties the above together: one model decision,
  at most one tool execution, with every step durably recorded via
  `app.services.workflow.WorkflowService`.
"""
