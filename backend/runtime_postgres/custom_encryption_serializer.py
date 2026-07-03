"""Custom encrypted serializer for checkpoint blobs.

This serializer applies custom encryption/decryption using handlers provided by the
user via LANGGRAPH_ENCRYPTION configuration.

This serializer implements AsyncSerializerProtocol with async methods (adumps_typed,
aloads_typed) that should be used in async contexts. The sync methods (dumps_typed,
loads_typed) use run_coroutine_threadsafe to call the async methods safely from
sync contexts that may already have a running event loop.

Format:
- type_str: `<base_type>+langchain.dev/v1` (e.g., `msgpack+langchain.dev/v1`)
- blob: JSON `{"b": <encrypted_b64>, "c": <context_b64>}`

Migration support:
- Reads detect format via type suffix and route to appropriate deserializer
- `+langchain.dev/v1` suffix → custom decrypt → base deserialize
- `+aes` suffix (no custom) → AES decrypt via aes_serializer (if provided)
- No suffix → plain base deserialize
- Writes always use custom encryption only (no double encryption with AES)
"""

import asyncio
import base64
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import orjson
from langgraph.checkpoint.serde.base import SerializerProtocol

from api.asyncio import get_event_loop

if TYPE_CHECKING:
    from langgraph_sdk import Encryption


@runtime_checkable
class AsyncSerializerProtocol(SerializerProtocol, Protocol):
    """Protocol for serializers with async support.

    Serializers implementing this protocol provide async methods (adumps_typed,
    aloads_typed) that can be awaited directly in async contexts, avoiding the
    need to use run_coroutine_threadsafe.
    """

    async def adumps_typed(self, obj: Any) -> tuple[str, bytes]: ...
    async def aloads_typed(self, data: tuple[str, bytes]) -> Any: ...


TYPE_VERSION_SUFFIX = "+langchain.dev/v1"


class AsyncSerializerAdapter:
    """Adapter that wraps a sync SerializerProtocol to provide async methods.

    This allows checkpoint code to always use async serde methods without conditionals.
    """

    def __init__(self, base: SerializerProtocol):
        self.base = base

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return self.base.dumps_typed(obj)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        return self.base.loads_typed(data)

    async def adumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return self.base.dumps_typed(obj)

    async def aloads_typed(self, data: tuple[str, bytes]) -> Any:
        return self.base.loads_typed(data)


def ensure_async_serde(serde: SerializerProtocol) -> AsyncSerializerProtocol:
    """Wrap a serializer to ensure it has async methods.

    If the serializer already implements AsyncSerializerProtocol, return it as-is.
    Otherwise, wrap it in AsyncSerializerAdapter.
    """
    if isinstance(serde, AsyncSerializerProtocol):
        return serde
    return AsyncSerializerAdapter(serde)


AES_TYPE_SUFFIX = "+aes"


class CustomEncryptionSerializer:
    """Serializer that applies custom encryption to checkpoint blobs.

    This serializer uses custom encryption for writes and supports reading
    AES-encrypted or unencrypted data for migration scenarios.

    Implements AsyncSerializerProtocol with async methods (adumps_typed, aloads_typed)
    that should be preferred in async contexts. The sync methods use
    run_coroutine_threadsafe to safely call the async methods from sync contexts.
    """

    def __init__(
        self,
        base: SerializerProtocol,
        encryption_instance: "Encryption",
        aes_serializer: SerializerProtocol | None = None,
    ):
        """Initialize with base serializer and encryption instance.

        Args:
            base: The base serializer (e.g., JsonPlusSerializer) for serialization
            encryption_instance: The Encryption instance with encryption handlers
            aes_serializer: Optional AES serializer for reading AES-encrypted data
        """
        self.base = base
        self.encryption_instance = encryption_instance
        self.aes_serializer = aes_serializer

    async def adumps_typed(self, obj: Any) -> tuple[str, bytes]:
        """Serialize and encrypt an object asynchronously, returning type and bytes.

        This is the preferred method in async contexts.

        Args:
            obj: The object to serialize and encrypt

        Returns:
            Tuple of (type_string with version suffix, encrypted_blob_json)
        """
        type_str, serialized = self.base.dumps_typed(obj)

        if self.encryption_instance._blob_encryptor:
            from langgraph_sdk import EncryptionContext

            from api.encryption.context import get_encryption_context

            context_dict = get_encryption_context()

            ctx = EncryptionContext(model="checkpoint", metadata=context_dict)

            encrypted = await self.encryption_instance._blob_encryptor(ctx, serialized)

            versioned_type = f"{type_str}{TYPE_VERSION_SUFFIX}"

            blob = orjson.dumps(
                {
                    "b": base64.b64encode(encrypted).decode(),
                    "c": base64.b64encode(orjson.dumps(context_dict)).decode(),
                },
            )

            return versioned_type, blob

        return type_str, serialized

    async def aloads_typed(self, data: tuple[str, bytes]) -> Any:
        """Decrypt and deserialize typed data asynchronously.

        Routes to appropriate deserializer based on type suffix:
        - `+langchain.dev/v1` → custom decrypt → base deserialize
        - `+aes` (no custom suffix) → AES deserialize
        - No suffix → plain base deserialize

        Args:
            data: Tuple of (type_string, blob_bytes)

        Returns:
            Deserialized object
        """
        type_str, blob = data

        if type_str.endswith(TYPE_VERSION_SUFFIX):
            if self.encryption_instance._blob_decryptor:
                from langgraph_sdk import EncryptionContext

                base_type = type_str[: -len(TYPE_VERSION_SUFFIX)]

                parsed = orjson.loads(blob)

                encrypted = base64.b64decode(parsed["b"])

                context_dict = orjson.loads(base64.b64decode(parsed["c"]))

                ctx = EncryptionContext(model="checkpoint", metadata=context_dict)

                decrypted = await self.encryption_instance._blob_decryptor(ctx, encrypted)

                return self.base.loads_typed((base_type, decrypted))

        if AES_TYPE_SUFFIX in type_str and self.aes_serializer is not None:
            return self.aes_serializer.loads_typed(data)

        return self.base.loads_typed(data)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        """Serialize and encrypt an object, returning type and bytes.

        This sync method uses run_coroutine_threadsafe to call adumps_typed.
        Prefer adumps_typed in async contexts.

        Args:
            obj: The object to serialize and encrypt

        Returns:
            Tuple of (type_string with version suffix, encrypted_blob_json)
        """
        loop = get_event_loop()
        future = asyncio.run_coroutine_threadsafe(self.adumps_typed(obj), loop)
        return future.result()

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        """Decrypt and deserialize typed data.

        This sync method uses run_coroutine_threadsafe to call aloads_typed.
        Prefer aloads_typed in async contexts.

        Args:
            data: Tuple of (type_string, blob_bytes)

        Returns:
            Deserialized object
        """
        loop = get_event_loop()
        future = asyncio.run_coroutine_threadsafe(self.aloads_typed(data), loop)
        return future.result()
