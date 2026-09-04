"""Extraction-time shaping — the "structure and metrics, never document bodies" gate.

Every row that leaves the operational Postgres for the warehouse passes through
one of these pure functions. They are the enforcement point for the analytics
blueprint's privacy doctrine: message/CV/JD text, run input payloads, store
values, and checkpoint channel state must never be yielded to the destination.
The unit tests in ``tests/test_transforms.py`` and ``tests/test_message_explode.py``
pin that contract — extend them with any new field before shipping it.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Run/thread metadata keys that may ship to the warehouse. Anything else in
#: the JSONB (arbitrary client-supplied metadata) is dropped wholesale.
_CHECKPOINT_META_KEYS = ("source", "step", "graph_id", "assistant_id", "owner", "user_id")

#: The two model-override slots the runtime reads from ``config.configurable``
#: (see backend/agents/career_agent/middleware.py `_read_config`).
_MODEL_OVERRIDE_KEYS = ("main_agent_model", "subagent_model")

_USER_AGENT_MAX_LEN = 256


def hash_email(email: str | None) -> tuple[str | None, str | None]:
    """Reduce an email address to ``(sha256_hex, domain)``.

    The hash keeps joins/dedup possible without storing the address; the domain
    is retained as a quasi-identifier for cohort analysis (PII-tagged in dbt).
    """
    if not email:
        return None, None
    normalized = email.strip().lower()
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    domain = normalized.rsplit("@", 1)[1] if "@" in normalized else None
    return digest, domain


def truncate_ip(ip: str | None) -> str | None:
    """Truncate an IPv4 address to its /24 prefix; anything else becomes None.

    IPv6 (and malformed values) are dropped rather than guessed at — the
    warehouse only ever sees a coarse network prefix.
    """
    if not ip:
        return None
    parts = ip.strip().split(".")
    if len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts):  # noqa: PLR2004
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    return None


def extract_model_overrides(configurable: dict[str, Any] | None) -> dict[str, str | None]:
    """Pick the model-override slots out of a run's ``config.configurable``.

    Only scalar string values of the known override keys are kept — the rest of
    the configurable (which can carry arbitrary payload) never leaves this
    function.
    """
    configurable = configurable or {}
    overrides: dict[str, str | None] = {}
    for key in _MODEL_OVERRIDE_KEYS:
        value = configurable.get(key)
        overrides[key] = value if isinstance(value, str) and value else None
    return overrides


def shape_run(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a ``run`` row: keep ids/timestamps/status, drop metadata + kwargs.

    ``kwargs`` contains the full run input (user messages) — only the two
    model-override strings survive; ``metadata`` contributes only ``owner``.
    """
    metadata = row.pop("metadata", None) or {}
    kwargs = row.pop("kwargs", None) or {}
    configurable = (kwargs.get("config") or {}).get("configurable") or {}
    overrides = extract_model_overrides(configurable)
    row["owner"] = metadata.get("owner")
    row["main_model_override"] = overrides["main_agent_model"]
    row["subagent_model_override"] = overrides["subagent_model"]
    return row


def shape_thread(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a ``thread`` row: identity + status + counts, never the values body."""
    metadata = row.pop("metadata", None) or {}
    values = row.pop("values", None) or {}
    interrupts = row.pop("interrupts", None) or {}
    messages = values.get("messages") or []
    files = values.get("files") or {}
    todos = values.get("todos") or []
    row["owner"] = metadata.get("owner")
    row["graph_id"] = metadata.get("graph_id")
    row["assistant_id"] = metadata.get("assistant_id")
    row["message_count"] = len(messages)
    row["file_count"] = len(files)
    row["todo_count"] = len(todos)
    row["has_interrupt"] = bool(interrupts)
    return row


def _content_length(content: Any) -> int:  # noqa: ANN401
    """Character length of a message's content (str, or list of text parts)."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += len(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    total += len(text)
        return total
    return 0


def shape_message(thread_id: str, index: int, message: dict[str, Any]) -> dict[str, Any]:
    """Reduce one thread message to its metrics row — the content never ships.

    Keeps type/name/model/token accounting (``usage_metadata`` is present on
    every AI message) plus tool-call *names*; drops content, tool-call args,
    and everything else.
    """
    usage = message.get("usage_metadata") or {}
    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}
    response_meta = message.get("response_metadata") or {}
    tool_calls = message.get("tool_calls") or []
    tool_call_names = sorted(
        {tc.get("name") for tc in tool_calls if isinstance(tc, dict) and tc.get("name")},
    )
    message_type = message.get("type")
    return {
        "thread_id": thread_id,
        "message_index": index,
        "message_id": message.get("id"),
        "type": message_type,
        "name": message.get("name"),
        "model_name": response_meta.get("model_name"),
        "model_provider": response_meta.get("model_provider"),
        "finish_reason": response_meta.get("finish_reason"),
        "content_length": _content_length(message.get("content")),
        "tool_call_count": len(tool_calls),
        "tool_call_names": ",".join(tool_call_names) or None,
        "tool_call_id": message.get("tool_call_id"),
        "tool_status": message.get("status") if message_type == "tool" else None,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cache_creation_tokens": input_details.get("cache_creation"),
        "cache_read_tokens": input_details.get("cache_read"),
        "reasoning_tokens": output_details.get("reasoning"),
    }


def explode_messages(thread_id: str, messages: Any) -> Iterator[dict[str, Any]]:  # noqa: ANN401
    """Yield one metrics row per message in a thread's ``values.messages`` list."""
    if not isinstance(messages, list):
        return
    for index, message in enumerate(messages):
        if isinstance(message, dict):
            yield shape_message(thread_id, index, message)


def shape_checkpoint_meta(row: dict[str, Any]) -> dict[str, Any]:
    """Keep only the whitelisted checkpoint metadata keys (agent telemetry)."""
    metadata = row.pop("metadata", None) or {}
    for key in _CHECKPOINT_META_KEYS:
        row[key] = metadata.get(key)
    return row


def shape_user(row: dict[str, Any]) -> dict[str, Any]:
    """Pseudonymize a Better Auth ``user`` row: email → hash + domain."""
    email_hash, email_domain = hash_email(row.pop("email", None))
    row["email_hash"] = email_hash
    row["email_domain"] = email_domain
    return row


def shape_session(row: dict[str, Any]) -> dict[str, Any]:
    """Coarsen a Better Auth ``session`` row: IP → /24, user agent truncated."""
    row["ip_prefix"] = truncate_ip(row.pop("ipAddress", None))
    user_agent = row.pop("userAgent", None)
    row["user_agent"] = user_agent[:_USER_AGENT_MAX_LEN] if isinstance(user_agent, str) else None
    return row
