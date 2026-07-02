"""Shared encryption constants and utilities.

This module contains constants and helper functions used by both
custom encryption (via SDK) and built-in AES encryption.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph_api.encryption.aes_json import AesEncryptionInstance
    from langgraph_api.encryption.custom import JsonEncryptionWrapper

# Stored alongside JSON-encrypted fields to record which encryption context was
# used.  Presence means sibling values are already encrypted.  Stripped before
# data reaches user code.
ENCRYPTION_CONTEXT_KEY = "__encryption_context__"

# Injected into run/cron payloads by the HTTP layer when JSON encryption is NOT
# configured but blob encryption is still needed.  Signals to the worker "encrypt
# blobs for this run" without implying the JSON siblings are encrypted.  Stripped
# before data reaches user code.
BLOB_ENCRYPTION_CONTEXT_KEY = "__blob_encryption_context__"

# Reserved keys that should never appear in user-facing responses
RESERVED_ENCRYPTION_KEYS = frozenset(
    {ENCRYPTION_CONTEXT_KEY, BLOB_ENCRYPTION_CONTEXT_KEY}
)


def strip_encryption_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Strip encryption-related keys from a data dict.

    Used during decryption to remove internal markers before returning
    data to callers.

    Args:
        data: Dict that may contain encryption marker keys

    Returns:
        New dict with marker keys removed
    """
    return {k: v for k, v in data.items() if k not in RESERVED_ENCRYPTION_KEYS}


@functools.lru_cache(maxsize=1)
def get_encryption() -> JsonEncryptionWrapper | AesEncryptionInstance | None:
    """Get the effective encryption instance for JSON encryption.

    Returns the cached encryption instance based on configuration:
    - Custom + AES configured: JsonEncryptionWrapper (handles migration)
    - AES only: AesEncryptionInstance
    - Neither: None
    """
    # Late import to avoid circular dependency
    from langgraph_api.encryption.aes_json import (  # noqa: PLC0415
        get_aes_encryption_instance,
    )
    from langgraph_api.encryption.custom import (  # noqa: PLC0415
        JsonEncryptionWrapper,
        get_custom_encryption_instance,
    )

    custom_instance = get_custom_encryption_instance()
    aes = get_aes_encryption_instance()

    if custom_instance:
        # Wrap custom encryption with AES migration support (can decrypt old AES data)
        return JsonEncryptionWrapper(custom_instance, aes)
    return aes


@functools.lru_cache(maxsize=1)
def using_custom_encryption() -> bool:
    """Check if custom encryption is configured.
    This is *not* mutually exclusive with AES encryption.

    Returns:
        True if custom encryption is configured, False otherwise.
    """
    from langgraph_api.encryption.custom import JsonEncryptionWrapper  # noqa: PLC0415

    return (
        isinstance(get_encryption(), JsonEncryptionWrapper)
        and get_encryption().has_custom
    )


@functools.lru_cache(maxsize=1)
def using_aes_encryption() -> bool:
    """Check if AES encryption is configured.
    This is *not* mutually exclusive with custom encryption.

    Returns:
        True if AES encryption is configured, False otherwise.
    """
    from langgraph_api.encryption.aes_json import AesEncryptionInstance  # noqa: PLC0415
    from langgraph_api.encryption.custom import JsonEncryptionWrapper  # noqa: PLC0415

    enc = get_encryption()
    return isinstance(enc, AesEncryptionInstance) or (
        isinstance(enc, JsonEncryptionWrapper) and enc.has_aes
    )
