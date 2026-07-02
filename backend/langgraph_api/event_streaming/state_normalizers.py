from __future__ import annotations

import json
from typing import Any

_PROTOCOL_STATE_MESSAGE_TYPES = frozenset(
    {"human", "user", "ai", "assistant", "system", "tool", "function", "remove"}
)

_MIME_TYPE_BY_AUDIO_FORMAT: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pcm16": "audio/wav",
    "pcm": "audio/wav",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
}

_PROTOCOL_CONTENT_BLOCK_TYPES = frozenset(
    {
        "text",
        "reasoning",
        "tool_call",
        "tool_call_chunk",
        "invalid_tool_call",
        "server_tool_call",
        "server_tool_call_chunk",
        "server_tool_call_result",
        "image",
        "audio",
        "video",
        "file",
        "non_standard",
    }
)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


# ---------------------------------------------------------------------------
# Tool call helpers — shared by ``normalize_event_streaming_state_payload`` for
# ``values`` / ``updates`` message normalization.
# ---------------------------------------------------------------------------


def _get_tool_call_identity(
    value: dict[str, Any],
) -> dict[str, str | None]:
    nested_fn = value.get("function") if _is_record(value.get("function")) else None
    return {
        "id": value["id"] if isinstance(value.get("id"), str) else None,
        "name": (
            value["name"]
            if isinstance(value.get("name"), str)
            else (
                nested_fn["name"]
                if nested_fn and isinstance(nested_fn.get("name"), str)
                else None
            )
        ),
    }


def _get_tool_call_args(value: dict[str, Any]) -> Any:
    if "args" in value:
        return value["args"]
    nested_fn = value.get("function") if _is_record(value.get("function")) else None
    return nested_fn.get("arguments") if nested_fn else None


def _normalize_final_tool_call_args(value: Any) -> dict[str, Any]:
    if _is_record(value):
        return {"valid": True, "args": value}
    if isinstance(value, str):
        if not value:
            return {"valid": True, "args": {}}
        try:
            return {"valid": True, "args": json.loads(value)}
        except (json.JSONDecodeError, ValueError):
            return {"valid": False, "args": value}
    if value is None:
        return {"valid": True, "args": {}}
    return {"valid": True, "args": value}


# ---------------------------------------------------------------------------
# Content block normalization
# ---------------------------------------------------------------------------


def _normalize_audio_block_from_additional_kwargs(
    additional_kwargs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if additional_kwargs is None:
        return None
    audio = additional_kwargs.get("audio")
    if not _is_record(audio):
        return None
    data = audio.get("data") if isinstance(audio.get("data"), str) else None
    url = audio.get("url") if isinstance(audio.get("url"), str) else None
    if data is None and url is None:
        return None

    fmt = (
        audio["format"].lower()
        if isinstance(audio.get("format"), str)
        else (None if isinstance(audio.get("mime_type"), str) else "wav")
    )
    result: dict[str, Any] = {"type": "audio"}
    if isinstance(audio.get("id"), str):
        result["id"] = audio["id"]
    if url is not None:
        result["url"] = url
    if data is not None:
        result["data"] = data
    if isinstance(audio.get("mime_type"), str):
        result["mime_type"] = audio["mime_type"]
    elif fmt is not None and fmt in _MIME_TYPE_BY_AUDIO_FORMAT:
        result["mime_type"] = _MIME_TYPE_BY_AUDIO_FORMAT[fmt]
    if isinstance(audio.get("transcript"), str):
        result["transcript"] = audio["transcript"]
    return result


def normalize_event_streaming_content_block(value: Any) -> dict[str, Any] | None:
    """Normalize a raw content block into a protocol content block."""
    if not _is_record(value) or not isinstance(value.get("type"), str):
        return None
    if value["type"] in _PROTOCOL_CONTENT_BLOCK_TYPES:
        return value

    if value["type"] == "image_url":
        raw_image = value.get("image_url")
        if isinstance(raw_image, str):
            return {"type": "image", "url": raw_image}
        if _is_record(raw_image) and isinstance(raw_image.get("url"), str):
            return {"type": "image", "url": raw_image["url"]}
        return None

    if value["type"] == "input_audio":
        raw_audio = (
            value.get("input_audio") if _is_record(value.get("input_audio")) else None
        )
        if raw_audio is None:
            return None
        result: dict[str, Any] = {"type": "audio"}
        if isinstance(raw_audio.get("data"), str):
            result["data"] = raw_audio["data"]
        if isinstance(raw_audio.get("mime_type"), str):
            result["mime_type"] = raw_audio["mime_type"]
        return result

    return {"type": "non_standard", "value": {**value}}


def normalize_event_streaming_finalized_content_block(
    value: Any,
) -> dict[str, Any] | None:
    """Normalize a content block, excluding chunk types."""
    block = normalize_event_streaming_content_block(value)
    if block is None:
        return None
    if block.get("type") in ("tool_call_chunk", "server_tool_call_chunk"):
        return None
    return block


def normalize_event_streaming_message_content(
    content: Any,
    additional_kwargs: dict[str, Any] | None = None,
) -> Any:
    """Normalize message content into protocol content blocks."""
    if isinstance(content, str):
        audio_block = _normalize_audio_block_from_additional_kwargs(additional_kwargs)
        if audio_block is None:
            return content
        blocks: list[dict[str, Any]] = []
        if content:
            blocks.append({"type": "text", "text": content})
        blocks.append(audio_block)
        return blocks

    if not isinstance(content, list):
        audio_block = _normalize_audio_block_from_additional_kwargs(additional_kwargs)
        return [audio_block] if audio_block is not None else content

    blocks = []
    for entry in content:
        if isinstance(entry, str):
            blocks.append({"type": "text", "text": entry})
            continue
        normalized = normalize_event_streaming_content_block(entry)
        if normalized is not None:
            blocks.append(normalized)

    audio_block = _normalize_audio_block_from_additional_kwargs(additional_kwargs)
    if audio_block is not None and not any(b.get("type") == "audio" for b in blocks):
        blocks.append(audio_block)

    return blocks if blocks else content


# ---------------------------------------------------------------------------
# State message normalization (``values`` / ``updates`` payloads)
# ---------------------------------------------------------------------------


def _normalize_state_message_type(value: Any) -> str | None:
    if value == "assistant":
        return "ai"
    if value == "user":
        return "human"
    return value if isinstance(value, str) else None


def _is_event_streaming_state_message(value: Any) -> bool:
    if not _is_record(value):
        return False
    normalized_type = _normalize_state_message_type(value.get("type"))
    return (
        normalized_type is not None and normalized_type in _PROTOCOL_STATE_MESSAGE_TYPES
    )


def _normalize_state_invalid_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in value:
        if not _is_record(raw):
            continue
        identity = _get_tool_call_identity(raw)
        entry: dict[str, Any] = {"type": "invalid_tool_call"}
        if identity["id"] is not None:
            entry["id"] = identity["id"]
        if identity["name"] is not None:
            entry["name"] = identity["name"]
        if isinstance(raw.get("args"), str):
            entry["args"] = raw["args"]
        entry["error"] = (
            raw["error"] if isinstance(raw.get("error"), str) else "Malformed args."
        )
        result.append(entry)
    return result


def _normalize_state_tool_calls(
    value: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(value, list):
        return [], []
    tool_calls: list[dict[str, Any]] = []
    invalid_tool_calls: list[dict[str, Any]] = []
    for raw in value:
        if not _is_record(raw):
            continue
        identity = _get_tool_call_identity(raw)
        raw_args = _get_tool_call_args(raw)
        normalized_args = _normalize_final_tool_call_args(raw_args)

        if identity["name"] is None:
            entry: dict[str, Any] = {
                "type": "invalid_tool_call",
                "error": "Incomplete tool call.",
            }
            if identity["id"] is not None:
                entry["id"] = identity["id"]
            if isinstance(raw_args, str):
                entry["args"] = raw_args
            invalid_tool_calls.append(entry)
            continue
        if not normalized_args["valid"]:
            entry = {
                "type": "invalid_tool_call",
                "name": identity["name"],
                "error": "Malformed args.",
            }
            if identity["id"] is not None:
                entry["id"] = identity["id"]
            if isinstance(normalized_args["args"], str):
                entry["args"] = normalized_args["args"]
            invalid_tool_calls.append(entry)
            continue
        tc: dict[str, Any] = {
            "type": "tool_call",
            "name": identity["name"],
            "args": normalized_args["args"],
        }
        if identity["id"] is not None:
            tc["id"] = identity["id"]
        tool_calls.append(tc)
    return tool_calls, invalid_tool_calls


def _normalize_state_message(value: dict[str, Any]) -> dict[str, Any]:
    msg_type = _normalize_state_message_type(value.get("type"))
    if msg_type is None:
        return value

    additional_kwargs = (
        value["additional_kwargs"]
        if _is_record(value.get("additional_kwargs"))
        else None
    )

    message: dict[str, Any] = {
        "type": msg_type,
        "content": normalize_event_streaming_message_content(
            value.get("content", ""),
            additional_kwargs=additional_kwargs if msg_type == "ai" else None,
        ),
    }
    if isinstance(value.get("id"), str):
        message["id"] = value["id"]
    if isinstance(value.get("name"), str):
        message["name"] = value["name"]
    if msg_type in ("ai", "human") and isinstance(value.get("example"), bool):
        message["example"] = value["example"]

    if msg_type == "tool":
        if isinstance(value.get("tool_call_id"), str):
            message["tool_call_id"] = value["tool_call_id"]
        if value.get("status") in ("success", "error"):
            message["status"] = value["status"]
        if "artifact" in value:
            message["artifact"] = value["artifact"]

    if msg_type == "ai":
        raw_tool_calls = (
            value["tool_calls"]
            if isinstance(value.get("tool_calls"), list) and value["tool_calls"]
            else (
                additional_kwargs.get("tool_calls")
                if additional_kwargs
                and isinstance(additional_kwargs.get("tool_calls"), list)
                else None
            )
        )
        tool_calls, invalid_from_valid = _normalize_state_tool_calls(raw_tool_calls)
        invalid_tool_calls = (
            _normalize_state_invalid_tool_calls(value["invalid_tool_calls"])
            if isinstance(value.get("invalid_tool_calls"), list)
            and value["invalid_tool_calls"]
            else invalid_from_valid
        )
        if tool_calls:
            message["tool_calls"] = tool_calls
        if invalid_tool_calls:
            message["invalid_tool_calls"] = invalid_tool_calls

    return message


def normalize_event_streaming_state_payload(value: Any) -> Any:
    """Recursively normalize a protocol state payload.

    Messages are normalized into protocol shapes; ``__interrupt__`` keys
    are stripped.
    """
    if isinstance(value, list):
        return [
            _normalize_state_message(item)
            if _is_event_streaming_state_message(item)
            else normalize_event_streaming_state_payload(item)
            for item in value
        ]
    if not _is_record(value):
        return value
    normalized: dict[str, Any] = {}
    for key, entry in value.items():
        if key == "__interrupt__":
            continue
        if key == "messages" and isinstance(entry, list):
            normalized[key] = [
                _normalize_state_message(item)
                if _is_event_streaming_state_message(item)
                else item
                for item in entry
            ]
            continue
        normalized[key] = normalize_event_streaming_state_payload(entry)
    return normalized
