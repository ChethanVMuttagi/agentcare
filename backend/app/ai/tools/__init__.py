"""The explicit AgentCare tool contract, registry, and initial real
tools.

See `base.py` for `ToolDefinition`/`ToolExecutionContext`/`ToolResult`,
`registry.py` for `ToolRegistry`, and `appointment_tools.py` for the
tools actually registered in this story
(`check_availability`/`book_appointment`) — each calling the REAL
existing service layer, never a fake success path.
"""
