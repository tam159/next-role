"""Shared extraction utilities for thread search.

Used by the API layer (threads.py), inmem runtime (ops.py), and gRPC ops
(grpc/ops/threads.py) to extract values from thread dicts using dot/bracket
path syntax.
"""

from __future__ import annotations

import re
from typing import Any

from starlette.exceptions import HTTPException

from langgraph_api.schema import THREAD_FIELDS

# Columns that support extraction path syntax.
EXTRACTABLE_COLUMNS = frozenset({"values", "metadata", "config", "interrupts"})

# Maximum number of extraction paths allowed per request.
MAX_EXTRACT_PATHS = 10

# Regex for validating extract alias keys (must be a safe identifier).
_ALIAS_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_SPLIT_RE = re.compile(r"\.|\[")


def validate_extract_alias(alias: str) -> bool:
    """Return True if *alias* is a valid extract key (safe identifier)."""
    return bool(_ALIAS_RE.match(alias))


def validate_extract(extract: dict[str, str]) -> dict[str, str]:
    """Validate the extract parameter for thread search.

    Raises HTTPException(422) on validation errors.
    Returns the validated extract dict.
    """
    if len(extract) > MAX_EXTRACT_PATHS:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum of {MAX_EXTRACT_PATHS} extract paths allowed, got {len(extract)}",
        )

    reserved_keys = extract.keys() & THREAD_FIELDS
    if reserved_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Extract keys cannot use reserved field names: {sorted(reserved_keys)}",
        )

    for alias, path in extract.items():
        if not validate_extract_alias(alias):
            raise HTTPException(
                status_code=422,
                detail=f"Extract key '{alias}' must be a valid identifier (letters, digits, underscores)",
            )
        if not path or not isinstance(path, str):
            raise HTTPException(
                status_code=422,
                detail=f"Extract path for '{alias}' must be a non-empty string",
            )
        root = path.split(".")[0].split("[")[0]
        if root not in EXTRACTABLE_COLUMNS:
            raise HTTPException(
                status_code=422,
                detail=f"Extract path '{path}' must start with one of: {', '.join(sorted(EXTRACTABLE_COLUMNS))}",
            )

    return extract


def extract_path_value(data: dict[str, Any], path: str) -> Any:
    """Extract a value from a thread dict using a dot/bracket path.

    Path format: ``column.key1.key2[index]``
    Example: ``values.messages[-1].content``

    Handles both dict-like access and attribute access for non-dict objects
    (e.g. LangGraph Message objects stored in the inmem runtime).

    Returns ``None`` if the path doesn't resolve.
    """
    parts = _SPLIT_RE.split(path)
    if not parts:
        return None

    root = parts[0]
    if root not in EXTRACTABLE_COLUMNS:
        return None

    current = data.get(root)
    for part in parts[1:]:
        if current is None:
            return None
        if not part:
            continue
        # Reject private/dunder attribute access
        if part.startswith("_"):
            return None
        if part.endswith("]"):
            idx_str = part.rstrip("]")
            try:
                idx = int(idx_str)
            except ValueError:
                return None
            if isinstance(current, (list, tuple)):
                try:
                    current = current[idx]
                except IndexError:
                    return None
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(part)
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
    return current
