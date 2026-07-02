"""Encryption gRPC servicer implementation.

This module implements the Encryption gRPC service, exposing the Python
custom encryption implementation to the Go server.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import grpc
import orjson
import structlog
from langgraph_grpc_common.proto.encryption_pb2 import (
    DecryptResponse,
    EncryptResponse,
)
from langgraph_grpc_common.proto.encryption_pb2_grpc import EncryptionServicer
from langgraph_sdk import EncryptionContext

from langgraph_api.encryption.middleware import _extract_skip_fields
from langgraph_api.encryption.shared import BLOB_ENCRYPTION_CONTEXT_KEY, get_encryption
from langgraph_api.schema import NESTED_ENCRYPTED_SUBFIELDS

if TYPE_CHECKING:
    from grpc import aio as grpc_aio

    from langgraph_api.encryption.custom import ModelType

logger = structlog.stdlib.get_logger(__name__)

ENCRYPTION_CONTEXT_KEY = "__encryption_context__"


def _parse_metadata(metadata: dict[str, bytes]) -> dict[str, Any]:
    """Parse metadata from proto map<string, bytes> to dict.

    Args:
        metadata: Map of string keys to JSON-encoded bytes values

    Returns:
        Dict with parsed JSON values

    Raises:
        ValueError: If any value is not valid JSON
    """
    result = {}
    for k, v in metadata.items():
        if v:
            try:
                result[k] = orjson.loads(v)
            except Exception as e:
                raise ValueError(
                    f"Failed to parse metadata value for key '{k}' as JSON: {e}"
                ) from e
        else:
            result[k] = None
    return result


def _build_encryption_context(
    model: str | None,
    field: str | None,
    metadata: dict[str, bytes],
) -> EncryptionContext:
    """Build an EncryptionContext from proto fields.

    Args:
        model: Model type (e.g., "thread", "run")
        field: Field name (e.g., "metadata", "kwargs")
        metadata: Proto metadata map

    Returns:
        EncryptionContext for SDK encryption handlers
    """
    return EncryptionContext(
        model=model or None,
        field=field or None,
        metadata=_parse_metadata(metadata),
    )


class EncryptionServicerImpl(EncryptionServicer):
    """Implementation of the Encryption gRPC service.

    This servicer delegates to the Python custom encryption implementation,
    allowing the Go server to use Python-based encryption when custom
    encryption is configured.

    For JSON operations, uses the JsonEncryptionWrapper which handles:
    - Key preservation validation
    - Encryption context marker storage
    - Migration routing between AES and custom encryption
    """

    async def _encrypt_field_recursive(
        self,
        data: dict[str, Any],
        model_type: str | None,
        field_name: str,
        metadata: dict[str, bytes],
        encryptor,
        path: str | None = None,
    ) -> dict[str, Any]:
        """Encrypt a field, handling nested subfields defined in NESTED_ENCRYPTED_SUBFIELDS.

        This mirrors the middleware's _encrypt_field behavior:
        1. Extract subfields that need separate encryption
        2. Extract fields that should never be encrypted (NEVER_ENCRYPT_* rules)
        3. Encrypt the parent field (without subfields or skipped fields)
        4. Merge skipped fields back as plaintext
        5. Recursively encrypt each subfield
        6. Add encrypted subfields back to the result
        """
        # Build the current path for skip-field evaluation
        current_path = (
            f"{path}.{field_name}"
            if path
            else (f"{model_type}.{field_name}" if model_type else field_name)
        )

        subfields_to_extract: dict[str, Any] = {}

        # Check if this field has nested subfields that need separate encryption
        # Only look up in NESTED_ENCRYPTED_SUBFIELDS when model_type is defined
        if model_type is not None:
            nested_key = (model_type, field_name)
            for subfield in NESTED_ENCRYPTED_SUBFIELDS.get(nested_key, []):
                subfield_value = data.get(subfield)
                if subfield_value is not None and isinstance(subfield_value, dict):
                    subfields_to_extract[subfield] = subfield_value

        # Create data without subfields for the parent encryption
        if subfields_to_extract:
            data_without_subfields = {
                k: v for k, v in data.items() if k not in subfields_to_extract
            }
        else:
            data_without_subfields = data

        # Extract fields that should never be encrypted (custom encryption only)
        data_without_subfields, skipped_fields = _extract_skip_fields(
            data_without_subfields, current_path
        )

        # Encrypt the parent field (without subfields or skipped fields)
        ctx = _build_encryption_context(model_type, field_name, metadata)
        encrypted = await encryptor(ctx, data_without_subfields)

        # Merge back skipped fields as plaintext
        if skipped_fields and isinstance(encrypted, dict):
            encrypted = {**encrypted, **skipped_fields}

        # Recursively encrypt subfields and add them back
        if subfields_to_extract and isinstance(encrypted, dict):
            subfield_tasks = [
                self._encrypt_field_recursive(
                    sf_value,
                    model_type,
                    sf_name,
                    metadata,
                    encryptor,
                    path=current_path,
                )
                for sf_name, sf_value in subfields_to_extract.items()
            ]
            subfield_results = await asyncio.gather(*subfield_tasks)
            for (sf_name, _), sf_encrypted in zip(
                subfields_to_extract.items(),
                subfield_results,
                strict=True,
            ):
                encrypted[sf_name] = sf_encrypted

        return encrypted

    async def _decrypt_field_recursive(
        self,
        data: dict[str, Any],
        model_type: str | None,
        field_name: str,
        decryptor,
    ) -> dict[str, Any]:
        """Decrypt a field, handling nested subfields defined in NESTED_ENCRYPTED_SUBFIELDS.

        This mirrors the Python middleware's _decrypt_field behavior:
        1. Decrypt the parent field
        2. Recursively decrypt any nested subfields
        """
        # First decrypt the parent field.
        # Populate ctx.metadata from the stored __encryption_context__ marker so
        # that user decrypt handlers (e.g. ones that look up ctx.metadata["tenant_id"])
        # receive the same context that was present at encryption time.
        stored_ctx = data.get(ENCRYPTION_CONTEXT_KEY)
        metadata = stored_ctx if isinstance(stored_ctx, dict) else {}
        ctx = EncryptionContext(model=model_type, field=field_name, metadata=metadata)
        decrypted = await decryptor(ctx, data)

        # Check for nested subfields that need recursive decryption
        # Only look up in NESTED_ENCRYPTED_SUBFIELDS when model_type is defined
        if model_type is not None and isinstance(decrypted, dict):
            nested_key = (model_type, field_name)
            subfield_tasks = []
            subfield_names = []
            for sf_name in NESTED_ENCRYPTED_SUBFIELDS.get(nested_key, []):
                sf_value = decrypted.get(sf_name)
                if (
                    sf_value is not None
                    and isinstance(sf_value, dict)
                    and ENCRYPTION_CONTEXT_KEY in sf_value
                ):
                    subfield_names.append(sf_name)
                    subfield_tasks.append(
                        self._decrypt_field_recursive(
                            sf_value, model_type, sf_name, decryptor
                        )
                    )

            if subfield_tasks:
                subfield_results = await asyncio.gather(*subfield_tasks)
                for sf_name, sf_decrypted in zip(
                    subfield_names, subfield_results, strict=True
                ):
                    decrypted[sf_name] = sf_decrypted

        return decrypted

    async def EncryptJSON(
        self,
        request,
        context: grpc_aio.ServicerContext,
    ) -> EncryptResponse:
        """Encrypt JSON data using the configured encryption.

        Uses the JsonEncryptionWrapper for proper key validation and context storage.
        Handles nested subfields defined in NESTED_ENCRYPTED_SUBFIELDS recursively.
        """
        try:
            encryption_instance = get_encryption()
            if encryption_instance is None:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                context.set_details("No encryption configured")
                raise RuntimeError("No encryption configured")

            data: dict[str, Any] = orjson.loads(request.data)

            model_type: ModelType | None = request.context.model or None
            field = request.context.field or None

            encryptor = encryption_instance.get_json_encryptor(model_type)
            if encryptor is None:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                model_err = f"No encryptor configured for model type: {model_type}"
                context.set_details(model_err)
                raise RuntimeError(model_err)

            # Use recursive encryption that handles NESTED_ENCRYPTED_SUBFIELDS
            encrypted = await self._encrypt_field_recursive(
                data,
                model_type,
                field or "",
                dict(request.context.metadata),
                encryptor,
            )
            encrypted_bytes = orjson.dumps(encrypted)

            return EncryptResponse(data=encrypted_bytes)

        except Exception as e:
            await logger.aerror("EncryptJSON failed", error=str(e), exc_info=True)
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Encryption failed: {e}")
            raise

    async def DecryptJSON(
        self,
        request,
        context: grpc_aio.ServicerContext,
    ) -> DecryptResponse:
        """Decrypt JSON data using the configured encryption.

        Uses the JsonEncryptionWrapper which routes to the appropriate decryptor
        based on the encryption context marker (handles AES migration).
        Handles nested subfields defined in NESTED_ENCRYPTED_SUBFIELDS recursively.
        """
        try:
            encryption_instance = get_encryption()
            if encryption_instance is None:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                context.set_details("No encryption configured")
                raise RuntimeError("No encryption configured")

            data: dict[str, Any] = orjson.loads(request.data)

            # Preserve __blob_encryption_context__ — this is user-facing
            # metadata (e.g., tenant/key info for cron execution) that must
            # survive decryption.  The decryptor's strip_encryption_metadata
            # removes it, so we save and restore it after decryption.
            blob_enc_ctx = data.get(BLOB_ENCRYPTION_CONTEXT_KEY)

            # Model and field are optional, used for decryptor selection/routing
            model_type: ModelType | None = request.model or None
            field = request.field or None

            # Get the decryptor from the wrapper (handles routing based on
            # __encryption_context__ marker in the data)
            decryptor = encryption_instance.get_json_decryptor(model_type)
            if decryptor is None:
                model_err = f"No decryptor configured for model type: {model_type}"
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(model_err)
                raise RuntimeError(model_err)

            # Use recursive decryption that handles NESTED_ENCRYPTED_SUBFIELDS
            decrypted = await self._decrypt_field_recursive(
                data, model_type, field or "", decryptor
            )

            # Restore __blob_encryption_context__ so that internal consumers
            # (e.g., the cron scheduler) can recover the original encryption
            # context when creating downstream runs.
            if blob_enc_ctx is not None and isinstance(decrypted, dict):
                decrypted[BLOB_ENCRYPTION_CONTEXT_KEY] = blob_enc_ctx

            decrypted_bytes = orjson.dumps(decrypted)

            return DecryptResponse(data=decrypted_bytes)

        except Exception as e:
            await logger.aerror("DecryptJSON failed", error=str(e), exc_info=True)
            if context.code() is None:
                context.set_code(grpc.StatusCode.INTERNAL)
                context.set_details(f"Decryption failed: {e}")
            raise
