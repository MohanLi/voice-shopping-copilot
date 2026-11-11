"""MCP client: tool discovery, calls, and adapters that hand the discovered tools to the agents."""

from voiceshop.mcp_client.client import (
    ALLOWED_TOOLS,
    MCPConnectionError,
    MCPToolClient,
    ToolCallResult,
    ToolSpec,
    UnknownToolError,
    alias_for,
)

__all__ = [
    "ALLOWED_TOOLS",
    "MCPConnectionError",
    "MCPToolClient",
    "ToolCallResult",
    "ToolSpec",
    "UnknownToolError",
    "alias_for",
]
