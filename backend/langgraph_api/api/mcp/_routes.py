from __future__ import annotations

from starlette.responses import Response

from langgraph_api.api.mcp._handlers import (
    handle_delete_request,
    handle_get_request,
    handle_post_request,
)
from langgraph_api.route import ApiRequest, ApiRoute

__all__ = ["mcp_routes"]


async def handle_mcp_endpoint(request: ApiRequest) -> Response:
    # MCP endpoint handler that implements the Streamable HTTP protocol.
    # Supports POST (JSON-RPC request) and DELETE (terminate session).
    # GET (streaming session resumption) and text/event-stream not yet supported.
    # Route request based on HTTP method
    if request.method == "DELETE":
        return handle_delete_request()
    elif request.method == "GET":
        return handle_get_request()
    elif request.method == "POST":
        return await handle_post_request(request)
    else:
        # Method not allowed
        return Response(status_code=405)


# Define routes for the MCP endpoint
mcp_routes = [
    ApiRoute("/mcp", handle_mcp_endpoint, methods=["GET", "POST", "DELETE"]),
]
