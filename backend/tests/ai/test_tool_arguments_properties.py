"""Property-based tests (Sprint 3, `hypothesis`) for
`describe_tool_arguments` (`app.ai.tools.base`) — complements the
fixed, hand-picked cases in `tests/ai/test_tools_base.py` by checking
the function's two real invariants hold for ARBITRARY field sets, not
just the ones a human thought to write down: every field is named
exactly once, and its required/optional marker matches the schema.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, create_model
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.base import (
    ToolCategory,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
    describe_tool_arguments,
)


async def _noop_handler(
    args: BaseModel, context: ToolExecutionContext, session: AsyncSession
) -> ToolResult:
    raise NotImplementedError("never actually called by this test")


# Lowercase snake_case-ish identifiers, avoiding pydantic's own reserved
# `model_*` prefix so `create_model` never collides with its internals.
_FIELD_NAME = st.from_regex(r"[a-z][a-z_]{1,14}", fullmatch=True).filter(
    lambda name: not name.startswith("model")
)


@given(
    field_specs=st.dictionaries(
        keys=_FIELD_NAME,
        values=st.booleans(),  # True = required
        min_size=1,
        max_size=6,
    )
)
def test_describe_tool_arguments_names_every_field_with_correct_requiredness(
    field_specs: dict[str, bool],
) -> None:
    fields: dict[str, Any] = {
        name: (str, ...) if required else (str | None, None)
        for name, required in field_specs.items()
    }
    model = create_model(
        "SyntheticArguments", __config__=ConfigDict(extra="forbid"), **fields
    )
    tool = ToolDefinition(
        name="synthetic_tool",
        description="A synthetic tool for property testing.",
        category=ToolCategory.ADMINISTRATIVE_ROUTING,
        input_schema=model,
        handler=_noop_handler,
    )

    text = describe_tool_arguments(tool)

    assert "TOOL: synthetic_tool" in text
    for name, required in field_specs.items():
        matching_lines = [
            line for line in text.splitlines() if line.strip().startswith(f"- {name}:")
        ]
        assert len(matching_lines) == 1, f"expected exactly one line naming {name!r}"
        line = matching_lines[0]
        assert ("required" in line) is required
        assert ("optional" in line) is not required
