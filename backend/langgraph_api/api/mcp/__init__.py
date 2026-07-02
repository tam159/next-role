"""MCP (Model Context Protocol) server using Streamable HTTP transport.

Specification (2025-11-25):
  https://modelcontextprotocol.io/specification/2025-11-25

Supported protocol versions: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25.

LangGraph exposes each Assistant as an MCP Tool.  The implementation is
stateless (no session persistence) and returns ``application/json`` only.
"""

from langgraph_api.api.mcp._routes import mcp_routes

__all__ = ["mcp_routes"]
