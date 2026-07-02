"""Implement A2A (Agent2Agent) endpoint for JSON-RPC 2.0 protocol.

The Agent2Agent (A2A) Protocol is an open standard designed to facilitate
communication and interoperability between independent AI agent systems.

A2A Protocol specification:
https://a2a-protocol.org/dev/specification/

The implementation currently supports JSON-RPC 2.0 transport only.
Push notifications are not implemented.
"""

import asyncio
import functools
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, NotRequired, cast

import orjson
import structlog
from langgraph_sdk.client import LangGraphClient, get_client
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from typing_extensions import TypedDict

from langgraph_api import __version__
from langgraph_api.metadata import USER_API_URL
from langgraph_api.route import ApiRequest, ApiRoute
from langgraph_api.schema import RunCommand
from langgraph_api.sse import EventSourceResponse
from langgraph_api.utils.cache import LRUCache
from langgraph_api.utils.uuids import uuid7

logger = structlog.stdlib.get_logger(__name__)

# Cache for assistant schemas (assistant_id -> schemas dict)
_assistant_schemas_cache = LRUCache[dict[str, Any]](max_size=1000, ttl=60)

MAX_HISTORY_LENGTH_REQUESTED = 10
LANGGRAPH_HISTORY_QUERY_LIMIT = 500


# ============================================================================
# JSON-RPC 2.0 Base Types (shared with MCP)
# ============================================================================


class JsonRpcErrorObject(TypedDict):
    code: int
    message: str
    data: NotRequired[Any]


class JsonRpcRequest(TypedDict):
    jsonrpc: Literal["2.0"]
    id: str | int
    method: str
    params: NotRequired[dict[str, Any]]


class JsonRpcResponse(TypedDict):
    jsonrpc: Literal["2.0"]
    id: str | int
    result: NotRequired[dict[str, Any]]
    error: NotRequired[JsonRpcErrorObject]


# ============================================================================
# A2A Specific Error Codes
# ============================================================================

# Standard JSON-RPC error codes
ERROR_CODE_PARSE_ERROR = -32700
ERROR_CODE_INVALID_REQUEST = -32600
ERROR_CODE_METHOD_NOT_FOUND = -32601
ERROR_CODE_INVALID_PARAMS = -32602
ERROR_CODE_INTERNAL_ERROR = -32603

# A2A-specific error codes (in server error range -32000 to -32099)
ERROR_CODE_TASK_NOT_FOUND = -32001
ERROR_CODE_TASK_NOT_CANCELABLE = -32002
ERROR_CODE_PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
ERROR_CODE_UNSUPPORTED_OPERATION = -32004
ERROR_CODE_CONTENT_TYPE_NOT_SUPPORTED = -32005
ERROR_CODE_INVALID_AGENT_RESPONSE = -32006


# ============================================================================
# Constants and Configuration
# ============================================================================

A2A_PROTOCOL_VERSION = "1.0"

# ============================================================================
# Legacy (v0.x) format helpers
# ============================================================================

# Maps for downgrading v1.0 → v0.x values in responses.
_ROLE_V1_TO_LEGACY: dict[str, str] = {
    "ROLE_USER": "user",
    "ROLE_AGENT": "agent",
}
_STATE_V1_TO_LEGACY: dict[str, str] = {
    "TASK_STATE_SUBMITTED": "submitted",
    "TASK_STATE_WORKING": "working",
    "TASK_STATE_COMPLETED": "completed",
    "TASK_STATE_FAILED": "failed",
    "TASK_STATE_CANCELED": "canceled",
    "TASK_STATE_INPUT_REQUIRED": "input-required",
    "TASK_STATE_REJECTED": "rejected",
    "TASK_STATE_AUTH_REQUIRED": "auth-required",
}
# Maps for upgrading v0.x → v1.0 values on input.
_ROLE_LEGACY_TO_V1: dict[str, str] = {v: k for k, v in _ROLE_V1_TO_LEGACY.items()}
_STATE_LEGACY_TO_V1: dict[str, str] = {v: k for k, v in _STATE_V1_TO_LEGACY.items()}


def _normalize_input_role(role: str) -> str:
    """Normalize incoming role to v1.0 format (accept both old and new)."""
    return _ROLE_LEGACY_TO_V1.get(role, role)


def _to_spec_format(data: Any) -> Any:
    """Recursively convert internal roles/states to A2A spec format.

    Internal code uses uppercase enums (``ROLE_AGENT``, ``TASK_STATE_WORKING``).
    The A2A spec requires lowercase (``agent``, ``working``).  Also unwraps
    the ``{"result": {"task": {…}}}`` wrapper to ``{"result": {…}}``.
    """
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            if k == "role" and isinstance(v, str):
                out[k] = _ROLE_V1_TO_LEGACY.get(v, v)
            elif k == "state" and isinstance(v, str):
                out[k] = _STATE_V1_TO_LEGACY.get(v, v)
            elif k == "result" and isinstance(v, dict) and list(v.keys()) == ["task"]:
                # Unwrap {"result": {"task": {…}}} → {"result": {…}}
                out[k] = _to_spec_format(v["task"])
            else:
                out[k] = _to_spec_format(v)
        return out
    if isinstance(data, list):
        return [_to_spec_format(item) for item in data]
    return data


@functools.lru_cache(maxsize=1)
def _client() -> LangGraphClient:
    """Get a client for local operations."""
    return get_client(url=None)


def _make_task_id(context_id: str, run_id: str) -> str:
    """Create composite A2A task ID from contextId and run_id.

    A2A spec allows task IDs to be any string (UUIDs are just an example).
    We encode both thread_id (contextId) and run_id to allow tasks/get
    to work without requiring contextId as a separate parameter.

    Format: "{context_id}:{run_id}"
    """
    return f"{context_id}:{run_id}"


def _parse_task_id(task_id: str) -> tuple[str, str]:
    """Parse composite task ID into (context_id, run_id).

    If task_id contains ":", split on first occurrence.
    Otherwise, return empty context_id (caller must get it from params).

    Returns:
        Tuple of (context_id, run_id)
    """
    if ":" in task_id:
        context_id, run_id = task_id.split(":", 1)
        return context_id, run_id
    # Fallback for raw run_id (requires contextId in params)
    return "", task_id


async def _get_assistant(
    assistant_id: str, headers: Headers | dict[str, Any] | None
) -> dict[str, Any]:
    """Get assistant with proper 404 error handling.

    Args:
        assistant_id: The assistant ID to get
        headers: Request headers

    Returns:
        The assistant dictionary

    Raises:
        ValueError: If assistant not found or other errors
    """
    try:
        return await get_client().assistants.get(assistant_id, headers=headers)
    except Exception as e:
        if (
            hasattr(e, "response")
            and hasattr(e.response, "status_code")
            and e.response.status_code == 404
        ):
            raise ValueError(f"Assistant '{assistant_id}' not found") from e
        raise ValueError(f"Failed to get assistant '{assistant_id}': {e}") from e


async def _validate_supports_messages(
    assistant: dict[str, Any],
    headers: Headers | dict[str, Any] | None,
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate that assistant supports messages if text parts are present.

    If the parts contain text parts, the agent must support the 'messages' field.
    If the parts only contain data parts, no validation is performed.

    Args:
        assistant: The assistant dictionary
        headers: Request headers
        parts: The original A2A message parts

    Returns:
        The schemas dictionary from the assistant

    Raises:
        ValueError: If assistant doesn't support messages when text parts are present
    """
    assistant_id = assistant["assistant_id"]

    cached_schemas = await _assistant_schemas_cache.get(assistant_id)
    if cached_schemas is not None:
        schemas = cached_schemas
    else:
        try:
            schemas = await get_client().assistants.get_schemas(
                assistant_id, headers=headers
            )
            _assistant_schemas_cache.set(assistant_id, schemas)
        except Exception as e:
            raise ValueError(
                f"Failed to get schemas for assistant '{assistant_id}': {e}"
            ) from e

    # Validate messages field only if there are text parts
    has_text_parts = any("text" in part for part in parts)
    if has_text_parts:
        input_schema = schemas.get("input_schema") or schemas.get("state_schema")
        if not input_schema:
            raise ValueError(
                f"Assistant '{assistant_id}' has no input schema defined. "
                f"A2A conversational agents using text parts must have an input schema with a 'messages' field."
            )

        properties = input_schema.get("properties", {})
        if "messages" not in properties:
            graph_id = assistant["graph_id"]
            raise ValueError(
                f"Assistant '{assistant_id}' (graph '{graph_id}') does not support A2A conversational messages. "
                f"Graph input schema must include a 'messages' field to accept text parts. "
                f"Available input fields: {list(properties.keys())}"
            )

    return schemas


def _extract_and_validate_command(
    message: dict[str, Any],
    context_id: str | None,
) -> tuple[RunCommand | None, dict[str, Any] | None]:
    """Extract and validate command field from A2A message.

    Args:
        message: The A2A message dict
        context_id: The context ID (thread ID) from the message

    Returns:
        Tuple of (command, error_dict). If validation passes, error_dict is None.
        If validation fails, command is None and error_dict contains code/message.
    """
    command: RunCommand | None = message.get("command")

    if command is not None and command.get("resume") and not context_id:
        # Validate that resume requires contextId (maps to thread_id)
        return None, {
            "code": ERROR_CODE_INVALID_PARAMS,
            "message": "contextId is required when resuming a task with command.resume",
        }

    return command, None


def _extract_resume_from_parts(
    parts: list[dict[str, Any]],
) -> tuple[Any | None, dict[str, Any] | None]:
    """Extract resume payload from A2A data parts.

    Returns:
        Tuple of (resume_value, error_dict). If no resume payload is found,
        resume_value is None and error_dict is None.
    """
    resume_values = []
    for part in parts:
        if "data" not in part:
            continue
        part_data = part.get("data")
        if not isinstance(part_data, dict):
            continue
        if "resume" in part_data:
            resume_values.append(part_data.get("resume"))

    if not resume_values:
        return None, None
    if len(resume_values) > 1:
        return None, {
            "code": ERROR_CODE_INVALID_PARAMS,
            "message": "Only one resume value is allowed in data parts",
        }

    return resume_values[0], None


def _extract_text_from_parts(parts: list[dict[str, Any]]) -> str | None:
    texts: list[str] = []
    for part in parts:
        if "text" not in part:
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
    if not texts:
        return None
    return "\n".join(texts)


async def _is_thread_interrupted(
    client: LangGraphClient,
    context_id: str,
    headers: Headers,
    *,
    attempts: int = 3,
    delay_seconds: float = 0.05,
) -> bool:
    for attempt in range(attempts):
        try:
            thread_info = await client.threads.get(
                thread_id=context_id,
                headers=headers,
            )
        except Exception:
            return False
        if thread_info.get("status") == "interrupted":
            return True
        if attempt < attempts - 1:
            await asyncio.sleep(delay_seconds)
    return False


async def _maybe_promote_resume_to_command(
    *,
    client: LangGraphClient,
    parts: list[dict[str, Any]],
    context_id: str | None,
    task_id: str | None,
    input_content: dict[str, Any],
    headers: Headers,
) -> tuple[RunCommand | None, dict[str, Any] | None, dict[str, Any]]:
    """Convert resume content to a command when the thread is interrupted."""
    resume_value, resume_error = _extract_resume_from_parts(parts)
    resume_source = "data" if resume_value is not None else None
    if resume_error:
        return None, resume_error, input_content

    if resume_source is None:
        text_resume = _extract_text_from_parts(parts)
        if text_resume is not None:
            resume_value = text_resume
            resume_source = "text"

    if resume_source is None:
        return None, None, input_content
    if resume_source == "data":
        if not context_id or not task_id:
            return (
                None,
                {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "contextId and taskId are required when resuming a task",
                },
                input_content,
            )
        if not await _is_thread_interrupted(client, context_id, headers):
            return (
                None,
                {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Task is not awaiting input",
                },
                input_content,
            )
        if "resume" in input_content:
            input_content = dict(input_content)
            input_content.pop("resume", None)
        return {"resume": resume_value}, None, input_content

    if not context_id:
        return None, None, input_content
    if not await _is_thread_interrupted(client, context_id, headers):
        return None, None, input_content
    if not task_id:
        return (
            None,
            {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": "contextId and taskId are required when resuming a task",
            },
            input_content,
        )

    if resume_source == "text" and "messages" in input_content:
        input_content = dict(input_content)
        input_content.pop("messages", None)

    return {"resume": resume_value}, None, input_content


def _process_a2a_message_parts(
    parts: list[dict[str, Any]],
    message_role: str,
    message_id: str,
) -> dict[str, Any]:
    """Convert A2A message parts to LangChain messages format.

    Args:
        parts: List of A2A message parts
        message_role: A2A message role ("user" or "agent")

    Returns:
        Input content with messages in LangChain format

    Raises:
        ValueError: If message parts are invalid
    """
    messages = []
    additional_data = {}

    for part in parts:
        if "text" in part:
            # Text parts become messages with role based on A2A message role
            # Map A2A role to LangGraph role
            langgraph_role = "human" if message_role == "ROLE_USER" else "assistant"
            messages.append(
                {"role": langgraph_role, "content": part["text"], "id": message_id}
            )

        elif "data" in part:
            # Data parts become structured input parameters
            part_data = part.get("data", {})
            if not isinstance(part_data, dict):
                raise ValueError(
                    "DataPart must contain a JSON object in the 'data' field"
                )
            additional_data.update(part_data)

        else:
            raise ValueError(
                "Unsupported part type. "
                "A2A agents support 'text' and 'data' parts only."
            )

    if not messages and not additional_data:
        raise ValueError("Message must contain at least one valid text or data part")

    # Create input with messages in LangChain format
    input_content = {}
    if messages:
        input_content["messages"] = messages
    if additional_data:
        input_content.update(additional_data)

    return input_content


def _extract_a2a_response(result: dict[str, Any]) -> str:
    """Extract the last assistant message from graph execution result.

    Args:
        result: Graph execution result

    Returns:
        Content of the last assistant message

    Raises:
        ValueError: If result doesn't contain messages or is invalid
    """
    if "__error__" in result:
        # Let the caller handle errors
        return str(result)

    if "messages" not in result:
        # Fallback to the full result if no messages schema. It is not optimal to do A2A on assistants without
        # a messages key, but it is not a hard requirement.
        return str(result)

    messages = result["messages"]
    if not isinstance(messages, list) or not messages:
        return str(result)

    # Find the last assistant message
    for message in reversed(messages):
        if (
            isinstance(message, dict)
            and message.get("role") == "assistant"
            and "content" in message
        ) or (message.get("type") == "ai" and "content" in message):
            return message["content"]

    # If no assistant message found, return the last message content
    last_message = messages[-1]
    if isinstance(last_message, dict):
        return last_message.get("content", str(last_message))

    return str(last_message)


def _create_interrupt_artifact(interrupts: list[dict[str, Any]]) -> dict[str, Any]:
    """Create an A2A artifact from interrupt data.

    Args:
        interrupts: List of interrupt objects with 'id' and 'value' keys

    Returns:
        A2A artifact dict with interrupt data parts
    """
    interrupt_parts = [
        {
            "kind": "data",
            "data": {
                "id": interrupt_obj.get("id"),
                "value": interrupt_obj.get("value"),
            },
        }
        for interrupt_obj in interrupts
    ]
    return {
        "artifactId": str(uuid.uuid4()),
        "name": "Interrupt",
        "description": "Agent requires input to continue",
        "parts": interrupt_parts,
    }


def _tool_result_data(it: dict[str, Any]) -> dict[str, Any] | None:
    tool_call_id = it.get("tool_call_id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        return None

    result: dict[str, Any] = {"toolCallId": tool_call_id}
    content = it.get("content")
    if content not in (None, ""):
        result["content"] = content
    for key in ("name", "status"):
        value = it.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    return result


def _lc_stream_items_to_a2a_message(
    items: list[dict[str, Any]],
    *,
    task_id: str,
    context_id: str,
    role: Literal["ROLE_AGENT", "ROLE_USER"] = "ROLE_AGENT",
) -> dict[str, Any]:
    """Convert LangChain stream "messages/*" items into a valid A2A Message.

    This takes the list found in a messages/* StreamPart's data field and
    constructs a single A2A Message object, concatenating textual content and
    preserving select structured metadata into a DataPart.

    Args:
        items: List of LangChain message dicts from stream (e.g., with keys like
            "content", "type", "response_metadata", "tool_calls", etc.)
        task_id: The A2A task ID this message belongs to
        context_id: The A2A context ID (thread) for grouping
        role: A2A role; defaults to "agent" for streamed assistant output

    Returns:
        A2A Message dict with required fields and minimally valid parts.
    """
    # Aggregate any text content across items
    text_parts: list[str] = []
    # Collect a small amount of structured data for debugging/traceability
    extra_data: dict[str, Any] = {}

    def _sse_safe_text(s: str) -> str:
        return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    for it in items:
        if not isinstance(it, dict):
            continue
        content = it.get("content")
        if isinstance(content, str) and content:
            text_parts.append(_sse_safe_text(content))
        elif isinstance(content, list):
            # Handle Anthropic-style content blocks: [{"type": "text", "text": "..."}]
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        text_parts.append(_sse_safe_text(text))
                elif isinstance(block, str) and block:
                    text_parts.append(_sse_safe_text(block))

        # Only preserve tool_calls as structured data — response_metadata is
        # internal LangChain metadata that should not leak to A2A clients.
        tc = it.get("tool_calls")
        if isinstance(tc, list) and tc:
            extra_data.setdefault("tool_calls", tc)
        tool_result = _tool_result_data(it)
        if tool_result is not None:
            extra_data.setdefault("tool_results", []).append(tool_result)

    parts: list[dict[str, Any]] = []
    if text_parts:
        parts.append({"kind": "text", "text": "".join(text_parts)})
    if extra_data:
        parts.append({"kind": "data", "data": extra_data})

    # Ensure we always produce a minimally valid A2A Message
    if not parts:
        parts = [{"kind": "text", "text": ""}]

    return {
        "kind": "message",
        "role": role,
        "parts": parts,
        "messageId": str(uuid.uuid4()),
        "taskId": task_id,
        "contextId": context_id,
    }


def _lc_items_to_status_update_event(
    items: list[dict[str, Any]],
    *,
    task_id: str,
    context_id: str,
    state: str = "TASK_STATE_WORKING",
) -> dict[str, Any]:
    """Build a TaskStatusUpdateEvent embedding a converted A2A Message.

    This avoids emitting standalone Message results (which some clients reject)
    and keeps message content within the status update per spec.
    """
    message = _lc_stream_items_to_a2a_message(
        items, task_id=task_id, context_id=context_id, role="ROLE_AGENT"
    )
    return {
        "taskId": task_id,
        "contextId": context_id,
        "kind": "status-update",
        "status": {
            "state": state,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        "final": False,
    }


def _map_runs_create_error_to_rpc(
    exception: Exception, assistant_id: str, thread_id: str | None = None
) -> dict[str, Any]:
    """Map runs.create() exceptions to A2A JSON-RPC error responses.

    Args:
        exception: Exception from runs.create()
        assistant_id: The assistant ID that was used
        thread_id: The thread ID that was used (if any)

    Returns:
        A2A error response dictionary
    """
    if hasattr(exception, "response") and hasattr(exception.response, "status_code"):
        status_code = exception.response.status_code
        error_text = str(exception)

        if status_code == 404:
            # Check if it's a thread or assistant not found
            if "thread" in error_text.lower() or "Thread" in error_text:
                return {
                    "error": {
                        "code": ERROR_CODE_INVALID_PARAMS,
                        "message": f"Thread '{thread_id}' not found. Please create the thread first before sending messages to it.",
                        "data": {
                            "thread_id": thread_id,
                            "error_type": "thread_not_found",
                        },
                    }
                }
            else:
                return {
                    "error": {
                        "code": ERROR_CODE_INVALID_PARAMS,
                        "message": f"Assistant '{assistant_id}' not found",
                    }
                }
        elif status_code == 400:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": f"Invalid request: {error_text}",
                }
            }
        elif status_code == 403:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Access denied to assistant or thread",
                }
            }
        else:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": f"Failed to create run: {error_text}",
                }
            }

    return {
        "error": {
            "code": ERROR_CODE_INTERNAL_ERROR,
            "message": "Internal server error",
        }
    }


def _map_runs_get_error_to_rpc(
    exception: Exception, task_id: str, thread_id: str
) -> dict[str, Any]:
    """Map runs.get() exceptions to A2A JSON-RPC error responses.

    Args:
        exception: Exception from runs.get()
        task_id: The task/run ID that was requested
        thread_id: The thread ID that was requested

    Returns:
        A2A error response dictionary
    """
    if hasattr(exception, "response") and hasattr(exception.response, "status_code"):
        status_code = exception.response.status_code
        error_text = str(exception)

        status_code_handlers = {
            404: {
                "error": {
                    "code": ERROR_CODE_TASK_NOT_FOUND,
                    "message": f"Task '{task_id}' not found in thread '{thread_id}'",
                }
            },
            400: {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": f"Invalid request: {error_text}",
                }
            },
            403: {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Access denied to task",
                }
            },
        }

        return status_code_handlers.get(
            status_code,
            {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": f"Failed to get task: {error_text}",
                }
            },
        )

    return {
        "error": {
            "code": ERROR_CODE_INTERNAL_ERROR,
            "message": "Internal server error",
        }
    }


def _convert_messages_to_a2a_format(
    messages: list[dict[str, Any]],
    task_id: str,
    context_id: str,
) -> list[dict[str, Any]]:
    """Convert LangChain messages to A2A message format.

    Args:
        messages: List of LangChain messages
        task_id: The task ID to assign to all messages
        context_id: The context ID to assign to all messages

    Returns:
        List of A2A messages
    """

    # Convert each LangChain message to A2A format
    a2a_messages = []
    for msg in messages:
        if isinstance(msg, dict):
            msg_type = msg.get("type", "ai")
            msg_role = msg.get("role", "")
            content = msg.get("content", "")
            id = msg.get("id") or str(uuid7())

            # Support both LangChain style (type: "human"/"ai") and OpenAI style (role: "user"/"assistant")
            # Map to A2A roles: "human"/"user" -> "ROLE_USER", everything else -> "ROLE_AGENT"
            a2a_role = (
                "ROLE_USER"
                if msg_type == "human" or msg_role == "user"
                else "ROLE_AGENT"
            )

            parts: list[dict[str, Any]] = [{"kind": "text", "text": str(content)}]
            extra_data: dict[str, Any] = {}
            tc = msg.get("tool_calls")
            if isinstance(tc, list) and tc:
                extra_data["tool_calls"] = tc
            tool_result = _tool_result_data(msg)
            if tool_result is not None:
                extra_data["tool_results"] = [tool_result]
            if extra_data:
                parts.append({"kind": "data", "data": extra_data})

            a2a_message = {
                "kind": "message",
                "role": a2a_role,
                "parts": parts,
                "messageId": id,
                "taskId": task_id,
                "contextId": context_id,
            }
            a2a_messages.append(a2a_message)

    return a2a_messages


async def _create_task_response(
    task_id: str,
    context_id: str,
    result: dict[str, Any],
    assistant_id: str,
) -> dict[str, Any]:
    """Create A2A Task response structure for both success and failure cases.

    Args:
        task_id: The task/run ID
        context_id: The context/thread ID
        message: Original A2A message from request
        result: LangGraph execution result
        assistant_id: The assistant ID used
        headers: Request headers

    Returns:
        A2A Task response dictionary
    """
    # Convert result messages to A2A message format
    messages = result.get("messages", []) or []
    thread_history = _convert_messages_to_a2a_format(messages, task_id, context_id)

    base_task: dict[str, Any] = {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "history": thread_history,
    }

    if "__error__" in result:
        base_task["status"] = {
            "state": "TASK_STATE_FAILED",
            "message": {
                "kind": "message",
                "role": "ROLE_AGENT",
                "parts": [
                    {
                        "kind": "text",
                        "text": f"Error executing assistant: {result['__error__']['error']}",
                    }
                ],
                "messageId": str(uuid.uuid4()),
                "taskId": task_id,
                "contextId": context_id,
            },
        }
    elif "__interrupt__" in result:
        base_task["status"] = {
            "state": "TASK_STATE_INPUT_REQUIRED",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        base_task["artifacts"] = [_create_interrupt_artifact(result["__interrupt__"])]
    else:
        artifact_id = str(uuid.uuid4())
        artifacts = [
            {
                "artifactId": artifact_id,
                "name": "Assistant Response",
                "description": f"Response from assistant {assistant_id}",
                "parts": [
                    {
                        "kind": "text",
                        "text": _extract_a2a_response(result),
                    }
                ],
            }
        ]

        base_task["status"] = {
            "state": "TASK_STATE_COMPLETED",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        base_task["artifacts"] = artifacts

    return {"result": {"task": base_task}}


# ============================================================================
# Main A2A Endpoint Handler
# ============================================================================


def handle_get_request() -> Response:
    """Handle HTTP GET requests (streaming not currently supported).

    Returns:
        405 Method Not Allowed
    """
    return Response(status_code=405)


def handle_delete_request() -> Response:
    """Handle HTTP DELETE requests (session termination not currently supported).

    Returns:
        404 Not Found
    """
    return Response(status_code=405)


async def handle_post_request(request: ApiRequest, assistant_id: str) -> Response:
    """Handle HTTP POST requests containing JSON-RPC messages.

    Args:
        request: The incoming HTTP request
        assistant_id: The assistant ID from the URL path

    Returns:
        JSON-RPC response
    """
    body = await request.body()

    try:
        message = orjson.loads(body)
    except orjson.JSONDecodeError:
        # JSON-RPC 2.0: Parse error (-32700) - Invalid JSON was received
        return create_jsonrpc_error_response(
            ERROR_CODE_PARSE_ERROR, "Invalid JSON payload"
        )

    if not isinstance(message, dict):
        # JSON-RPC 2.0: Invalid Request (-32600) - Not a valid Request object
        return create_jsonrpc_error_response(
            ERROR_CODE_INVALID_REQUEST, "Invalid message format: expected object"
        )

    if message.get("jsonrpc") != "2.0":
        # JSON-RPC 2.0: Invalid Request (-32600) - Missing or invalid jsonrpc version
        return create_jsonrpc_error_response(
            ERROR_CODE_INVALID_REQUEST,
            "Invalid JSON-RPC message: missing or invalid jsonrpc version",
            message.get("id"),
        )

    # Route based on message type
    id_ = message.get("id")
    method = message.get("method")

    # Backward-compat: normalize old A2A v0.x method names to v1.0 names.
    # The official a2a-sdk (0.3.x) still sends old names.
    _METHOD_ALIASES: dict[str, str] = {
        "message/send": "SendMessage",
        "message/stream": "SendStreamingMessage",
        "tasks/get": "GetTask",
        "tasks/cancel": "CancelTask",
    }
    if isinstance(method, str) and method in _METHOD_ALIASES:
        # Slash method names are spec-standard; convert response to spec format
        request.state.a2a_spec_format = True
        method = _METHOD_ALIASES[method]
        message = {**message, "method": method}
    else:
        request.state.a2a_spec_format = False

    # Validate id type: JSON-RPC 2.0 requires id to be String, Number, or Null
    # Objects and arrays are not valid id types
    if id_ is not None and not isinstance(id_, (str, int, float)):
        return create_jsonrpc_error_response(
            ERROR_CODE_INVALID_REQUEST,
            "Invalid JSON-RPC request: 'id' must be a string, number, or null",
            None,  # Can't echo back invalid id
        )

    # Validate method type: JSON-RPC 2.0 requires method to be a String
    # Per A2A TCK, invalid method type returns "Method Not Found" (-32601)
    if method is not None and not isinstance(method, str):
        return create_jsonrpc_error_response(
            ERROR_CODE_METHOD_NOT_FOUND,
            "Method not found: method must be a string",
            id_ if isinstance(id_, (str, int, float, type(None))) else None,
        )

    # Validate method is a known A2A method
    known_methods = {
        "SendMessage",
        "SendStreamingMessage",
        "GetTask",
        "CancelTask",
        "ListTasks",
        "GetExtendedAgentCard",
    }
    if method is not None and method not in known_methods:
        return create_jsonrpc_error_response(
            ERROR_CODE_METHOD_NOT_FOUND,
            f"Method not found: {method}",
            id_,
        )

    params = message.get("params", {})
    if method in ("SendMessage", "SendStreamingMessage"):
        if not isinstance(params, dict):
            return create_jsonrpc_error_response(
                ERROR_CODE_INVALID_PARAMS,
                "Invalid params: must be an object",
                id_,
            )
        msg = params.get("message")
        if not msg or not isinstance(msg, dict):
            return create_jsonrpc_error_response(
                ERROR_CODE_INVALID_PARAMS,
                "Missing or invalid 'message' in params",
                id_,
            )

    accept_header = request.headers.get("Accept") or ""
    if method == "SendStreamingMessage":
        if not _accepts_media_type(accept_header, "text/event-stream"):
            return create_error_response(
                "Accept header must include text/event-stream for streaming", 400
            )
    else:
        if not _accepts_media_type(accept_header, "application/json"):
            return create_error_response(
                "Accept header must include application/json", 400
            )

    if id_ is not None and method:
        # JSON-RPC request: has id and method
        return await handle_jsonrpc_request(
            request, cast("JsonRpcRequest", message), assistant_id
        )
    elif id_ is not None and ("result" in message or "error" in message):
        # JSON-RPC response: has id plus result or error (not expected in A2A server context)
        return handle_jsonrpc_response()
    elif id_ is not None:
        # Has id but no method and not a valid response - Invalid Request
        # JSON-RPC 2.0: Request objects MUST have a "method" member
        return create_jsonrpc_error_response(
            ERROR_CODE_INVALID_REQUEST,
            "Invalid JSON-RPC request: missing 'method' field",
            id_,
        )
    else:
        # JSON-RPC 2.0: Invalid Request (-32600) - Neither request nor notification
        return create_jsonrpc_error_response(
            ERROR_CODE_INVALID_REQUEST,
            "Invalid message format: must be a JSON-RPC request or notification",
        )


def create_error_response(message: str, status_code: int) -> Response:
    """Create a JSON error response.

    Args:
        message: Error message
        status_code: HTTP status code

    Returns:
        JSON error response
    """
    return Response(
        content=orjson.dumps({"error": message}),
        status_code=status_code,
        media_type="application/json",
    )


def create_jsonrpc_error_response(
    code: int, message: str, id: str | int | float | None = None
) -> Response:
    """Create a JSON-RPC 2.0 error response.

    Per JSON-RPC 2.0 spec, error responses MUST have HTTP 200 status.
    The error is conveyed in the response body, not via HTTP status code.

    Args:
        code: JSON-RPC error code (e.g., -32700 for parse error)
        message: Human-readable error message
        id: Request ID (None for parse errors where id couldn't be determined)

    Returns:
        JSON-RPC 2.0 compliant error response with HTTP 200 status
    """
    return JSONResponse(
        {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}
    )


def _accepts_media_type(accept_header: str, media_type: str) -> bool:
    """Return True if the Accept header allows the provided media type."""
    if not accept_header:
        return False

    target = media_type.lower()
    for media_range in accept_header.split(","):
        value = media_range.strip().lower()
        if not value:
            continue
        candidate = value.split(";", 1)[0].strip()
        if candidate == "*/*" or candidate == target:
            return True
        if candidate.endswith("/*"):
            type_prefix = candidate.split("/", 1)[0]
            if target.startswith(f"{type_prefix}/"):
                return True
    return False


# ============================================================================
# JSON-RPC Message Handlers
# ============================================================================


async def handle_jsonrpc_request(
    request: ApiRequest, message: JsonRpcRequest, assistant_id: str
) -> Response:
    """Handle JSON-RPC requests with A2A methods.

    Args:
        request: The HTTP request
        message: Parsed JSON-RPC request
        assistant_id: The assistant ID from the URL path

    Returns:
        JSON-RPC response
    """
    method = message["method"]
    params = message.get("params", {})
    # Route to appropriate A2A method handler
    if method == "SendStreamingMessage":
        return await handle_message_stream(request, params, assistant_id, message["id"])
    elif method == "SendMessage":
        result_or_error = await handle_message_send(request, params, assistant_id)
    elif method == "GetTask":
        result_or_error = await handle_tasks_get(request, params)
    elif method == "CancelTask":
        result_or_error = await handle_tasks_cancel(request, params)
    elif method == "ListTasks":
        result_or_error = await handle_list_tasks(request, params)
    elif method == "GetExtendedAgentCard":
        result_or_error = await handle_get_extended_card(request, assistant_id)
    else:
        result_or_error = {
            "error": {
                "code": ERROR_CODE_METHOD_NOT_FOUND,
                "message": f"Method not found: {method}",
            }
        }

    response_keys = set(result_or_error.keys())
    if not (response_keys == {"result"} or response_keys == {"error"}):
        raise AssertionError(
            "Internal server error. Invalid response format in A2A implementation"
        )

    response_body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": message["id"],
        **result_or_error,
    }
    if getattr(request.state, "a2a_spec_format", False):
        response_body = _to_spec_format(response_body)
    return JSONResponse(response_body)


def handle_jsonrpc_response() -> Response:
    """Handle JSON-RPC responses (not expected in server context).

    Args:
        message: Parsed JSON-RPC response

    Returns:
        202 Accepted acknowledgement
    """
    return Response(status_code=202)


# ============================================================================
# A2A Method Implementations
# ============================================================================


async def handle_message_send(
    request: ApiRequest, params: dict[str, Any], assistant_id: str
) -> dict[str, Any]:
    """Handle message/send requests to create or continue tasks.

    This method:
    1. Accepts A2A Messages containing text/file/data parts
    2. Maps to LangGraph assistant execution
    3. Returns Task objects with status and results

    Args:
        request: HTTP request for auth/headers
        params: A2A MessageSendParams
        assistant_id: The target assistant ID from the URL

    Returns:
        {"result": Task} or {"error": JsonRpcErrorObject}
    """
    client = _client()
    context_id: str | None
    run_context = params.get("context")

    try:
        message = params.get("message")
        if not message:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Missing 'message' in params",
                }
            }

        message_id = message.get("messageId")
        if not message_id:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Missing required 'messageId' in message",
                }
            }

        role = message.get("role")
        if not role:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Missing required 'role' in message",
                }
            }

        parts = message.get("parts", [])
        if not isinstance(parts, list):
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Invalid params: 'parts' must be an array",
                }
            }
        if not parts:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Message must contain at least one part",
                }
            }

        try:
            assistant = await _get_assistant(assistant_id, request.headers)
            await _validate_supports_messages(assistant, request.headers, parts)
        except ValueError as e:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": str(e),
                }
            }

        # Process A2A message parts into LangChain messages format
        try:
            message_role = _normalize_input_role(message.get("role", "ROLE_USER"))
            input_content = _process_a2a_message_parts(parts, message_role, message_id)
        except ValueError as e:
            return {
                "error": {
                    "code": ERROR_CODE_CONTENT_TYPE_NOT_SUPPORTED,
                    "message": str(e),
                }
            }

        context_id = message.get("contextId")
        # Check if this is a continuation (taskId provided in message)
        existing_task_id = message.get("taskId")

        # Extract and validate command (LangGraph extension for resuming interrupts)
        command, command_error = _extract_and_validate_command(message, context_id)
        if command_error:
            return {"error": command_error}
        if command is not None and command.get("resume") and existing_task_id is None:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "taskId is required when resuming a task",
                }
            }
        if command is None:
            (
                command,
                command_error,
                input_content,
            ) = await _maybe_promote_resume_to_command(
                client=client,
                parts=parts,
                context_id=context_id,
                task_id=existing_task_id,
                input_content=input_content,
                headers=request.headers,
            )
            if command_error:
                return {"error": command_error}

        if existing_task_id is not None and command is None:
            await logger.awarning(
                "User requested to resume a task without specifying a Command.",
                task_id=existing_task_id,
            )

        # If no contextId provided, generate a UUID so we don't pass None to runs.create
        if context_id is None:
            context_id = str(uuid.uuid4())

        try:
            run = await client.runs.create(
                thread_id=context_id,
                assistant_id=assistant_id,
                input=input_content,
                command=command,
                context=run_context,
                if_not_exists="create",
                headers=request.headers,
            )
        except Exception as e:
            error_response = _map_runs_create_error_to_rpc(e, assistant_id, context_id)
            if error_response.get("error", {}).get("code") == ERROR_CODE_INTERNAL_ERROR:
                raise
            return error_response

        result = await client.runs.join(
            thread_id=run["thread_id"],
            run_id=run["run_id"],
            headers=request.headers,
        )

        context_id = str(run["thread_id"])
        # If continuing an existing task, preserve the original task_id
        # Otherwise create a new composite task_id
        task_id = existing_task_id or _make_task_id(context_id, run["run_id"])

        return await _create_task_response(
            task_id=task_id,
            context_id=context_id,
            result=result,
            assistant_id=assistant_id,
        )

    except Exception:
        logger.exception(f"Error in message/send for assistant {assistant_id}")
        return {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": "Internal server error",
            }
        }


async def _get_historical_messages_for_task(
    context_id: str,
    task_run_id: str,
    request_headers: Headers,
    history_length: int | None = None,
) -> list[Any]:
    """Get historical messages for a specific task by matching run_id."""
    history = await get_client().threads.get_history(
        context_id,
        limit=LANGGRAPH_HISTORY_QUERY_LIMIT,
        metadata={"run_id": task_run_id},
        headers=request_headers,
    )

    if history:
        # Find the checkpoint with the highest step number (final state for this task)
        target_checkpoint = max(
            history, key=lambda c: c.get("metadata", {}).get("step", 0)
        )
        values = target_checkpoint["values"]
        messages = values.get("messages", [])

        # Apply client-requested history length limit per A2A spec
        if history_length is not None and len(messages) > history_length:
            # Return the most recent messages up to the limit
            messages = messages[-history_length:]
        return messages
    else:
        return []


async def handle_tasks_get(
    request: ApiRequest, params: dict[str, Any]
) -> dict[str, Any]:
    """Handle tasks/get requests to retrieve task status.

    This method:
    1. Accepts task ID from params
    2. Maps to LangGraph run/thread status
    3. Returns current Task state and results

    Args:
        request: HTTP request for auth/headers
        params: A2A TaskQueryParams containing task ID

    Returns:
        {"result": Task} or {"error": JsonRpcErrorObject}
    """
    client = _client()

    try:
        task_id_raw = params.get("id")
        context_id_param = params.get("contextId")
        history_length = params.get("historyLength")

        if not task_id_raw:
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Missing required parameter: id (task_id)",
                }
            }

        # Parse composite task_id to extract context_id and run_id
        parsed_context_id, run_id = _parse_task_id(task_id_raw)

        # Use contextId from params if provided, otherwise from task_id
        context_id = context_id_param or parsed_context_id

        if not context_id:
            # If task_id isn't a composite ID and no contextId provided, task doesn't exist
            return {
                "error": {
                    "code": ERROR_CODE_TASK_NOT_FOUND,
                    "message": f"Task not found: {task_id_raw}",
                }
            }

        # Keep original task_id for A2A response (preserve what was sent/received)
        task_id = task_id_raw

        # Validate history_length parameter per A2A spec
        if history_length is not None:
            if not isinstance(history_length, int) or history_length < 0:
                return {
                    "error": {
                        "code": ERROR_CODE_INVALID_PARAMS,
                        "message": "historyLength must be a non-negative integer",
                    }
                }
            if history_length > MAX_HISTORY_LENGTH_REQUESTED:
                return {
                    "error": {
                        "code": ERROR_CODE_INVALID_PARAMS,
                        "message": f"historyLength cannot exceed {MAX_HISTORY_LENGTH_REQUESTED}",
                    }
                }

        try:
            # TODO: fix the N+1 query issue
            run_info, thread_info = await asyncio.gather(
                client.runs.get(
                    thread_id=context_id,
                    run_id=run_id,
                    headers=request.headers,
                ),
                client.threads.get(
                    thread_id=context_id,
                    headers=request.headers,
                ),
            )
        except Exception as e:
            error_response = _map_runs_get_error_to_rpc(e, run_id, context_id)
            if error_response.get("error", {}).get("code") == ERROR_CODE_INTERNAL_ERROR:
                # For unmapped errors, re-raise to be caught by outer exception handler
                raise
            return error_response

        lg_status = run_info.get("status", "unknown")

        if lg_status == "pending":
            a2a_state = "TASK_STATE_SUBMITTED"
        elif lg_status == "running":
            a2a_state = "TASK_STATE_WORKING"
        elif lg_status == "success":
            # Hack hack: if the thread **at present** is interrupted, assume
            # the run also is interrupted
            if thread_info.get("status") == "interrupted":
                a2a_state = "TASK_STATE_INPUT_REQUIRED"
            else:
                # Inspect whether there are next tasks
                a2a_state = "TASK_STATE_COMPLETED"
        elif (
            lg_status == "interrupted"
        ):  # Note that this is if you interrupt FROM the outside (i.e., with double texting)
            a2a_state = "TASK_STATE_INPUT_REQUIRED"
        elif lg_status in ["error", "timeout"]:
            a2a_state = "TASK_STATE_FAILED"
        else:
            a2a_state = "TASK_STATE_SUBMITTED"

        try:
            task_run_id = run_info.get("run_id")
            messages = await _get_historical_messages_for_task(
                context_id, task_run_id, request.headers, history_length
            )
            thread_history = _convert_messages_to_a2a_format(
                messages, task_id, context_id
            )
        except Exception as e:
            await logger.aexception(f"Failed to get thread state for tasks/get: {e}")
            thread_history = []

        # Build the A2A Task response
        task_response: dict[str, Any] = {
            "kind": "task",
            "id": task_id,
            "contextId": context_id,
            "history": thread_history,
            "status": {
                "state": a2a_state,
            },
        }

        # Add result message if completed
        if a2a_state == "TASK_STATE_COMPLETED":
            task_response["status"]["message"] = {
                "kind": "message",
                "role": "ROLE_AGENT",
                "parts": [{"kind": "text", "text": "Task completed successfully"}],
                "messageId": str(uuid.uuid4()),
                "taskId": task_id,
            }
        elif a2a_state == "TASK_STATE_FAILED":
            task_response["status"]["message"] = {
                "kind": "message",
                "role": "ROLE_AGENT",
                "parts": [
                    {"kind": "text", "text": f"Task failed with status: {lg_status}"}
                ],
                "messageId": str(uuid.uuid4()),
                "taskId": task_id,
            }

        return {"result": task_response}

    except Exception as e:
        await logger.aexception(
            f"Error in tasks/get for task {params.get('id')}: {e!s}", exc_info=True
        )
        return {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": "Internal server error",
            }
        }


async def handle_tasks_cancel(
    request: ApiRequest, params: dict[str, Any]
) -> dict[str, Any]:
    """Handle tasks/cancel requests to cancel running tasks.

    This method:
    1. Accepts task ID from params
    2. Checks if the task exists
    3. Cancels the run if it's pending/running
    4. Returns the Task with state "canceled"

    Args:
        request: HTTP request for auth/headers
        params: A2A TaskIdParams containing task ID

    Returns:
        {"result": Task} or {"error": JsonRpcErrorObject}
    """
    client = _client()

    task_id_raw = params.get("id")
    context_id_param = params.get("contextId")

    if not task_id_raw:
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": "Missing required parameter: id (task_id)",
            }
        }

    # Parse composite task_id to extract context_id and run_id
    parsed_context_id, run_id = _parse_task_id(task_id_raw)
    context_id = context_id_param or parsed_context_id

    if not context_id:
        # If task_id isn't a composite ID and no contextId provided, task doesn't exist
        return {
            "error": {
                "code": ERROR_CODE_TASK_NOT_FOUND,
                "message": f"Task not found: {task_id_raw}",
            }
        }

    # Check if the task exists first
    try:
        run_info = await client.runs.get(
            thread_id=context_id,
            run_id=run_id,
            headers=request.headers,
        )
    except Exception as e:
        # Check if it's a 404 error
        if (
            hasattr(e, "response")
            and hasattr(e.response, "status_code")
            and e.response.status_code == 404
        ):
            return {
                "error": {
                    "code": ERROR_CODE_TASK_NOT_FOUND,
                    "message": f"Task not found: {task_id_raw}",
                }
            }
        # For other errors, return internal error
        return {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": f"Failed to check task status: {e!s}",
            }
        }

    # Check if the task is in a cancelable state
    lg_status = run_info.get("status", "unknown")

    # If task is already in a terminal state, return it as "canceled" per A2A spec
    # The spec expects idempotent behavior - tasks/cancel always returns canceled state
    if lg_status not in ("pending", "running"):
        task_response = {
            "kind": "task",
            "id": task_id_raw,
            "contextId": context_id,
            "status": {
                "state": "TASK_STATE_CANCELED",
                "message": {
                    "kind": "message",
                    "role": "ROLE_AGENT",
                    "parts": [
                        {
                            "kind": "text",
                            "text": f"Task cancel acknowledged (was: {lg_status})",
                        }
                    ],
                    "messageId": str(uuid.uuid4()),
                    "taskId": task_id_raw,
                },
            },
        }
        return {"result": task_response}

    # Cancel the run
    try:
        await client.runs.cancel(
            thread_id=context_id,
            run_id=run_id,
            wait=True,  # Wait for cancellation to complete
            action="interrupt",
            headers=request.headers,
        )
    except Exception as e:
        await logger.aerror(f"Failed to cancel run {run_id}: {e!s}", exc_info=True)
        return {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": f"Failed to cancel task: {e!s}",
            }
        }

    # Return the canceled task
    task_response = {
        "kind": "task",
        "id": task_id_raw,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_CANCELED",
            "message": {
                "kind": "message",
                "role": "ROLE_AGENT",
                "parts": [{"kind": "text", "text": "Task was canceled"}],
                "messageId": str(uuid.uuid4()),
                "taskId": task_id_raw,
            },
        },
    }

    return {"result": task_response}


def _lg_status_to_a2a_state(lg_status: str) -> str:
    """Map a LangGraph run status to an A2A task state."""
    mapping = {
        "pending": "TASK_STATE_SUBMITTED",
        "running": "TASK_STATE_WORKING",
        "success": "TASK_STATE_COMPLETED",
        "interrupted": "TASK_STATE_INPUT_REQUIRED",
        "error": "TASK_STATE_FAILED",
        "timeout": "TASK_STATE_FAILED",
    }
    return mapping.get(lg_status, "TASK_STATE_SUBMITTED")


async def handle_list_tasks(
    request: ApiRequest, params: dict[str, Any]
) -> dict[str, Any]:
    """Handle ListTasks requests to list tasks with filtering and pagination.

    Supports:
    - contextId filter (maps to thread_id)
    - status filter (A2A task state)
    - statusTimestampAfter filter (ISO 8601 timestamp)
    - pageSize / pageToken pagination
    - historyLength for including message history
    - includeArtifacts for including artifacts

    Args:
        request: HTTP request for auth/headers
        params: ListTasksParams

    Returns:
        {"result": ListTasksResult} or {"error": JsonRpcErrorObject}
    """
    # --- Validate params ---
    raw_page_size = params.get("pageSize")
    if raw_page_size is not None and (
        not isinstance(raw_page_size, int) or raw_page_size < 1 or raw_page_size > 100
    ):
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": "pageSize must be an integer between 1 and 100.",
            }
        }
    requested_page_size: int = raw_page_size if raw_page_size is not None else 50

    raw_history_length = params.get("historyLength")
    if raw_history_length is not None and (
        not isinstance(raw_history_length, int) or raw_history_length < 0
    ):
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": "historyLength must be a non-negative integer.",
            }
        }
    history_length: int = raw_history_length if raw_history_length is not None else 0

    valid_states = {
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_AUTH_REQUIRED",
    }
    status_filter = params.get("status")
    # Normalize legacy state values (e.g. "completed" → "TASK_STATE_COMPLETED")
    if status_filter is not None and status_filter in _STATE_LEGACY_TO_V1:
        status_filter = _STATE_LEGACY_TO_V1[status_filter]
    if status_filter is not None and status_filter not in valid_states:
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": f"Invalid status value: '{status_filter}'.",
            }
        }

    status_timestamp_after = params.get("statusTimestampAfter")
    if status_timestamp_after is not None:
        try:
            if isinstance(status_timestamp_after, str):
                datetime.fromisoformat(status_timestamp_after.replace("Z", "+00:00"))
            else:
                raise ValueError("Not a string")
        except (ValueError, TypeError):
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "statusTimestampAfter must be a valid ISO 8601 timestamp.",
                }
            }

    page_token = params.get("pageToken")
    if page_token is not None:
        if not isinstance(page_token, str):
            return {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "pageToken must be a string.",
                }
            }
        # Our page tokens are numeric offsets; reject non-numeric tokens
        if page_token != "":
            try:
                int(page_token)
            except (ValueError, TypeError):
                return {
                    "error": {
                        "code": ERROR_CODE_INVALID_PARAMS,
                        "message": "Invalid pageToken.",
                    }
                }

    include_artifacts = params.get("includeArtifacts", False)
    context_id = params.get("contextId")

    client = _client()

    try:
        # Determine which threads to search
        if context_id:
            thread_ids = [context_id]
        else:
            threads = await client.threads.search(
                limit=1000,
                headers=request.headers,
            )
            thread_ids = [t["thread_id"] for t in threads]

        # Collect all runs from matching threads
        all_tasks: list[dict[str, Any]] = []
        for tid in thread_ids:
            try:
                runs = await client.runs.list(
                    tid,
                    limit=100,
                    headers=request.headers,
                )
            except Exception:
                continue

            for run in runs:
                task_id = _make_task_id(tid, run["run_id"])
                a2a_state = _lg_status_to_a2a_state(run.get("status", "unknown"))

                if status_filter and a2a_state != status_filter:
                    continue

                timestamp = run.get("updated_at") or run.get("created_at") or ""
                if hasattr(timestamp, "isoformat"):
                    timestamp = timestamp.isoformat()

                if status_timestamp_after and timestamp:
                    try:
                        if timestamp < status_timestamp_after:
                            continue
                    except (TypeError, ValueError):
                        pass

                task: dict[str, Any] = {
                    "kind": "task",
                    "id": task_id,
                    "contextId": tid,
                    "status": {
                        "state": a2a_state,
                        "timestamp": timestamp,
                    },
                }

                if history_length > 0:
                    try:
                        messages = await _get_historical_messages_for_task(
                            tid, run["run_id"], request.headers, history_length
                        )
                        task["history"] = _convert_messages_to_a2a_format(
                            messages, task_id, tid
                        )
                    except Exception:
                        task["history"] = []
                else:
                    task["history"] = []

                if not include_artifacts:
                    task["artifacts"] = []

                all_tasks.append(task)

        # Sort by timestamp descending (newest first)
        all_tasks.sort(
            key=lambda t: t["status"].get("timestamp", ""),
            reverse=True,
        )

        total_size = len(all_tasks)
        offset = int(page_token) if page_token else 0

        page_tasks = all_tasks[offset : offset + requested_page_size]
        next_offset = offset + len(page_tasks)
        next_page_token = str(next_offset) if next_offset < total_size else ""

        return {
            "result": {
                "tasks": page_tasks,
                "totalSize": total_size,
                "pageSize": len(page_tasks) if page_tasks else 0,
                "nextPageToken": next_page_token,
            }
        }

    except Exception:
        logger.exception("Error in ListTasks")
        return {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": "Internal server error",
            }
        }


async def handle_get_extended_card(
    request: ApiRequest, assistant_id: str
) -> dict[str, Any]:
    """Handle agent/getAuthenticatedExtendedCard requests.

    Returns the agent card as the "extended" card. Since we don't have
    auth differentiation, this returns the same card as the public one.

    Args:
        request: HTTP request for auth/headers
        assistant_id: The target assistant ID

    Returns:
        {"result": AgentCard} or {"error": JsonRpcErrorObject}
    """
    try:
        agent_card = await generate_agent_card(request, assistant_id)
        return {"result": agent_card}
    except ValueError as e:
        return {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": str(e),
            }
        }
    except Exception:
        logger.exception(f"Error generating extended agent card for {assistant_id}")
        return {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": "Internal server error",
            }
        }


# ============================================================================
# Agent Card Generation
# ============================================================================


async def generate_agent_card(request: ApiRequest, assistant_id: str) -> dict[str, Any]:
    """Generate A2A Agent Card for a specific assistant.

    Each LangGraph assistant becomes its own A2A agent with a dedicated
    agent card describing its individual capabilities and skills.

    Args:
        request: HTTP request for auth/headers
        assistant_id: The specific assistant ID to generate card for

    Returns:
        A2A AgentCard dictionary for the specific assistant
    """
    client = _client()

    assistant = await _get_assistant(assistant_id, request.headers)
    schemas = await client.assistants.get_schemas(assistant_id, headers=request.headers)

    # Extract schema information for metadata
    input_schema = schemas.get("input_schema") or schemas.get("state_schema") or {}
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    assistant_name = assistant["name"]
    assistant_description = (
        assistant.get("description") or f"{assistant_name} assistant"
    )

    # For now, each assistant has one main skill - itself
    skills = [
        {
            "id": f"{assistant_id}-main",
            "name": f"{assistant_name} Capabilities",
            "description": assistant_description,
            "tags": ["assistant", "langgraph"],
            "examples": [],
            "inputModes": ["application/json", "text/plain"],
            "outputModes": ["application/json", "text/plain"],
            "metadata": {
                "inputSchema": {
                    "required": required,
                    "properties": sorted(properties.keys()),
                    "supportsA2A": "messages" in properties,
                }
            },
        }
    ]

    if USER_API_URL:
        base_url = USER_API_URL.rstrip("/")
    else:
        # Fallback to constructing from request
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        host = request.url.hostname or "localhost"
        port = request.url.port
        path = (
            request.url.path.removesuffix("/.well-known/agent-card.json")
            .removesuffix("/.well-known/agent.json")
            .removesuffix(f"/a2a/{assistant_id}")
        )
        if port and (
            (scheme == "http" and port != 80) or (scheme == "https" and port != 443)
        ):
            base_url = f"{scheme}://{host}:{port}{path}"
        else:
            base_url = f"{scheme}://{host}{path}"
    agent_path = f"/a2a/{assistant_id}"

    agent_url = f"{base_url}{agent_path}"

    return {
        "name": assistant_name,
        "description": assistant_description,
        "url": agent_url,
        "supportedInterfaces": [
            {
                "url": agent_url,
                "protocolBinding": "jsonrpc",
                "protocolVersion": "1.0",
            },
        ],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,  # Not implemented yet
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": skills,
        "version": __version__,
    }


async def handle_agent_card_endpoint(request: ApiRequest) -> Response:
    """Serve Agent Card for a specific assistant.
    ---
    summary: Serve Agent Card for a specific assistant.
    description: >
        Serves the Agent Card for a specific assistant identified by the
        assistant_id query parameter. Falls back to the DEFAULT_A2A_ASSISTANT_ID
        env var when no query parameter is provided.
        Expected URL is /.well-known/agent-card.json?assistant_id=uuid
    """
    try:
        # Get assistant_id from query parameters, env var, or file fallback
        # The file fallback is used by run_a2a_tck.py for testing
        assistant_id = request.query_params.get("assistant_id")
        if not assistant_id:
            assistant_id = os.environ.get("DEFAULT_A2A_ASSISTANT_ID")
        if not assistant_id:
            tck_file = "/tmp/langgraph_tck_assistant_id"
            if os.path.exists(tck_file):
                with open(tck_file) as f:
                    assistant_id = f.read().strip()

        if not assistant_id:
            error_response = {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Missing required query parameter: assistant_id",
                }
            }
            return Response(
                content=orjson.dumps(error_response),
                status_code=400,
                media_type="application/json",
            )

        agent_card = await generate_agent_card(request, assistant_id)
        return JSONResponse(agent_card)

    except ValueError as e:
        # A2A validation error or assistant not found
        error_response = {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": str(e),
            }
        }
        return Response(
            content=orjson.dumps(error_response),
            status_code=400,
            media_type="application/json",
        )
    except Exception:
        logger.exception("Failed to generate agent card")
        error_response = {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": "Internal server error",
            }
        }
        return Response(
            content=orjson.dumps(error_response),
            status_code=500,
            media_type="application/json",
        )


# ============================================================================
# Message Streaming
# ============================================================================


async def handle_message_stream(
    request: ApiRequest,
    params: dict[str, Any],
    assistant_id: str,
    rpc_id: str | int,
) -> Response:
    """Handle message/stream requests and stream JSON-RPC responses via SSE.

    Each SSE "data" is a JSON-RPC 2.0 response object. We emit:
    - An initial TaskStatusUpdateEvent with state "submitted".
    - Optionally a TaskStatusUpdateEvent with state "working" on first update.
    - A final Task result when the run completes.
    - A JSON-RPC error if anything fails.
    """
    client = _client()
    run_context = params.get("context")

    async def stream_body():
        try:
            message = params.get("message")
            if not message:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": ERROR_CODE_INVALID_PARAMS,
                            "message": "Missing 'message' in params",
                        },
                    },
                )
                return

            message_id = message.get("messageId")
            if not message_id:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": ERROR_CODE_INVALID_PARAMS,
                            "message": "Missing required 'messageId' in message",
                        },
                    },
                )
                return

            role = message.get("role")
            if not role:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": ERROR_CODE_INVALID_PARAMS,
                            "message": "Missing required 'role' in message",
                        },
                    },
                )
                return

            parts = message.get("parts", [])
            if not parts:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": ERROR_CODE_INVALID_PARAMS,
                            "message": "Message must contain at least one part",
                        },
                    },
                )
                return

            try:
                assistant = await _get_assistant(assistant_id, request.headers)
                await _validate_supports_messages(assistant, request.headers, parts)
            except ValueError as e:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": ERROR_CODE_INVALID_PARAMS,
                            "message": str(e),
                        },
                    },
                )
                return

            # Process A2A message parts into LangChain messages format
            try:
                message_role = _normalize_input_role(message.get("role", "ROLE_USER"))
                input_content = _process_a2a_message_parts(
                    parts, message_role, message_id
                )
            except ValueError as e:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": ERROR_CODE_CONTENT_TYPE_NOT_SUPPORTED,
                            "message": str(e),
                        },
                    },
                )
                return

            # Check if this is a continuation (taskId provided in message)
            existing_task_id = message.get("taskId")
            context_id_from_message = message.get("contextId")

            # Extract and validate command (LangGraph extension for resuming interrupts)
            command, command_error = _extract_and_validate_command(
                message, context_id_from_message
            )
            if command_error:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": command_error,
                    },
                )
                return
            if (
                command is not None
                and command.get("resume")
                and existing_task_id is None
            ):
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": ERROR_CODE_INVALID_PARAMS,
                            "message": "taskId is required when resuming a task",
                        },
                    },
                )
                return
            if command is None:
                (
                    command,
                    command_error,
                    input_content,
                ) = await _maybe_promote_resume_to_command(
                    client=client,
                    parts=parts,
                    context_id=context_id_from_message,
                    task_id=existing_task_id,
                    input_content=input_content,
                    headers=request.headers,
                )
                if command_error:
                    yield (
                        b"message",
                        {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "error": command_error,
                        },
                    )
                    return

            if existing_task_id is not None and command is None:
                await logger.awarning(
                    "User requested to resume a task without specifying a Command.",
                    task_id=existing_task_id,
                )

            if context_id_from_message is None:
                context_id_from_message = str(uuid.uuid4())
            context_id = context_id_from_message

            stream = client.runs.stream(
                thread_id=context_id,
                assistant_id=assistant_id,
                stream_mode=["messages", "values"],
                if_not_exists="create",
                input=input_content,
                command=command,
                context=run_context,
                headers=request.headers,
            )

            # The first event is always metadata including the run_id
            run_event = await anext(stream)
            run_id = run_event.data.get("run_id")
            if not run_id:
                raise ValueError("Stream did not include run_id")

            # If continuing an existing task, preserve the original task_id
            task_id = existing_task_id or _make_task_id(context_id, run_id)
            # Emit initial Task object to establish task context
            initial_task = {
                "kind": "task",
                "id": task_id,
                "contextId": context_id,
                "history": [
                    {
                        "kind": "message",
                        **message,
                        "taskId": task_id,
                        "contextId": context_id,
                    }
                ],
                "status": {
                    "state": "TASK_STATE_SUBMITTED",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            }
            yield (
                b"message",
                {"jsonrpc": "2.0", "id": rpc_id, "result": {"task": initial_task}},
            )

            result = None
            err = None
            notified_is_working = False
            async for chunk in stream:
                try:
                    if chunk.event == "metadata":
                        data = chunk.data or {}
                        if data.get("status") == "run_done":
                            final_message = None
                            if isinstance(result, dict):
                                try:
                                    final_text = _extract_a2a_response(result)
                                    final_message = {
                                        "kind": "message",
                                        "role": "ROLE_AGENT",
                                        "parts": [{"kind": "text", "text": final_text}],
                                        "messageId": str(uuid.uuid4()),
                                        "taskId": task_id,
                                        "contextId": context_id,
                                    }
                                except Exception:
                                    await logger.aexception(
                                        "Failed to extract final message from result",
                                        result=result,
                                    )
                            if final_message is None:
                                final_message = {
                                    "kind": "message",
                                    "role": "ROLE_AGENT",
                                    "parts": [{"kind": "text", "text": str(result)}],
                                    "messageId": str(uuid.uuid4()),
                                    "taskId": task_id,
                                    "contextId": context_id,
                                }
                            # Check if result contains an interrupt
                            final_state = (
                                "TASK_STATE_INPUT_REQUIRED"
                                if isinstance(result, dict)
                                and "__interrupt__" in result
                                else "TASK_STATE_COMPLETED"
                            )
                            completed = {
                                "taskId": task_id,
                                "contextId": context_id,
                                "kind": "status-update",
                                "status": {
                                    "state": final_state,
                                    "message": final_message,
                                    "timestamp": datetime.now(UTC).isoformat(),
                                },
                                "final": True,
                            }
                            # Emit interrupt artifact as separate event
                            if isinstance(result, dict) and "__interrupt__" in result:
                                yield (
                                    b"message",
                                    {
                                        "jsonrpc": "2.0",
                                        "id": rpc_id,
                                        "result": {
                                            "taskId": task_id,
                                            "contextId": context_id,
                                            "kind": "artifact-update",
                                            "artifact": _create_interrupt_artifact(
                                                result["__interrupt__"]
                                            ),
                                        },
                                    },
                                )
                            yield (
                                b"message",
                                {"jsonrpc": "2.0", "id": rpc_id, "result": completed},
                            )
                            return
                        # TODO: This should just be sent from the start as well
                        if data.get("run_id") and not notified_is_working:
                            notified_is_working = True
                            yield (
                                b"message",
                                {
                                    "jsonrpc": "2.0",
                                    "id": rpc_id,
                                    "result": {
                                        "taskId": task_id,
                                        "contextId": context_id,
                                        "kind": "status-update",
                                        "status": {"state": "TASK_STATE_WORKING"},
                                        "final": False,
                                    },
                                },
                            )
                    elif chunk.event == "error":
                        err = chunk.data
                    elif chunk.event == "values":
                        err = None  # Error was retriable
                        result = chunk.data
                    elif chunk.event.startswith("messages"):
                        err = None  # Error was retriable
                        items = chunk.data or []
                        if isinstance(items, list) and items:
                            update = _lc_items_to_status_update_event(
                                items,
                                task_id=task_id,
                                context_id=context_id,
                                state="TASK_STATE_WORKING",
                            )
                            # Skip emitting events with no meaningful content
                            # (e.g. Anthropic metadata-only chunks).
                            msg = update.get("status", {}).get("message", {})
                            parts = msg.get("parts", [])
                            has_content = any(
                                (p.get("text") or p.get("data")) for p in parts
                            )
                            if has_content:
                                yield (
                                    b"message",
                                    {
                                        "jsonrpc": "2.0",
                                        "id": rpc_id,
                                        "result": update,
                                    },
                                )
                    else:
                        await logger.awarning(
                            "Ignoring unknown event type: " + chunk.event
                        )

                except Exception as e:
                    await logger.aexception("Failed to process message stream")
                    err = {"error": type(e).__name__, "message": str(e)}
                    continue

            # If we exit unexpectedly, send a final status based on error presence
            final_message = None
            if isinstance(err, dict) and ("__error__" in err or "error" in err):
                msg = (
                    err.get("__error__", {}).get("error")
                    if isinstance(err.get("__error__"), dict)
                    else err.get("message")
                )
                await logger.aerror("Failed to process message stream", err=err)
                final_message = {
                    "kind": "message",
                    "role": "ROLE_AGENT",
                    "parts": [{"kind": "text", "text": str(msg or "")}],
                    "messageId": str(uuid.uuid4()),
                    "taskId": task_id,
                    "contextId": context_id,
                }
            # Determine final state: failed > input-required > completed
            if err:
                fallback_state = "TASK_STATE_FAILED"
            elif isinstance(result, dict) and "__interrupt__" in result:
                fallback_state = "TASK_STATE_INPUT_REQUIRED"
            else:
                fallback_state = "TASK_STATE_COMPLETED"
            fallback = {
                "taskId": task_id,
                "contextId": context_id,
                "kind": "status-update",
                "status": {
                    "state": fallback_state,
                    **({"message": final_message} if final_message else {}),
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                "final": True,
            }
            # Emit interrupt artifact as separate event
            if isinstance(result, dict) and "__interrupt__" in result:
                yield (
                    b"message",
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "taskId": task_id,
                            "contextId": context_id,
                            "kind": "artifact-update",
                            "artifact": _create_interrupt_artifact(
                                result["__interrupt__"]
                            ),
                        },
                    },
                )
            yield (b"message", {"jsonrpc": "2.0", "id": rpc_id, "result": fallback})
        except Exception as e:
            await logger.aerror(
                f"Error in message/stream for assistant {assistant_id}: {e!s}",
                exc_info=True,
            )
            yield (
                b"message",
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {
                        "code": ERROR_CODE_INTERNAL_ERROR,
                        "message": "Internal server error",
                    },
                },
            )

    spec_format = getattr(request.state, "a2a_spec_format", False)

    async def consume_():
        async for chunk in stream_body():
            if spec_format:
                event_type, data = chunk
                data = _to_spec_format(data)
                chunk = (event_type, data)
            await logger.adebug("A2A.stream_body: Yielding chunk", chunk=chunk)
            yield chunk

    return EventSourceResponse(
        consume_(), headers={"Content-Type": "text/event-stream"}
    )


# ============================================================================
# Route Definitions
# ============================================================================


async def handle_a2a_assistant_endpoint(request: ApiRequest) -> Response:
    """A2A endpoint handler for specific assistant.
    ---
    summary: A2A endpoint handler for specific assistant.
    description: >
        Handles A2A JSON-RPC requests for a specific assistant identified
        by the assistant_id path parameter.
        Expected URL is /a2a/{assistant_id}
    """
    # Extract assistant_id from URL path params
    assistant_id = request.path_params.get("assistant_id")
    if not assistant_id:
        return create_error_response("Missing assistant ID in URL", 400)

    if request.method == "POST":
        return await handle_post_request(request, assistant_id)
    elif request.method == "GET":
        # Return agent card for this assistant (A2A spec: GET on agent URL returns card)
        return await handle_assistant_agent_card_endpoint(request)
    elif request.method == "DELETE":
        return handle_delete_request()
    else:
        return Response(status_code=405)  # Method Not Allowed


async def handle_assistant_agent_card_endpoint(request: ApiRequest) -> Response:
    """Serve Agent Card for a specific assistant via path parameter.
    ---
    summary: Serve Agent Card for a specific assistant via path parameter.
    description: >
        Serves the Agent Card for a specific assistant via path parameter,
        following the A2A multi-tenant pattern where each agent has its own
        well-known agent card path.
        Expected URL is /a2a/{assistant_id}/.well-known/agent-card.json
    """
    try:
        assistant_id = request.path_params.get("assistant_id")
        if not assistant_id:
            error_response = {
                "error": {
                    "code": ERROR_CODE_INVALID_PARAMS,
                    "message": "Missing assistant_id in path",
                }
            }
            return Response(
                content=orjson.dumps(error_response),
                status_code=400,
                media_type="application/json",
            )

        agent_card = await generate_agent_card(request, assistant_id)
        return JSONResponse(agent_card)

    except ValueError as e:
        error_response = {
            "error": {
                "code": ERROR_CODE_INVALID_PARAMS,
                "message": str(e),
            }
        }
        return Response(
            content=orjson.dumps(error_response),
            status_code=400,
            media_type="application/json",
        )
    except Exception:
        logger.exception("Failed to generate agent card")
        error_response = {
            "error": {
                "code": ERROR_CODE_INTERNAL_ERROR,
                "message": "Internal server error",
            }
        }
        return Response(
            content=orjson.dumps(error_response),
            status_code=500,
            media_type="application/json",
        )


a2a_routes = [
    # Per-assistant A2A endpoints: /a2a/{assistant_id}
    ApiRoute(
        "/a2a/{assistant_id}",
        handle_a2a_assistant_endpoint,
        methods=["GET", "POST", "DELETE"],
    ),
    # Per-assistant agent card (multi-tenant pattern)
    ApiRoute(
        "/a2a/{assistant_id}/.well-known/agent-card.json",
        handle_assistant_agent_card_endpoint,
        methods=["GET"],
    ),
    ApiRoute(
        "/a2a/{assistant_id}/.well-known/agent.json",
        handle_assistant_agent_card_endpoint,
        methods=["GET"],
    ),
    # Domain-root agent card (with query param or env var fallback)
    ApiRoute(
        "/.well-known/agent-card.json", handle_agent_card_endpoint, methods=["GET"]
    ),
]
