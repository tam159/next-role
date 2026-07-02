"""Custom encryption loading for LangGraph API.

This module provides functions to load and access custom encryption
instances defined by users in their langgraph.json configuration.
"""

from __future__ import annotations

import functools
import importlib.util
import sys
from typing import TYPE_CHECKING, Any, Literal, get_args

import structlog

from langgraph_api import timing
from langgraph_api.config import LANGGRAPH_ENCRYPTION
from langgraph_api.encryption.shared import (
    ENCRYPTION_CONTEXT_KEY,
    strip_encryption_metadata,
)
from langgraph_api.timing import profiled_import

if TYPE_CHECKING:
    from langgraph_sdk import Encryption
    from langgraph_sdk.encryption.types import JsonDecryptor, JsonEncryptor

    from langgraph_api.encryption.aes_json import AesEncryptionInstance

ModelType = Literal["run", "thread", "assistant", "cron", "checkpoint"]
SUPPORTED_ENCRYPTION_MODELS: frozenset[str] = frozenset(get_args(ModelType))

logger = structlog.stdlib.get_logger(__name__)


@functools.lru_cache(maxsize=1)
def get_custom_encryption_instance() -> Encryption | None:
    """Get the custom (SDK-injected) encryption instance if configured.

    Custom encryption is user-defined encryption logic loaded from a Python module
    specified in langgraph.json via LANGGRAPH_ENCRYPTION config.

    Returns:
        The Encryption instance if configured, or None if no custom encryption is configured.
    """
    if not LANGGRAPH_ENCRYPTION:
        return None
    logger.info(
        f"Getting custom encryption instance: {LANGGRAPH_ENCRYPTION}",
        langgraph_encryption=str(LANGGRAPH_ENCRYPTION),
    )
    path = LANGGRAPH_ENCRYPTION.get("path")
    if path is None:
        return None
    return _load_custom_encryption(path)


def _load_custom_encryption(path: str) -> Encryption:
    """Load a custom encryption instance from a module path.

    Args:
        path: Module path in format "./path/to/file.py:name" or "module:name"

    Returns:
        The custom Encryption instance.

    Raises:
        ValueError: If the path is invalid or the encryption instance is not found.
    """
    encryption_instance = _load_encryption_obj(path)
    logger.info(
        f"Loaded custom encryption instance from path {path}: {encryption_instance}"
    )
    return encryption_instance


@timing.timer(
    message="Loading custom encryption {encryption_path}",
    metadata_fn=lambda encryption_path: {"encryption_path": encryption_path},
    warn_threshold_secs=5,
    warn_message="Loading custom encryption '{encryption_path}' took longer than expected",
    error_threshold_secs=10,
)
def _load_encryption_obj(path: str) -> Encryption:
    """Load an Encryption object from a path string.

    Args:
        path: Module path in format "./path/to/file.py:name" or "module:name"

    Returns:
        The Encryption instance.

    Raises:
        ValueError: If the path is invalid or the encryption instance is not found.
        ImportError: If the module cannot be imported.
        FileNotFoundError: If the file cannot be found.
    """
    if ":" not in path:
        raise ValueError(
            f"Invalid encryption path format: {path}. "
            "Must be in format: './path/to/file.py:name' or 'module:name'"
        )

    module_name, callable_name = path.rsplit(":", 1)
    module_name = module_name.rstrip(":")

    if module_name.endswith(".js") or module_name.endswith(".mjs"):
        raise ValueError(
            f"JavaScript encryption is not supported. "
            f"Please use a Python module instead: {module_name}"
        )

    try:
        with profiled_import(path):
            if "/" in module_name or ".py" in module_name:
                modname = f"dynamic_module_{hash(module_name)}"
                modspec = importlib.util.spec_from_file_location(modname, module_name)
                if modspec is None or modspec.loader is None:
                    raise ValueError(f"Could not load file: {module_name}")
                module = importlib.util.module_from_spec(modspec)
                sys.modules[modname] = module
                modspec.loader.exec_module(module)
            else:
                module = importlib.import_module(module_name)

        loaded_encrypt = getattr(module, callable_name, None)
        if loaded_encrypt is None:
            raise ValueError(
                f"Could not find encrypt '{callable_name}' in module: {module_name}"
            )
        # Import Encryption at runtime only when needed (avoids requiring SDK 0.2.14)
        from langgraph_sdk import Encryption as EncryptionClass  # noqa: PLC0415

        if not isinstance(loaded_encrypt, EncryptionClass):
            raise ValueError(
                f"Expected an Encryption instance, got {type(loaded_encrypt)}"
            )

        return loaded_encrypt

    except ImportError as e:
        e.add_note(f"Could not import module:\n{module_name}\n\n")
        raise
    except FileNotFoundError as e:
        raise ValueError(f"Could not find file: {module_name}") from e


class JsonEncryptionWrapper:
    """Wrapper for JSON encryption that routes between custom and AES encryption.

    This wrapper handles dual-mode encryption routing:
    - Encrypts using custom (SDK-injected) encryption when configured
    - Decrypts using either custom OR AES based on the encryption context marker
    - Supports migration from AES-only to custom encryption (reads old AES data)

    Key responsibilities:
    - Key preservation validation (encryptor must not add/remove keys)
    - Encryption context storage (adds __encryption_context__ marker)
    - Migration routing (AES-encrypted data routes to AES decryptor)
    - Defensive checks (AES values in custom path raises error)
    """

    def __init__(
        self,
        custom_instance: Encryption,
        aes_instance: AesEncryptionInstance | None = None,
    ) -> None:
        """Initialize with custom encryption and optional AES for migration.

        Args:
            custom_instance: The SDK's Encryption instance (custom/user-defined encryption)
            aes_instance: Optional AES instance for migration (decrypting old AES data)
        """
        self._custom = custom_instance
        self._aes = aes_instance

    def get_json_encryptor(self, model_type: ModelType) -> JsonEncryptor | None:
        """Return an async encryptor that validates keys and adds context.

        The encryptor:
        1. Calls the custom encryptor
        2. Validates key preservation (no added/removed keys)
        3. Adds __encryption_context__ with the user's context

        Args:
            model_type: The type of model being encrypted

        Returns:
            Async encryptor function, or None if custom has no encryptor
        """
        from langgraph_api.encryption.aes_json import (  # noqa: PLC0415
            EncryptionKeyError,
        )

        custom_encryptor = self._custom.get_json_encryptor(model_type)
        if custom_encryptor is None:
            return None

        async def encryptor(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
            encrypted = await custom_encryptor(ctx, data)

            if not isinstance(encrypted, dict):
                raise EncryptionKeyError(
                    f"JSON encryptor must return a dict, got "
                    f"{type(encrypted).__name__}. Use per-key encryption "
                    f"(transform values, not keys) instead of envelope patterns "
                    f"that return a single encrypted token."
                )

            # Validate key preservation for SQL JSONB merge compatibility
            input_keys = set(data.keys())
            output_keys = set(encrypted.keys())
            added_keys = output_keys - input_keys
            removed_keys = input_keys - output_keys
            if added_keys or removed_keys:
                raise EncryptionKeyError(
                    f"JSON encryptor must preserve key structure for SQL JSONB merge compatibility. "
                    f"Added keys: {added_keys or 'none'}, removed keys: {removed_keys or 'none'}. "
                    f"Use per-key encryption (transform values, not keys) instead of envelope patterns."
                )

            # Add encryption context marker with user's context
            from langgraph_api.encryption.context import (  # noqa: PLC0415
                get_encryption_context,
            )

            encrypted[ENCRYPTION_CONTEXT_KEY] = (
                ctx.metadata if ctx and ctx.metadata else get_encryption_context()
            )

            return encrypted

        return encryptor

    def get_json_decryptor(self, model_type: ModelType) -> JsonDecryptor:
        """Return an async decryptor that routes based on encryption type.

        The decryptor routes based on __encryption_context__ marker:
        1. AES type marker → AES decryptor (migration path)
        2. Custom marker → custom decryptor (with defensive check)
        3. No marker → passthrough (plaintext)

        Args:
            model_type: The type of model being decrypted

        Returns:
            Async decryptor function (always returns a function for routing)
        """
        from langgraph_api.encryption.aes_json import (  # noqa: PLC0415
            DecryptorMissingError,
            EncryptionRoutingError,
            has_any_aes_encrypted_values,
            is_aes_encryption_context,
        )

        custom_decryptor = self._custom.get_json_decryptor(model_type)

        async def decryptor(ctx: Any, data: dict[str, Any]) -> dict[str, Any]:
            # No marker → plaintext passthrough
            if ENCRYPTION_CONTEXT_KEY not in data:
                return strip_encryption_metadata(data)

            context_dict = data[ENCRYPTION_CONTEXT_KEY]

            # AES type marker → route to AES decryptor (migration path)
            if is_aes_encryption_context(context_dict):
                if self._aes is None:
                    raise DecryptorMissingError(
                        f"Data has AES encryption marker but LANGGRAPH_AES_KEY is not configured "
                        f"for {model_type}"
                    )
                aes_decryptor = self._aes.get_json_decryptor(model_type)
                return await aes_decryptor(ctx, data)

            # Custom marker → use custom decryptor
            if custom_decryptor is None:
                raise DecryptorMissingError(
                    f"Data contains custom encryption marker but no decryptor is configured for {model_type}"
                )

            # Defensive check: ensure custom decryptor doesn't receive AES-encrypted values
            if has_any_aes_encrypted_values(data):
                raise EncryptionRoutingError(
                    f"Data has AES-encrypted values but is being routed to custom decryptor. "
                    f"This indicates a bug in encryption routing for {model_type}."
                )

            # Strip marker and decrypt
            data = strip_encryption_metadata(data)
            decrypted = await custom_decryptor(ctx, data)
            return strip_encryption_metadata(decrypted)

        return decryptor

    @property
    def has_aes(self) -> bool:
        return self._aes is not None

    @property
    def has_custom(self) -> bool:
        return self._custom is not None
