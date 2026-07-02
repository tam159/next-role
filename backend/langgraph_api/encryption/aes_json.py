"""AES encryption for JSON field values.

This module provides opt-in AES encryption for specific JSON keys,
using the same key and cipher as checkpoint encryption (LANGGRAPH_AES_KEY).
"""

from __future__ import annotations

import base64
import functools
from typing import TYPE_CHECKING, Any

import orjson
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

from langgraph_api.encryption.shared import (
    ENCRYPTION_CONTEXT_KEY,
)

if TYPE_CHECKING:
    from langgraph.checkpoint.serde.base import CipherProtocol
    from langgraph_sdk.encryption.types import JsonDecryptor, JsonEncryptor

    from langgraph_api.encryption.custom import ModelType

AES_ENCRYPTED_PREFIX = "encrypted.aes:"

# Marker key to identify AES encryption in __encryption_context__
AES_ENCRYPTION_TYPE_KEY = "__langgraph_encryption_type__"
AES_ENCRYPTION_CONTEXT = {AES_ENCRYPTION_TYPE_KEY: "aes"}


def _get_aes_cipher(key: bytes) -> CipherProtocol:
    """Get AES cipher using the SDK's implementation (same as checkpoint encryption)."""
    # EncryptedSerializer.from_pycryptodome_aes creates a PycryptodomeAesCipher internally
    # We extract it to reuse the exact same encryption format as checkpoints
    return EncryptedSerializer.from_pycryptodome_aes(key=key).cipher


def is_aes_encrypted(value: Any) -> bool:
    """Check if a value is AES-encrypted (has the encryption prefix)."""
    return isinstance(value, str) and value.startswith(AES_ENCRYPTED_PREFIX)


def has_any_aes_encrypted_values(data: dict[str, Any]) -> bool:
    """Check if any value in the dict is AES-encrypted (top-level only)."""
    return isinstance(data, dict) and any(is_aes_encrypted(v) for v in data.values())


def is_aes_encryption_context(ctx: dict[str, Any] | None) -> bool:
    """Check if an encryption context indicates AES encryption."""
    return isinstance(ctx, dict) and ctx.get(AES_ENCRYPTION_TYPE_KEY) == "aes"


class EncryptionKeyError(Exception):
    """Raised when JSON encryptor violates key preservation constraint."""


class EncryptionRoutingError(Exception):
    """Raised when encryption routing fails due to inconsistent markers."""


class DecryptorMissingError(Exception):
    """Raised when data has encryption marker but no decryptor is configured."""


class AesEncryptionInstance:
    """Built-in AES encryption for JSON field values.

    Uses the same AES cipher as checkpoint encryption (via SDK's CipherProtocol).
    Duck-types the SDK's Encryption interface for use in the middleware.
    """

    def __init__(self, key: bytes, allowlist: frozenset[str]) -> None:
        self._cipher = _get_aes_cipher(key)
        self._allowlist = allowlist

    def encrypt_value(self, value: Any) -> str:
        """Encrypt a JSON-serializable value, returning prefixed ciphertext."""
        plaintext = orjson.dumps(value)
        _, encrypted_blob = self._cipher.encrypt(plaintext)
        encoded = base64.b64encode(encrypted_blob).decode("ascii")
        return f"{AES_ENCRYPTED_PREFIX}{encoded}"

    def decrypt_value(self, encrypted: str) -> Any:
        """Decrypt an AES-encrypted value."""
        if not encrypted.startswith(AES_ENCRYPTED_PREFIX):
            raise ValueError(f"Expected prefix '{AES_ENCRYPTED_PREFIX}'")
        encrypted_blob = base64.b64decode(encrypted[len(AES_ENCRYPTED_PREFIX) :])
        plaintext = self._cipher.decrypt("aes", encrypted_blob)
        return orjson.loads(plaintext)

    def encrypt_json(self, data: dict[str, Any]) -> dict[str, Any]:
        """Encrypt allowlisted keys in a JSON dict."""
        if not data:
            return data
        keys_to_encrypt = data.keys() & self._allowlist
        if not keys_to_encrypt:
            return data
        result = {
            k: self.encrypt_value(v) if k in keys_to_encrypt and v is not None else v
            for k, v in data.items()
        }
        result[ENCRYPTION_CONTEXT_KEY] = AES_ENCRYPTION_CONTEXT.copy()
        return result

    def decrypt_json(self, data: dict[str, Any]) -> dict[str, Any]:
        """Decrypt all AES-encrypted values in a JSON dict."""
        if not data:
            return data
        return {
            k: self.decrypt_value(v) if is_aes_encrypted(v) else v
            for k, v in data.items()
            if k != ENCRYPTION_CONTEXT_KEY
        }

    def get_json_encryptor(self, model_type: ModelType) -> JsonEncryptor:
        """Return an async encryptor function for the given model type."""

        async def encryptor(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
            return self.encrypt_json(data)

        return encryptor

    def get_json_decryptor(self, model_type: ModelType) -> JsonDecryptor:
        """Return an async decryptor function for the given model type."""

        async def decryptor(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
            return self.decrypt_json(data)

        return decryptor


@functools.lru_cache(maxsize=1)
def get_aes_encryption_instance() -> AesEncryptionInstance | None:
    """Get the AES encryption instance if configured.

    Returns:
        - AesEncryptionInstance with allowlist if both LANGGRAPH_AES_KEY and
          LANGGRAPH_AES_JSON_KEYS are configured (encrypts and decrypts)
        - AesEncryptionInstance with empty allowlist if only LANGGRAPH_AES_KEY
          is configured (decrypts only, for migration from AES to custom)
        - None if LANGGRAPH_AES_KEY is not configured
    """
    # Import here to avoid circular imports
    from langgraph_api.config import (  # noqa: PLC0415
        LANGGRAPH_AES_JSON_KEYS,
        LANGGRAPH_AES_KEY,
    )

    if not LANGGRAPH_AES_KEY:
        return None

    # If both key and json_keys are set, use full encryption/decryption
    if LANGGRAPH_AES_JSON_KEYS:
        return AesEncryptionInstance(LANGGRAPH_AES_KEY, LANGGRAPH_AES_JSON_KEYS)

    # If only key is set (no json_keys), create decryption-only instance.
    # This supports migration from AES to custom encryption: the server can
    # decrypt old AES-encrypted data without re-encrypting new data with AES.
    return AesEncryptionInstance(LANGGRAPH_AES_KEY, frozenset())
