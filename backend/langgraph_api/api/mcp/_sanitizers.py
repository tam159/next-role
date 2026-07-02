import re
from typing import Any

# MCP tool name constraints per the 2025-11-25 spec.
# Valid characters: ASCII letters, digits, underscore, hyphen, dot.
# Length: 1-128 characters.
_MCP_TOOL_NAME_MAX_LENGTH = 128
_MCP_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_\-.]")

LANGCHAIN_MESSAGE_TYPES = {
    "AIMessage",
    "AIMessageChunk",
    "HumanMessage",
    "HumanMessageChunk",
    "ChatMessage",
    "ChatMessageChunk",
    "SystemMessage",
    "SystemMessageChunk",
    "FunctionMessage",
    "FunctionMessageChunk",
    "ToolMessage",
    "ToolMessageChunk",
}

LANGCHAIN_SUPPORT_TYPES = {
    "ToolCall",
    "ToolCallChunk",
    "InvalidToolCall",
    "UsageMetadata",
    "InputTokenDetails",
    "OutputTokenDetails",
}

ALL_LANGCHAIN_DEFS = LANGCHAIN_MESSAGE_TYPES | LANGCHAIN_SUPPORT_TYPES

_REF_PREFIX = "#/$defs/"


def _refs_langchain_messages(prop: dict) -> bool:
    if prop.get("type") != "array":
        return False
    items = prop.get("items", {})
    for candidate in items.get("oneOf") or items.get("anyOf") or []:
        ref = candidate.get("$ref", "")
        if (
            ref.startswith(_REF_PREFIX)
            and ref[len(_REF_PREFIX) :] in LANGCHAIN_MESSAGE_TYPES
        ):
            return True
    return False


def simplify_mcp_schema_inplace(schema: dict[str, Any]) -> None:
    """Simplify a LangGraph assistant input schema in-place for MCP/LLM consumption."""
    defs = schema.get("$defs")
    if not defs or not (set(defs) & LANGCHAIN_MESSAGE_TYPES):
        return

    for type_name in ALL_LANGCHAIN_DEFS:
        defs.pop(type_name, None)

    if not defs:
        del schema["$defs"]

    props = schema.get("properties", {})
    for key, prop in props.items():
        if _refs_langchain_messages(prop):
            props[key] = {
                "type": "string",
                "title": prop.get("title", key),
                "description": "The user message to send to the agent.",
            }


def normalize_name(name: str) -> str:
    """Convert an assistant name to a valid MCP tool name.

    MCP tool names must match ``^[a-zA-Z0-9_\\-.]{1,128}$``.
    """
    # Replace whitespace runs with a single underscore
    name = re.sub(r"\s+", "_", name.strip())
    # Remove any remaining invalid characters
    name = _MCP_INVALID_CHARS.sub("", name)
    # Truncate to max length
    name = name[:_MCP_TOOL_NAME_MAX_LENGTH]
    return name
