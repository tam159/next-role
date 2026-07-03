from __future__ import annotations


def to_namespace_key(namespace: list[str]) -> str:
    """Convert a namespace array into a stable internal lookup key."""
    return "\0".join(namespace)


def normalize_namespace_segment(segment: str) -> str:
    """Strip dynamic suffixes (after ``:``) from a namespace segment."""
    return segment.split(":")[0]


def parse_event_name(event: str) -> tuple[str, list[str]]:
    """Split a stream event name into method and namespace components.

    Returns ``(method, namespace_segments)`` where namespace may be empty.
    """
    parts = event.split("|")
    return parts[0], parts[1:]


def is_prefix_match(namespace: list[str], prefix: list[str]) -> bool:
    """Check whether *namespace* starts with *prefix*.

    Segments are compared literally first; if the prefix segment does not
    contain ``:``, the candidate segment is also compared after stripping
    its dynamic suffix.
    """
    if len(prefix) > len(namespace):
        return False
    for i, segment in enumerate(prefix):
        candidate = namespace[i]
        if candidate == segment:
            continue
        if ":" in segment:
            return False
        if normalize_namespace_segment(candidate) == segment:
            continue
        return False
    return True


def guess_graph_name(namespace: list[str]) -> str:
    """Derive a human-readable graph name from a namespace."""
    if not namespace:
        return "root"
    return normalize_namespace_segment(namespace[-1])
