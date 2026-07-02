"""Shared utilities for configurable header filtering."""

import functools
import re
import urllib.parse
from collections.abc import Mapping

import orjson

LANGSMITH_METADATA = "langsmith-metadata"
LANGSMITH_TAGS = "langsmith-tags"
LANGSMITH_PROJECT = "langsmith-project"
# For security, don't include these in configuration
DEFAULT_RUN_HEADERS_EXCLUDE = {"x-api-key", "x-tenant-id", "x-service-key"}


def translate_pattern(pat: str) -> re.Pattern[str]:
    """Translate a pattern to regex, supporting only literals and * wildcards to avoid RE DoS."""
    res = []
    i = 0
    n = len(pat)

    while i < n:
        c = pat[i]
        i += 1

        if c == "*":
            res.append(".*")
        else:
            res.append(re.escape(c))

    pattern = "".join(res)
    return re.compile(rf"(?s:{pattern})\Z")


def get_configurable_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Extract headers that should be added to run configuration.

    This function handles special cases like langsmith-trace and baggage headers,
    while respecting the configurable header patterns.
    """
    configurable = {}
    if not headers:
        return configurable

    for key, value in headers.items():
        # First handle tracing stuff - always included regardless of patterns
        if key == "langsmith-trace":
            configurable[key] = value
            if baggage := headers.get("baggage"):
                for item in baggage.split(","):
                    if "=" not in item:
                        continue
                    baggage_key, baggage_value = item.split("=", 1)
                    if (
                        baggage_key == LANGSMITH_METADATA
                        and baggage_key not in configurable
                    ):
                        configurable[baggage_key] = orjson.loads(
                            urllib.parse.unquote(baggage_value)
                        )
                    elif baggage_key == LANGSMITH_TAGS:
                        configurable[baggage_key] = urllib.parse.unquote(
                            baggage_value
                        ).split(",")
                    elif baggage_key == LANGSMITH_PROJECT:
                        configurable[baggage_key] = urllib.parse.unquote(baggage_value)
            continue

        # Check if header should be included based on patterns
        # For run configuration, we have specific default behavior for x-* headers
        if key.startswith("x-"):
            # Check against default excludes for x-* headers
            if key in DEFAULT_RUN_HEADERS_EXCLUDE:
                # Check if explicitly included via patterns
                if should_include_header(key):
                    configurable[key] = value
                continue
            # Other x-* headers are included by default unless patterns exclude them
            if should_include_header(key):
                configurable[key] = value
        elif key == "user-agent":
            # user-agent is included by default unless excluded by patterns
            if should_include_header(key):
                configurable[key] = value
        else:
            # All other headers only included if patterns allow
            if should_include_header(key):
                configurable[key] = value

    return configurable


@functools.lru_cache(maxsize=1)
def get_header_patterns(
    key: str,
) -> tuple[list[re.Pattern[str]] | None, list[re.Pattern[str]] | None]:
    """Get the configured header include/exclude patterns."""
    from langgraph_api import config  # noqa: PLC0415

    if not config.HTTP_CONFIG:
        return None, None
    configurable = config.HTTP_CONFIG.get(key)
    if not configurable:
        return None, None
    header_includes = configurable.get("includes") or configurable.get("include") or []
    include_patterns = []
    for include in header_includes:
        include_patterns.append(translate_pattern(include))
    header_excludes = configurable.get("excludes") or configurable.get("exclude") or []
    exclude_patterns = []
    for exclude in header_excludes:
        exclude_patterns.append(translate_pattern(exclude))
    return include_patterns or None, exclude_patterns or None


@functools.lru_cache(maxsize=512)
def should_include_header(key: str) -> bool:
    """Check if a header should be included based on cached patterns.

    This function uses cached patterns from get_header_patterns() and
    provides efficient header filtering.

    Args:
        key: The header key to check

    Returns:
        True if the header should be included, False otherwise
    """
    if (
        key == "x-api-key"
        or key == "x-service-key"
        or key == "x-tenant-id"
        or key == "authorization"
    ):
        return False

    include_patterns, exclude_patterns = get_header_patterns("configurable_headers")

    return pattern_matches(key, include_patterns, exclude_patterns)


@functools.lru_cache(maxsize=512)
def should_include_header_in_logs(key: str) -> bool:
    """Check if header should be included in logs specifically."""

    include_patterns, exclude_patterns = get_header_patterns("logging_headers")

    return pattern_matches(key, include_patterns, exclude_patterns)


def pattern_matches(
    key: str,
    include_patterns: list[re.Pattern[str]] | None,
    exclude_patterns: list[re.Pattern[str]] | None,
) -> bool:
    # Handle configurable behavior
    if exclude_patterns and any(pattern.match(key) for pattern in exclude_patterns):
        return False
    if include_patterns:
        # If include patterns are specified, only include headers matching them
        return any(pattern.match(key) for pattern in include_patterns)

    # Default behavior - exclude
    return False
