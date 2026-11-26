"""MCP discovery with callable compatibility aliases."""

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Tool
from starlette.applications import Starlette
from starlette.routing import Route

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

    def stateless_app(
        self,
        *,
        max_request_body_size: int,
        host: str = "127.0.0.1",
        transport_security: TransportSecuritySettings | None = None,
    ) -> Starlette:
        """Serve request/response MCP without a standalone, unbounded GET event stream."""
        app = self.streamable_http_app(
            stateless_http=True,
            json_response=True,
            host=host,
            transport_security=transport_security,
            max_request_body_size=max_request_body_size,
        )
        for route in app.routes:
            if isinstance(route, Route) and route.path == "/mcp":
                route.methods = {"POST", "DELETE"}
        return app

    async def list_tools(self) -> list[Tool]:
        return [
            tool for tool in await super().list_tools() if tool.name not in COMPATIBILITY_ALIASES
        ]
