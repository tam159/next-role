"""Request-scoped encryption context storage.

This module provides a ContextVar for storing encryption context
(tenant ID, key identifiers, etc.) that is accessible throughout
the async request lifecycle, including in checkpoint serialization.
"""

from contextvars import ContextVar
from typing import Any

# Request-scoped encryption context
# Set by API middleware when X-Encryption-Context header is present
# Accessed by serializers during checkpoint encryption/decryption
encryption_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "encryption_context", default=None
)


def get_encryption_context() -> dict[str, Any]:
    """Get the current request's encryption context.

    Returns:
        The encryption context dict, or empty dict if not set.
    """
    ctx = encryption_context.get()
    return ctx if ctx is not None else {}


def set_encryption_context(context: dict[str, Any]) -> None:
    """Set the encryption context for the current request.

    Args:
        context: The encryption context dict (e.g., {"tenant_id": "...", "key_id": "..."})
    """
    encryption_context.set(context)
