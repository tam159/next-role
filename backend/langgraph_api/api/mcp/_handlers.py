from __future__ import annotations

import asyncio
import functools
import json
from typing import TYPE_CHECKING, Any, cast

import structlog
from langgraph_sdk.client import LangGraphClient, get_client
from starlette.responses import JSONResponse, Response

from langgraph_api import __version__
from langgraph_api.api.mcp import _sanitizers
from langgraph_api.api.mcp._constants import (
    DEFAULT_PAGE_SIZE,
    ERROR_CODE_INVALID_PARAMS,
    ERROR_CODE_METHOD_NOT_FOUND,
    LATEST_PROTOCOL_VERSION,
    MAX_ASSISTANTS,
    SUPPORTED_PROTOCOL_VERSIONS,
)

if TYPE_CHECKING:
    from langgraph_api.api.mcp._models import JsonRpcRequest
    from langgraph_api.route import ApiRequest

logger = structlog.stdlib.get_logger(__name__)


@functools.lru_cache(maxsize=1)
def _client() -> LangGraphClient:
    """Get a client for local operations."""
    return get_client(url=None)


def handle_delete_request() -> Response:
    """Handle HTTP DELETE requests for session termination.

    Returns:
        Response with appropriate status code
    """
    return Response(status_code=404)


def handle_get_request() -> Response:
    """Handle HTTP GET requests for streaming (not currently supported).

    Returns:
        Method not allowed response
    """
    # Does not support streaming at the moment
    return Response(status_code=405)


async def handle_post_request(request: ApiRequest) -> Response:
    """Handle HTTP POST requests for JSON-RPC messaging.

    Args:
        request: The incoming request object

    Returns:
        Response to the JSON-RPC message
    """
    body = await request.body()

    # Validate JSON
    try:
        message = json.loads(body)
    except json.JSONDecodeError:
        return create_error_response("Invalid JSON", 400)

    # Validate Accept header
    if not is_valid_accept_header(request):
        return create_error_response(
            "Accept header must include application/json or text/event-stream", 400
        )

    # Validate message format
    if not isinstance(message, dict):
        return create_error_response("Invalid message format.", 400)

    # Determine message type and route to appropriate handler
    id_ = message.get("id")
    method = message.get("method")

    # Check for required jsonrpc field
    if message.get("jsonrpc") != "2.0":
        return create_error_response(
            "Invalid JSON-RPC message. Missing or invalid jsonrpc version.", 400
        )

    # Careful ID checks as the integer 0 is a valid ID
    if id_ is not None and method:
        # JSON-RPC request
        return await handle_jsonrpc_request(request, cast("JsonRpcRequest", message))
    elif id_ is not None:
        # JSON-RPC response
        return Response(status_code=202)
    elif method:
        # JSON-RPC notification
        return Response(status_code=202)
    else:
        # Invalid message format
        return create_error_response(
            "Invalid message format. A message is to be either a JSON-RPC "
            "request, response, or notification."
            "Please see the Messages section of the Streamable HTTP RFC "
            "for more information.",
            400,
        )


def is_valid_accept_header(request: ApiRequest) -> bool:
    """Check if the Accept header contains supported content types.

    Args:
        request: The incoming request

    Returns:
        True if header contains application/json or text/event-stream
    """
    accept_header = request.headers.get("Accept", "")
    accepts_json = "application/json" in accept_header
    accepts_sse = "text/event-stream" in accept_header
    return accepts_json or accepts_sse


def create_error_response(message: str, status_code: int) -> Response:
    """Create a JSON error response.

    Args:
        message: The error message
        status_code: The HTTP status code

    Returns:
        JSON response with error details
    """
    return Response(
        content=json.dumps({"error": message}),
        status_code=status_code,
        media_type="application/json",
    )


async def handle_jsonrpc_request(
    request: ApiRequest,
    message: JsonRpcRequest,
) -> Response:
    """Handle JSON-RPC requests (messages with both id and method).

    Args:
        request: The incoming request object
        message: The parsed JSON-RPC message

    Returns:
        Response to the request
    """
    method = message["method"]
    params = message.get("params", {})

    if method == "initialize":
        result_or_error = handle_initialize_request(message)
    elif method == "ping":
        result_or_error = {"result": {}}
    elif method == "tools/list":
        result_or_error = await handle_tools_list(request, params)
    elif method == "tools/call":
        result_or_error = await handle_tools_call(request, params)
    else:
        result_or_error = {
            "error": {
                "code": ERROR_CODE_METHOD_NOT_FOUND,
                "message": f"Method not found: {method}",
            }
        }

    # Process the result or error output
    exists = {"error", "result"} - set(result_or_error.keys())
    if len(exists) != 1:
        raise AssertionError(
            "Internal server error. Invalid response in MCP protocol implementation."
        )

    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": message["id"],
            **result_or_error,
        }
    )


def _negotiate_protocol_version(requested: str) -> str:
    """Negotiate MCP protocol version with the client.

    Returns the requested version if supported, otherwise the latest.
    """
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def handle_initialize_request(message: JsonRpcRequest) -> dict[str, Any]:
    """Handle initialize requests to establish protocol version.

    Negotiates the protocol version with the client. If the client requests
    a supported version, the server echoes it back. Otherwise the server
    responds with the latest version it supports and the client may disconnect
    if it cannot work with that version.

    Args:
        message: The JSON-RPC request message

    Returns:
        Response with negotiated protocol details
    """
    params = message.get("params", {})
    requested = params.get("protocolVersion", LATEST_PROTOCOL_VERSION)
    negotiated = _negotiate_protocol_version(requested)

    return {
        "result": {
            "protocolVersion": negotiated,
            "capabilities": {
                "tools": {
                    "listChanged": False,
                }
            },
            "serverInfo": {"name": "LangGraph", "version": __version__},
        }
    }


async def handle_tools_list(
    request: ApiRequest, params: dict[str, Any]
) -> dict[str, Any]:
    """Handle tools/list request to get available assistants as tools.

    Args:
        request: The incoming request object. Used for propagating any headers
                 for authentication purposes.
        params: The parameters for the tools/list request

    Returns:
        Dictionary containing list of available tools
    """
    client = _client()

    try:
        cursor = params.get("cursor", 0)
        cursor = int(cursor)
    except ValueError:
        cursor = 0

    # Get assistants from the API
    # For now set a large limit to get all assistants
    assistants = await client.assistants.search(
        offset=cursor, limit=DEFAULT_PAGE_SIZE, headers=request.headers
    )

    if len(assistants) == DEFAULT_PAGE_SIZE:
        next_cursor = cursor + DEFAULT_PAGE_SIZE
    else:
        next_cursor = None

    # Deduplicate by normalized name, preserving original for the title field
    seen_names: set[str] = set()
    unique: list[tuple[Any, str]] = []  # (assistant, normalized_name)
    for assistant in assistants:
        normalized = _sanitizers.normalize_name(assistant["name"])
        if normalized in seen_names:
            await logger.awarning(
                f"Duplicate assistant name found {assistant['name']}",
                name=assistant["name"],
                normalized=normalized,
            )
            continue
        seen_names.add(normalized)
        unique.append((assistant, normalized))

    async def _get_tool(assistant: Any, normalized_name: str) -> dict[str, Any] | None:
        """Get tool definition for an assistant.

        Returns None if schema generation fails (e.g., non-serializable state).
        """
        try:
            schemas = await client.assistants.get_schemas(
                assistant["assistant_id"], headers=request.headers
            )
            input_schema = schemas.get("input_schema") or {}
            if input_schema:
                _sanitizers.simplify_mcp_schema_inplace(input_schema)
            # MCP spec requires inputSchema to be a JSON Schema with type "object"
            input_schema.setdefault("type", "object")
            return {
                "name": normalized_name,
                "title": assistant["name"],
                "description": assistant.get("description")
                or f"Tool based on the {assistant['name']} assistant",
                "inputSchema": input_schema,
                "annotations": {
                    "readOnlyHint": False,
                    "destructiveHint": False,
                    "idempotentHint": False,
                    "openWorldHint": True,
                },
            }
        except Exception as e:
            await logger.awarning(
                "Failed to get schema for assistant, skipping from MCP tools list",
                assistant_id=assistant["assistant_id"],
                assistant_name=assistant["name"],
                error=str(e),
            )
            return None

    tools_or_none = await asyncio.gather(*(_get_tool(a, n) for a, n in unique))
    # Filter out assistants that failed schema generation
    tools = [t for t in tools_or_none if t is not None]

    result = {"tools": tools}

    if next_cursor is not None:
        result["nextCursor"] = next_cursor

    return {
        "result": result,
    }


async def handle_tools_call(
    request: ApiRequest, params: dict[str, Any]
) -> dict[str, Any]:
    """Handle tools/call request to execute an assistant.

    Args:
        request: The incoming request
        params: The parameters for the tool call

    Returns:
        The result of the tool execution
    """
    client = _client()

    tool_name = params.get("name")

    if not tool_name:
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": f"Unknown tool: {tool_name}",
            },
        }

    arguments = params.get("arguments", {})
    context = params.get("context")
    assistants = await client.assistants.search(
        limit=MAX_ASSISTANTS, headers=request.headers
    )
    matching_assistant = []
    for assistant in assistants:
        if (
            normalized_name := _sanitizers.normalize_name(assistant["name"])
        ) and normalized_name == tool_name:
            matching_assistant.append(assistant)

    num_assistants = len(matching_assistant)

    if num_assistants == 0:
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": f"Unknown tool: {tool_name}",
            },
        }
    elif num_assistants > 1:
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": "Multiple tools found with the same name.",
            },
        }
    else:
        tool_name = matching_assistant[0]["assistant_id"]

    value = await client.runs.wait(
        thread_id=None,
        assistant_id=tool_name,
        input=arguments,
        context=context,
        headers=request.headers,
        raise_error=False,
    )

    if isinstance(value, dict) and "__error__" in value:
        # This is a run-time error in the tool.
        return {
            "result": {
                "isError": True,
                "content": [
                    {"type": "text", "text": value["__error__"]["error"]},
                ],
            }
        }

    # All good, return the result
    return {
        "result": {
            "content": [
                {"type": "text", "text": repr(value)},
            ]
        }
    }
