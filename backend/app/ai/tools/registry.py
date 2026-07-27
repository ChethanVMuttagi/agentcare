"""`ToolRegistry`: the explicit, allowlisted set of tools a model
decision may invoke.

Lookup is a plain `dict` access by exact string name — never
`getattr`, `eval`, `exec`, a dynamic import, or shell execution. An
unrecognized `tool_name` is a controlled rejection
(`ToolResultStatus.FAILURE`, code `"unknown_tool"`), not an exception
that could propagate provider/internal detail outward. See
docs/TOOLS.md "Registry".
"""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.base import ToolDefinition, ToolExecutionContext, ToolResult, ToolResultStatus


class ToolRegistry:
    """A plain, explicit allowlist. `register()` is called exactly once
    per tool, at startup (see `app.ai.tools.appointment_tools.build_default_registry`)
    — never at request time, and never based on anything a model
    supplies."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered.")
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> ToolDefinition | None:
        """Plain dict lookup — returns `None` for anything not
        explicitly registered. No fallback, no fuzzy matching, no
        dynamic resolution of any kind."""
        return self._tools.get(tool_name)

    def list_allowed(self) -> list[ToolDefinition]:
        """Every registered tool. STORY-010 does not yet vary this by
        `ToolExecutionContext` (e.g. role-specific tool visibility) —
        all registered tools are visible to every authenticated caller
        the orchestration layer runs for; authorization is enforced
        inside each tool's handler and the underlying service layer, not
        by hiding tools from the model. A future story could add
        context-aware filtering here without changing this method's
        signature in an incompatible way."""
        return list(self._tools.values())

    async def execute(
        self,
        tool_name: str,
        raw_arguments: dict[str, object],
        context: ToolExecutionContext,
        session: AsyncSession,
    ) -> ToolResult:
        """Validate `raw_arguments` against the named tool's schema and
        execute it. Always returns a `ToolResult` — never raises,
        including for an unknown tool, invalid arguments, or an
        unexpected exception inside the handler itself (a last-resort
        safety net; individual handlers are expected to catch their own
        known service-layer exceptions and translate them to a safe
        `ToolResult` — see `app.ai.tools.appointment_tools`)."""
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                code="unknown_tool",
                safe_message="The requested tool is not available.",
            )

        try:
            validated_arguments = tool.input_schema.model_validate(raw_arguments)
        except ValidationError:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                code="invalid_tool_arguments",
                safe_message="The provided arguments were not valid for this tool.",
            )

        try:
            return await tool.handler(validated_arguments, context, session)
        except Exception:
            return ToolResult(
                status=ToolResultStatus.FAILURE,
                code="tool_execution_failed",
                safe_message="This action could not be completed.",
            )
