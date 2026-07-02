"""Encryption/decryption middleware for API layer.

This module provides helpers to encrypt data before storing and decrypt
after retrieving, keeping encryption logic at the API layer.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any, cast

import orjson
import structlog
from starlette.authentication import BaseUser
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request  # noqa: TC002

from langgraph_api.auth.noop import UnauthenticatedUser
from langgraph_api.config import LANGGRAPH_ENCRYPTION
from langgraph_api.encryption.aes_json import (
    AesEncryptionInstance,
    DecryptorMissingError,
    is_aes_encryption_context,
)
from langgraph_api.encryption.context import (
    get_encryption_context,
    set_encryption_context,
)
from langgraph_api.encryption.custom import (
    JsonEncryptionWrapper,
    ModelType,
    get_custom_encryption_instance,
)
from langgraph_api.encryption.shared import (
    BLOB_ENCRYPTION_CONTEXT_KEY,
    ENCRYPTION_CONTEXT_KEY,
    get_encryption,
    strip_encryption_metadata,
)
from langgraph_api.schema import (
    NESTED_ENCRYPTED_SUBFIELDS,
    NEVER_ENCRYPT_FIELDS_GLOBAL,
    NEVER_ENCRYPT_PATHS,
)
from langgraph_api.serde import Fragment, json_loads

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Only import EncryptionContext at module load if encryption is configured
# This avoids requiring langgraph-sdk>=0.2.14 for users who don't use encryption
if LANGGRAPH_ENCRYPTION:
    from langgraph_sdk import EncryptionContext

logger = structlog.stdlib.get_logger(__name__)


def _serialize_user_for_encryption(user: BaseUser) -> dict[str, Any]:
    """Serialize a BaseUser to a JSON-serializable dict for encryption.

    Called by _prepare_data_for_encryption when langgraph_auth_user contains a
    BaseUser that needs to be serialized before JSON encryption.

    Args:
        user: The BaseUser to serialize (ProxyUser, SimpleUser, or custom subclass)

    Returns:
        A JSON-serializable dict with user data
    """
    # If the auth function returns a pydantic object as the User object, we
    # want to preserve the additional fields
    if hasattr(user, "model_dump") and callable(user.model_dump):
        return cast("dict[str, Any]", user.model_dump())

    # Plain BaseUser subclasses - extract the required properties
    return {
        "identity": user.identity,
        "is_authenticated": user.is_authenticated,
        "display_name": user.display_name,
    }


def _prepare_data_for_encryption(data: dict[str, Any]) -> dict[str, Any]:
    """Prepare data dict for encryption by serializing non-JSON-serializable objects.

    Specifically handles langgraph_auth_user which may contain BaseUser objects
    that can't be JSON-serialized. Dicts pass through unchanged (already serializable).

    Args:
        data: The data dict to prepare

    Returns:
        A new dict with serialized values where needed
    """
    if "langgraph_auth_user" not in data:
        return data

    user = data["langgraph_auth_user"]
    if isinstance(user, BaseUser):
        data = dict(data)  # shallow copy
        if isinstance(user, UnauthenticatedUser):
            data["langgraph_auth_user"] = None
        else:
            data["langgraph_auth_user"] = _serialize_user_for_encryption(user)

    return data


def _should_skip_encryption(key: str, path: str) -> bool:
    """Check if a field should be excluded from encryption at this path.

    Args:
        key: The field name to check
        path: The current path context (e.g., "run.kwargs.config.configurable")

    Returns:
        True if the field should be excluded from encryption
    """
    if key in NEVER_ENCRYPT_FIELDS_GLOBAL:
        return True
    return f"{path}.{key}" in NEVER_ENCRYPT_PATHS


def _extract_skip_fields(
    data: Mapping[str, Any], path: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract fields that should be skipped from encryption.

    Args:
        data: The data dict to process
        path: The current path context (e.g., "run.kwargs.config.configurable")

    Returns:
        Tuple of (data_to_encrypt, skipped_fields)
        - data_to_encrypt: dict with skipped fields removed
        - skipped_fields: dict of field_name -> value for skipped fields
    """
    skipped: dict[str, Any] = {}
    to_encrypt: dict[str, Any] = {}

    for key, value in data.items():
        if _should_skip_encryption(key, path):
            skipped[key] = value
        else:
            to_encrypt[key] = value

    return to_encrypt, skipped


def extract_encryption_context(request: Request) -> dict[str, Any]:
    """Extract encryption context from X-Encryption-Context header.

    Args:
        request: The Starlette request object

    Returns:
        Encryption context dict, or empty dict if header not present

    Raises:
        HTTPException: 422 if header is present but malformed
    """
    header_value = request.headers.get("X-Encryption-Context")
    if not header_value:
        return {}

    try:
        decoded = base64.b64decode(header_value.encode())
        context = orjson.loads(decoded)
        if not isinstance(context, dict):
            raise HTTPException(
                status_code=422,
                detail="Invalid X-Encryption-Context header: expected base64-encoded JSON object",
            )
        return context
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid X-Encryption-Context header: {e}",
        ) from e


class EncryptionContextMiddleware(BaseHTTPMiddleware):
    """Middleware to extract and set encryption context from request headers.

    If a @encryption.context handler is registered, it is called after extracting
    the initial context from the X-Encryption-Context header. The handler receives
    the authenticated user and can derive encryption context from auth (e.g., JWT claims).
    """

    async def dispatch(self, request: Request, call_next):
        context_dict = extract_encryption_context(request)

        # Call context handler if registered (to derive context from auth)
        encryption_instance = get_custom_encryption_instance()
        if encryption_instance and encryption_instance._context_handler:
            user = request.scope.get("user")
            if user:
                initial_ctx = EncryptionContext(
                    model=None, field=None, metadata=context_dict
                )
                try:
                    context_dict = await encryption_instance._context_handler(
                        user, initial_ctx
                    )
                except Exception as e:
                    await logger.aexception(
                        "Error in encryption context handler", exc_info=e
                    )

        set_encryption_context(context_dict)
        request.state.encryption_context = context_dict
        response = await call_next(request)
        return response


class DoubleEncryptionError(Exception):
    """Raised when attempting to encrypt data that is already encrypted.

    This typically indicates a bug where encrypted data is being passed through
    the encryption pipeline again, which would corrupt the data.
    """


async def encrypt_json_if_needed(
    data: dict[str, Any] | None,
    encryption_instance: JsonEncryptionWrapper | AesEncryptionInstance | None,
    model_type: ModelType,
    field: str | None = None,
    path: str | None = None,
) -> dict[str, Any] | None:
    """Encrypt JSON data dict if encryption is configured.

    Uses a unified interface where both custom (via wrapper) and AES encryption
    implement the same encryptor interface (get_json_encryptor). The wrapper
    handles key validation and context storage internally.

    For custom encryption only, certain system metadata fields are excluded from
    encryption (see NEVER_ENCRYPT_FIELDS_GLOBAL and NEVER_ENCRYPT_PATHS). Fields
    are extracted before encryption and merged back after, preserving plaintext.
    AES encryption uses an explicit allowlist, so it doesn't need this exclusion logic.

    Args:
        data: The plaintext data dict
        encryption_instance: The encryption instance (wrapped custom, AES, or None)
        model_type: The type of model (e.g., "thread", "assistant", "run")
        field: The specific field being encrypted (e.g., "metadata", "context")
        path: The full dot-separated path for path-based skip rules
              (e.g., "run.kwargs.config.configurable")

    Returns:
        Encrypted data dict with stored context, or original if no encryption configured

    Raises:
        EncryptionKeyError: If the encryptor adds or removes keys (violates key preservation)
        DoubleEncryptionError: If data already has encryption context marker (already encrypted)
    """
    if data is None:
        return data

    # Early return if no encryption configured (avoid unnecessary context lookups)
    if encryption_instance is None:
        return data

    # Safety check: detect if data is already encrypted to prevent double encryption.
    # Both AES and custom encryption use __encryption_context__ marker.
    if ENCRYPTION_CONTEXT_KEY in data:
        raise DoubleEncryptionError(
            f"Attempted to encrypt data that is already encrypted (has {ENCRYPTION_CONTEXT_KEY}). "
            f"model_type={model_type}, field={field}. "
            f"This indicates a bug where encrypted data is being re-encrypted. "
            f"Ensure data is decrypted before re-encrypting."
        )

    # Get encryptor from the instance (works for both custom wrapper and AES)
    encryptor = encryption_instance.get_json_encryptor(model_type)
    if encryptor is not None:
        # Prepare data for encryption by serializing non-JSON-serializable objects
        # (e.g., BaseUser in langgraph_auth_user)
        data = _prepare_data_for_encryption(data)

        # For custom encryption, extract fields that should never be encrypted.
        # AES uses an allowlist so it doesn't need this exclusion logic.
        skipped_fields: dict[str, Any] = {}
        is_custom_encryption = isinstance(encryption_instance, JsonEncryptionWrapper)
        if is_custom_encryption:
            # Build path for skip field evaluation
            effective_path = path if path else model_type
            data, skipped_fields = _extract_skip_fields(data, effective_path)

        # Build context for SDK interface (AES ignores this, custom uses it)
        context_dict = get_encryption_context()
        if LANGGRAPH_ENCRYPTION:
            ctx = EncryptionContext(
                model=model_type, field=field, metadata=context_dict
            )
        else:
            ctx = None  # AES doesn't need the EncryptionContext

        # The encryptor handles key validation and context storage internally
        encrypted = await encryptor(ctx, data)

        # Merge back skipped fields (for custom encryption)
        if skipped_fields and isinstance(encrypted, dict):
            encrypted = {**encrypted, **skipped_fields}

        await logger.adebug(
            "Encrypted JSON data",
            model_type=model_type,
            field=field,
            encryption_type="aes_only"
            if isinstance(encryption_instance, AesEncryptionInstance)
            else "custom_with_aes_migration",
            skipped_fields=list(skipped_fields.keys()) if skipped_fields else None,
        )
        return encrypted

    # No JSON encryption configured, but store context for blob encryption
    # This allows the worker to extract context even when JSON encryption is disabled
    context_dict = get_encryption_context()
    if context_dict and isinstance(data, dict):
        data = dict(data)  # shallow copy to avoid mutating input
        data[BLOB_ENCRYPTION_CONTEXT_KEY] = context_dict
    return data


def extract_blob_encryption_context(
    data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract blob encryption context from a data dict.

    This is used by the worker to extract the encryption context needed for
    blob encryption during checkpoint serialization.

    Checks both __blob_encryption_context__ (for blob-only encryption) and
    __encryption_context__ (from JSON encryption, backward compatibility).

    Args:
        data: The data dict that may contain an encryption context

    Returns:
        The parsed encryption context dict, or None if not present
    """
    if data is None:
        return None

    # Prefer __blob_encryption_context__ (explicit blob context)
    # Fall back to __encryption_context__ (from JSON encryption, backward compat)
    return data.get(BLOB_ENCRYPTION_CONTEXT_KEY) or data.get(ENCRYPTION_CONTEXT_KEY)


async def decrypt_json_if_needed(
    data: dict[str, Any] | None,
    encryption_instance: JsonEncryptionWrapper | AesEncryptionInstance | None,
    model_type: ModelType,
    field: str | None = None,
) -> dict[str, Any] | None:
    """Decrypt JSON data dict based on encryption markers.

    Routes to the appropriate decryptor based on the type in __encryption_context__:
    1. If marker present with AES type → use AES decryptor
    2. If marker present without AES type → use custom decryptor
    3. No marker → plaintext (return unchanged)

    The routing logic is handled internally by the wrapper/instance:
    - JsonEncryptionWrapper handles routing for custom + AES migration
    - AesEncryptionInstance handles AES-only decryption

    Args:
        data: The data dict (encrypted or plaintext)
        encryption_instance: The effective encryption instance (wrapper or AES-only)
        model_type: The type of model (e.g., "thread", "assistant", "run")
        field: The specific field being decrypted (e.g., "metadata", "context")

    Returns:
        Decrypted data dict (without reserved keys), or original if not encrypted

    Raises:
        DecryptorMissingError: If data is encrypted but required decryptor is not configured
        EncryptionRoutingError: If markers are inconsistent (e.g., AES values with custom marker)
    """
    if data is None:
        return data

    # No encryption configured
    if encryption_instance is None:
        # If data has encryption marker but no decryptor, raise error
        if ENCRYPTION_CONTEXT_KEY in data:
            context_dict = data[ENCRYPTION_CONTEXT_KEY]
            if is_aes_encryption_context(context_dict):
                raise DecryptorMissingError(
                    f"Data has AES encryption marker but LANGGRAPH_AES_KEY is not configured "
                    f"for {model_type}.{field}"
                )
            raise DecryptorMissingError(
                f"Data contains custom encryption marker but no encryption instance is configured "
                f"for {model_type}.{field}"
            )
        return strip_encryption_metadata(data)

    # Get decryptor - the wrapper/instance handles routing (AES/custom/plaintext) internally
    decryptor = encryption_instance.get_json_decryptor(model_type)

    # Build context for SDK interface (AES ignores this, custom uses it)
    context_dict = data.get(ENCRYPTION_CONTEXT_KEY, {})
    if LANGGRAPH_ENCRYPTION:
        ctx = EncryptionContext(model=model_type, field=field, metadata=context_dict)
    else:
        ctx = None

    decrypted = await decryptor(ctx, data)

    await logger.adebug(
        "Decrypted JSON data",
        model_type=model_type,
        field=field,
        encryption_type="aes_only"
        if isinstance(encryption_instance, AesEncryptionInstance)
        else "custom_with_aes_migration",
    )
    return decrypted


async def _decrypt_field(
    obj: dict[str, Any],
    field_name: str,
    encryption_instance: JsonEncryptionWrapper | AesEncryptionInstance | None,
    model_type: ModelType,
) -> tuple[str, Any]:
    """Decrypt a single field, returning (field_name, decrypted_value).

    Fields defined in NESTED_ENCRYPTED_SUBFIELDS have their subfields decrypted
    recursively (e.g., run.kwargs.config.configurable).

    Returns (field_name, None) if field doesn't exist or is falsy.
    """
    if not obj.get(field_name):
        return (field_name, obj.get(field_name))

    value = obj[field_name]
    # Database fields come back as either:
    # - dict: already parsed JSONB (psycopg JSON adapter)
    # - bytes/bytearray/memoryview/str: raw JSON to parse (psycopg binary mode)
    # - Fragment: wrapper around bytes (used by serde layer)
    if isinstance(value, dict):
        pass  # already parsed
    elif isinstance(value, (bytes, bytearray, memoryview, str, Fragment)):
        value = json_loads(value)
    else:
        raise TypeError(
            f"Cannot decrypt field '{field_name}': expected dict or JSON-serialized "
            f"bytes/str, got {type(value).__name__}"
        )

    decrypted = await decrypt_json_if_needed(
        value, encryption_instance, model_type, field=field_name
    )

    # Recursively decrypt subfields defined in NESTED_ENCRYPTED_SUBFIELDS.
    # This handles nested structures like run.kwargs.config.configurable where each
    # level needs individual encryption to preserve structure for SQL JSONB operations.
    nested_key = (model_type, field_name)
    if nested_key in NESTED_ENCRYPTED_SUBFIELDS and decrypted is not None:
        results = await asyncio.gather(
            *[
                _decrypt_field(decrypted, sf, encryption_instance, model_type)
                for sf in NESTED_ENCRYPTED_SUBFIELDS[nested_key]
                if sf in decrypted
            ]
        )
        for sf_name, sf_value in results:
            decrypted[sf_name] = sf_value

    return (field_name, decrypted)


async def _decrypt_object(
    obj: dict[str, Any],
    model_type: ModelType,
    fields: list[str],
    encryption_instance: JsonEncryptionWrapper | AesEncryptionInstance | None,
) -> None:
    """Decrypt all specified fields in a single object (in parallel).

    Only processes fields that exist in the object to avoid adding new fields.
    """
    results = await asyncio.gather(
        *[
            _decrypt_field(obj, f, encryption_instance, model_type)
            for f in fields
            if f in obj
        ]
    )
    for field_name, value in results:
        obj[field_name] = value


async def decrypt_response(
    obj: Mapping[str, Any],
    model_type: ModelType,
    fields: list[str],
) -> dict[str, Any]:
    """Decrypt specified fields in a response object (from database).

    IMPORTANT: This function only parses and decrypts fields when encryption is
    enabled (custom or AES). When encryption is disabled, the original object is
    returned as-is (no copy, no parsing). This is intentional: some fields can be
    very large, and we want to avoid parsing overhead when the bytes can be passed
    through directly to the response. Callers that need parsed dicts regardless of
    encryption state should use json_loads() on the fields they need to inspect.

    When encryption IS enabled, this parses bytes/memoryview/Fragment to dicts
    before decryption, and returns a shallow copy with decrypted fields.

    Fields defined in NESTED_ENCRYPTED_SUBFIELDS have their subfields decrypted
    recursively (e.g., config.configurable, config.metadata).

    Args:
        obj: Single mapping from database (fields may be bytes or already-parsed dicts, not mutated)
        model_type: Type identifier passed to EncryptionContext.model (e.g., "run", "cron", "thread")
        fields: List of field names to decrypt (e.g., ["metadata", "kwargs"])

    Returns:
        Original object if encryption disabled, otherwise new dict with decrypted fields
    """
    encryption_instance = get_encryption()
    if encryption_instance is None:
        # Even without encryption, the error field is stored as bytes (bytea column)
        # and must be parsed to return as a dict in the API response.
        error = obj.get("error")
        if isinstance(error, bytes | memoryview | Fragment):
            result = dict(obj)
            error_bytes = cast(
                "bytes | Fragment",
                bytes(error) if isinstance(error, memoryview) else error,
            )
            result["error"] = json_loads(error_bytes)
            return result
        return obj

    result = dict(obj)
    await _decrypt_object(result, model_type, fields, encryption_instance)
    return result


async def decrypt_responses(
    objects: Sequence[Mapping[str, Any]],
    model_type: ModelType,
    fields: list[str],
) -> list[dict[str, Any]]:
    """Decrypt specified fields in multiple response objects (from database).

    IMPORTANT: This function only parses and decrypts fields when encryption is
    enabled (custom or AES). When encryption is disabled, the original sequence is
    returned as-is (no copies, no parsing). This is intentional: some fields can be
    very large, and we want to avoid parsing overhead when the bytes can be passed
    through directly to the response. Callers that need parsed dicts regardless of
    encryption state should use json_loads() on the fields they need to inspect.

    When encryption IS enabled, this parses bytes/memoryview/Fragment to dicts
    before decryption, and returns a new list of shallow copies with decrypted fields.

    Fields defined in NESTED_ENCRYPTED_SUBFIELDS have their subfields decrypted
    recursively (e.g., config.configurable, config.metadata).

    Args:
        objects: Sequence of mappings from database (fields may be bytes or already-parsed dicts, not mutated)
        model_type: Type identifier passed to EncryptionContext.model (e.g., "run", "cron", "thread")
        fields: List of field names to decrypt (e.g., ["metadata", "kwargs"])

    Returns:
        Original sequence if encryption disabled, otherwise new list with decrypted fields
    """
    encryption_instance = get_encryption()
    if encryption_instance is None:
        # Even without encryption, the error field is stored as bytes (bytea column)
        # and must be parsed to return as a dict in the API response.
        needs_error_parsing = any(
            isinstance(obj.get("error"), bytes | memoryview | Fragment)
            for obj in objects
        )
        if needs_error_parsing:
            results = []
            for obj in objects:
                error = obj.get("error")
                if isinstance(error, bytes | memoryview | Fragment):
                    result = dict(obj)
                    error_bytes = cast(
                        "bytes | Fragment",
                        bytes(error) if isinstance(error, memoryview) else error,
                    )
                    result["error"] = json_loads(error_bytes)
                    results.append(result)
                else:
                    results.append(obj)
            return results
        return objects

    results = [dict(obj) for obj in objects]
    await asyncio.gather(
        *[
            _decrypt_object(result, model_type, fields, encryption_instance)
            for result in results
        ]
    )
    return results


async def _encrypt_field(
    data: Mapping[str, Any],
    field_name: str,
    encryption_instance: JsonEncryptionWrapper | AesEncryptionInstance | None,
    model_type: ModelType,
    path: str | None = None,
) -> tuple[str, Any]:
    """Encrypt a single field, returning (field_name, encrypted_value).

    Fields defined in NESTED_ENCRYPTED_SUBFIELDS have their subfields extracted
    and encrypted separately, then added back. This preserves the nested structure
    for SQL JSONB operations while encrypting each level individually.

    Args:
        data: The data mapping containing the field
        field_name: Name of the field to encrypt
        encryption_instance: The encryption instance to use
        model_type: The model type (e.g., "run", "thread")
        path: The current path for path-based skip rules (e.g., "run.kwargs")

    Returns (field_name, None) if field doesn't exist or is None.
    """
    if field_name not in data or data[field_name] is None:
        return (field_name, data.get(field_name))

    field_data = data[field_name]

    # Build path for this field
    current_path = f"{path}.{field_name}" if path else f"{model_type}.{field_name}"

    # Check if this field has subfields that need separate encryption
    nested_key = (model_type, field_name)
    subfields_to_extract: dict[str, Any] = {}

    if nested_key in NESTED_ENCRYPTED_SUBFIELDS:
        if not isinstance(field_data, dict):
            raise TypeError(
                f"'{field_name}' must be a dict for encryption, got {type(field_data).__name__}"
            )
        for subfield in NESTED_ENCRYPTED_SUBFIELDS[nested_key]:
            subfield_value = field_data.get(subfield)
            if subfield_value is not None and not isinstance(subfield_value, dict):
                raise TypeError(
                    f"'{subfield}' in '{field_name}' must be a dict for encryption, "
                    f"got {type(subfield_value).__name__}"
                )
            if subfield_value:
                subfields_to_extract[subfield] = subfield_value

        if subfields_to_extract:
            # Create a copy without subfields for the first encryption pass
            field_data = {
                k: v for k, v in field_data.items() if k not in subfields_to_extract
            }

    encrypted = await encrypt_json_if_needed(
        field_data,
        encryption_instance,
        model_type,
        field=field_name,
        path=current_path,
    )

    # Recursively encrypt extracted subfields and add them back
    if subfields_to_extract and isinstance(encrypted, dict):
        subfield_results = await asyncio.gather(
            *[
                _encrypt_field(
                    {sf_name: sf_value},
                    sf_name,
                    encryption_instance,
                    model_type,
                    path=current_path,
                )
                for sf_name, sf_value in subfields_to_extract.items()
            ]
        )
        for sf_name, sf_encrypted in subfield_results:
            encrypted[sf_name] = sf_encrypted

    return (field_name, encrypted)


async def encrypt_request(
    data: Mapping[str, Any],
    model_type: ModelType,
    fields: list[str],
) -> dict[str, Any]:
    """Encrypt specified fields in request data before passing to ops layer (in parallel).

    This is a generic helper that handles encryption for any object type.
    It uses the ContextVar to get encryption context (set by middleware or endpoint).

    When encryption is disabled (neither custom nor AES), the original data is
    returned as-is (no copy). When encryption IS enabled, returns a shallow copy
    with encrypted fields.

    Fields defined in NESTED_ENCRYPTED_SUBFIELDS have their subfields encrypted
    recursively (e.g., config.configurable, config.metadata).

    Only processes fields that exist in the data to avoid adding new fields.

    Args:
        data: Request data mapping to encrypt (not mutated)
        model_type: Type identifier passed to EncryptionContext.model (e.g., "run", "cron", "thread")
        fields: List of field names to encrypt (e.g., ["metadata", "kwargs"])

    Returns:
        Original data if encryption disabled, otherwise new dict with encrypted fields

    Example:
        encrypted = await encrypt_request(
            payload,
            "run",
            ["metadata"]
        )
    """
    encryption_instance = get_encryption()
    if encryption_instance is None:
        return data

    result = dict(data)
    encrypted_fields = await asyncio.gather(
        *[
            _encrypt_field(data, f, encryption_instance, model_type)
            for f in fields
            if f in data
        ]
    )
    for field_name, value in encrypted_fields:
        result[field_name] = value

    return result
