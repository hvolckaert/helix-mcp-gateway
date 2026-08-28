"""Sanitized MCP-adapter errors."""

from helix_mcp.observability.errors import ToolExecutionError

__all__ = [
    "ToolAdapterError",
    "ToolExecutionError",
    "ToolInputError",
]


class ToolAdapterError(ValueError):
    """Tool input or mapping failure safe to return to an MCP client."""

    code = "TOOL_ADAPTER_ERROR"


class ToolInputError(ToolAdapterError):
    code = "TOOL_INPUT_INVALID"
