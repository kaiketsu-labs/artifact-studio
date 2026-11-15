"""MCP discovery with callable compatibility aliases."""

from mcp.server.mcpserver import MCPServer
from mcp.types import Tool

COMPATIBILITY_ALIASES = frozenset(
    {
        "list_presentations",
        "list_designs",
        "wait_for_feedback",
        "wait_for_design_requests",
        "reply_to_presentation_feedback",
        "append_presentation_context",
    }
)


class ToolServer[T](MCPServer[T]):
    """Keep historical calls registered while advertising the canonical surface."""

    async def list_tools(self) -> list[Tool]:
        return [
            tool for tool in await super().list_tools() if tool.name not in COMPATIBILITY_ALIASES
        ]
