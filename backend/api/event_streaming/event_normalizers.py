from __future__ import annotations

from typing import Any


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def extract_text_content(value: Any) -> str | None:
    """Extract concatenated text from a message content payload."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif (
                _is_record(item)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                parts.append(item["text"])
        if parts:
            return "".join(parts)
    return None


def to_lifecycle_status(run_status: str) -> str:
    """Map a persisted run status to a protocol lifecycle status."""
    if run_status == "success":
        return "completed"
    if run_status == "error":
        return "failed"
    if run_status == "interrupted":
        return "interrupted"
    return "running"


def _as_update_values(value: Any) -> Any:
    if not _is_record(value):
        return {"value": value}
    return value


def normalize_updates_data(value: Any) -> dict[str, Any]:
    """Extract optional ``node`` and normalize ``values`` from an updates payload."""
    if _is_record(value):
        entries = list(value.items())
        if len(entries) == 1:
            node, node_values = entries[0]
            return {"node": node, "values": _as_update_values(node_values)}
    return {"values": _as_update_values(value)}


def _as_interrupt_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if _is_record(value) and isinstance(value.get("__interrupt__"), list):
        return value["__interrupt__"]
    return []


def normalize_input_requested_data(value: Any) -> list[dict[str, Any]]:
    """Extract interrupt requests from a value payload."""
    results: list[dict[str, Any]] = []
    for entry in _as_interrupt_array(value):
        if not _is_record(entry) or not isinstance(entry.get("id"), str):
            continue
        item: dict[str, Any] = {"interrupt_id": entry["id"]}
        if "value" in entry:
            item["payload"] = entry["value"]
        results.append(item)
    return results


def strip_interrupts_from_values(
    value: Any,
) -> tuple[list[dict[str, Any]], Any]:
    """Separate ``__interrupt__`` from a values payload.

    Returns ``(input_requests, cleaned_values)``.
    """
    input_requests = normalize_input_requested_data(value)
    if not _is_record(value) or "__interrupt__" not in value:
        return input_requests, value
    cleaned = {k: v for k, v in value.items() if k != "__interrupt__"}
    return input_requests, cleaned
