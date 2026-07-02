"""gRPC-based threads operations."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Sequence

import orjson
import structlog
from google.protobuf import field_mask_pb2
from grpc.aio import AioRpcError
from langgraph.checkpoint.serde.jsonplus import _msgpack_ext_hook_to_json
from langgraph.types import StateSnapshot, StateUpdate
from langgraph_grpc_common.proto import checkpointer_pb2
from langgraph_grpc_common.proto import core_api_pb2 as pb
from langgraph_grpc_common.proto import enum_thread_status_pb2 as enum_thread_status
from langgraph_grpc_common.proto import (
    enum_thread_stream_mode_pb2 as enum_thread_stream_mode,
)
from langgraph_sdk import Auth
from starlette.exceptions import HTTPException

from langgraph_api import _checkpointer as api_checkpointer
from langgraph_api import store as api_store
from langgraph_api.command import map_cmd
from langgraph_api.config import THREAD_TTL, USE_CUSTOM_CHECKPOINTER
from langgraph_api.encryption.shared import get_encryption
from langgraph_api.graph import get_graph
from langgraph_api.grpc.client import get_shared_client
from langgraph_api.grpc.ops import (
    Authenticated,
    _filters_to_proto,
    _handle_grpc_error,
    _map_sort_order,
    build_encryption_context,
    grpc_error_guard,
    map_if_exists,
    transform_grpc_error_event,
)
from langgraph_api.grpc.ops.runs import Runs
from langgraph_api.schema import ThreadUpdateResponse
from langgraph_api.serde import json_dumpb, json_dumpb_optional, json_loads
from langgraph_api.state import patch_interrupt, state_snapshot_to_thread_state
from langgraph_api.utils import fetchone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph_api.schema import (
        MetadataInput,
        OnConflictBehavior,
        Thread,
        ThreadSelectField,
        ThreadStatus,
    )

logger = structlog.stdlib.get_logger(__name__)


# Read-mask preset for Threads.get callers that need metadata/config but not
# the large "values" column. Used by state get/update/bulk/history which
# only need graph_id (from metadata) and the thread config.
THREAD_METADATA_READ_MASK_PATHS: list[str] = ["metadata", "config"]


THREAD_STATUS_TO_PB = {
    "idle": enum_thread_status.idle,
    "busy": enum_thread_status.busy,
    "interrupted": enum_thread_status.interrupted,
    "error": enum_thread_status.error,
}

THREAD_STATUS_FROM_PB = {v: k for k, v in THREAD_STATUS_TO_PB.items()}

THREAD_SORT_BY_MAP = {
    "unspecified": pb.ThreadsSortBy.THREADS_SORT_BY_UNSPECIFIED,  # for enum completeness, never sent
    "thread_id": pb.ThreadsSortBy.THREADS_SORT_BY_THREAD_ID,
    "created_at": pb.ThreadsSortBy.THREADS_SORT_BY_CREATED_AT,
    "updated_at": pb.ThreadsSortBy.THREADS_SORT_BY_UPDATED_AT,
    "status": pb.ThreadsSortBy.THREADS_SORT_BY_STATUS,
    "state_updated_at": pb.ThreadsSortBy.THREADS_SORT_BY_STATE_UPDATED_AT,
}

THREAD_TTL_STRATEGY_MAP = {
    "delete": pb.ThreadTTLStrategy.THREAD_TTL_STRATEGY_DELETE,
    "keep_latest": pb.ThreadTTLStrategy.THREAD_TTL_STRATEGY_KEEP_LATEST,
}

THREAD_STREAM_MODE_TO_PB = {
    "unknown": enum_thread_stream_mode.unknown,
    "lifecycle": enum_thread_stream_mode.lifecycle,
    "run_modes": enum_thread_stream_mode.run_modes,
    "state_update": enum_thread_stream_mode.state_update,
}


def _map_thread_stream_modes(
    stream_mode: str | list[str] | None,
) -> list[enum_thread_stream_mode.ThreadStreamMode]:
    """Map thread stream mode string(s) to protobuf enum list (filtering invalid modes)."""
    if stream_mode is None:
        return []

    modes = [stream_mode] if isinstance(stream_mode, str) else stream_mode
    result = []

    for mode in modes:
        proto_mode = THREAD_STREAM_MODE_TO_PB.get(mode)
        if proto_mode is None:
            sanitized = str(mode)[:50] + ("..." if len(str(mode)) > 50 else "")
            logger.error("Got invalid thread stream mode '%s', ignoring", sanitized)
        else:
            result.append(proto_mode)

    return result


def _snapshot_defaults():
    """Support older versions of langgraph that don't have interrupts field."""
    if not hasattr(StateSnapshot, "interrupts"):
        return {}
    return {"interrupts": tuple()}


def _map_thread_status(
    status: ThreadStatus | None,
) -> enum_thread_status.ThreadStatus | None:
    if status is None:
        return None
    return THREAD_STATUS_TO_PB.get(status)


def _map_threads_sort_by(sort_by: str | None) -> pb.ThreadsSortBy:
    if not sort_by or sort_by.lower() == "unspecified":
        return pb.ThreadsSortBy.THREADS_SORT_BY_CREATED_AT
    return THREAD_SORT_BY_MAP.get(
        sort_by.lower(), pb.ThreadsSortBy.THREADS_SORT_BY_CREATED_AT
    )


def _map_thread_ttl(ttl: dict[str, Any] | None) -> pb.ThreadTTLConfig | None:
    if not ttl:
        return None

    config = pb.ThreadTTLConfig()
    strategy = ttl.get("strategy")
    if strategy:
        mapped_strategy = THREAD_TTL_STRATEGY_MAP.get(str(strategy).lower())
        if mapped_strategy is None:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail=f"Invalid thread TTL strategy: {strategy}. Expected one of {list(THREAD_TTL_STRATEGY_MAP.keys())}",
            )
        config.strategy = mapped_strategy

    ttl_value = ttl.get("ttl", ttl.get("default_ttl"))
    if ttl_value is not None:
        config.default_ttl = float(ttl_value)

    sweep_interval = ttl.get("sweep_interval_minutes")
    if sweep_interval is not None:
        config.sweep_interval_minutes = int(sweep_interval)

    # Note: sweep_limit is a server-side configuration for the TTL sweep loop,
    # not a per-thread setting, so we don't send it via gRPC

    return config


def fragment_to_value(fragment: pb.Fragment | None) -> Any:
    if fragment is None or not fragment.value or fragment.value == b"{}":
        return None
    try:
        return json_loads(fragment.value)
    except orjson.JSONDecodeError:
        logger.warning("Failed to decode fragment", fragment=fragment.value)
        return None


def _proto_interrupts_to_dict(
    interrupts_map: dict[str, pb.Interrupts],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for key, interrupts in interrupts_map.items():
        entries: list[dict[str, Any]] = []
        for interrupt in interrupts.interrupts:
            entry: dict[str, Any] = {
                "id": interrupt.id or None,
                "value": json_loads(interrupt.value),
            }
            if interrupt.when:
                entry["when"] = interrupt.when
            if interrupt.resumable:
                entry["resumable"] = interrupt.resumable
            if interrupt.ns:
                entry["ns"] = list(interrupt.ns)
            entries.append(entry)
        out[key] = entries
    return out


def proto_to_thread(proto_thread: pb.Thread) -> Thread:
    """Convert protobuf Thread to API dictionary format."""
    thread_id = (
        UUID(proto_thread.thread_id.value)
        if proto_thread.HasField("thread_id")
        else None
    )
    if thread_id is None:
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail="Thread response missing thread_id",
        )
    created_at = (
        proto_thread.created_at.ToDatetime(tzinfo=UTC)
        if proto_thread.HasField("created_at")
        else None
    )
    updated_at = (
        proto_thread.updated_at.ToDatetime(tzinfo=UTC)
        if proto_thread.HasField("updated_at")
        else None
    )
    state_updated_at = (
        proto_thread.state_updated_at.ToDatetime(tzinfo=UTC)
        if proto_thread.HasField("state_updated_at")
        else None
    )
    status = THREAD_STATUS_FROM_PB.get(proto_thread.status, "idle")

    result = {
        "thread_id": thread_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "state_updated_at": state_updated_at,
        # Unlike other fields, metadata should never be `None`.
        "metadata": fragment_to_value(proto_thread.metadata) or {},
        "config": fragment_to_value(proto_thread.config) or {},
        "error": fragment_to_value(proto_thread.error),
        "status": status,
        "values": fragment_to_value(proto_thread.values),
        "interrupts": _proto_interrupts_to_dict(dict(proto_thread.interrupts)),
    }

    if proto_thread.extracted_json:
        result["extracted"] = json_loads(proto_thread.extracted_json)

    if proto_thread.HasField("ttl"):
        ttl = proto_thread.ttl
        strategy_map = {
            pb.THREAD_TTL_STRATEGY_DELETE: "delete",
            pb.THREAD_TTL_STRATEGY_KEEP_LATEST: "keep_latest",
        }
        result["ttl"] = {
            "strategy": strategy_map.get(ttl.strategy, "delete"),
            "ttl_minutes": ttl.ttl_minutes,
            "expires_at": ttl.expires_at.ToDatetime(tzinfo=UTC)
            if ttl.HasField("expires_at")
            else None,
        }
    return result


def _filter_thread_fields(
    thread: Thread, select: list[ThreadSelectField] | None
) -> dict[str, Any]:
    if not select:
        return dict(thread)
    result = {field: thread[field] for field in select if field in thread}
    if "extracted" in thread:
        result["extracted"] = thread["extracted"]
    return result


def _normalize_uuid(value: UUID | str) -> str:
    return str(value) if isinstance(value, UUID) else str(UUID(str(value)))


def _serialize_for_encryption(
    data: BaseException | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Serialize data to JSON-compatible form before encryption.

    This converts non-JSON-serializable types (like bytes) to JSON-safe forms
    (like base64 strings) using the existing serde machinery.
    """
    if data is None:
        return None

    return json_loads(json_dumpb(data))


async def _encrypt_thread_field(
    data: BaseException | dict[str, Any] | None, field_name: str
) -> dict[str, Any] | None:
    """Apply encryption to a thread field (AES mode only)

    Encryption is handled by the gRPC server for custom encryption,
    but not for AES encryption."""
    from langgraph_api.encryption.middleware import (  # noqa: PLC0415
        encrypt_json_if_needed,
    )
    from langgraph_api.encryption.shared import using_custom_encryption  # noqa: PLC0415

    if isinstance(data, BaseException):
        data = {"error": type(data).__name__, "message": str(data)}
    if using_custom_encryption() or data is None:
        return data
    if not (enc := get_encryption()):
        return data

    return await encrypt_json_if_needed(
        _serialize_for_encryption(data), enc, "thread", field=field_name
    )


async def _thread_status_checkpoint_to_proto(
    checkpoint: dict[str, Any] | None,
) -> pb.ThreadStatusCheckpoint | None:
    """Convert checkpoint dict to ThreadStatusCheckpoint proto (with optional encryption)."""
    if checkpoint is None:
        return None

    values = checkpoint.get("values", {})

    # Compute interrupts map from tasks
    interrupts = {
        t["id"]: [patch_interrupt(i) for i in t["interrupts"]]
        for t in checkpoint.get("tasks", [])
        if t.get("interrupts")
    }
    encrypted_interrupts, encrypted_values = await asyncio.gather(
        _encrypt_thread_field(interrupts, "interrupts"),
        _encrypt_thread_field(values, "values"),
    )

    return pb.ThreadStatusCheckpoint(
        values_json=json_dumpb(encrypted_values),
        next=list(checkpoint.get("next", [])),
        interrupts_json=json_dumpb(encrypted_interrupts),
    )


@grpc_error_guard
class Threads(Authenticated):
    """gRPC-based threads operations."""

    resource = "threads"

    @staticmethod
    async def search(
        conn,  # Not used in gRPC implementation
        *,
        ids: list[str] | list[UUID] | None = None,
        metadata: MetadataInput,
        values: MetadataInput,
        status: ThreadStatus | None,
        limit: int,
        offset: int,
        sort_by: str | None = None,
        sort_order: str | None = None,
        select: list[ThreadSelectField] | None = None,
        extract: dict[str, str] | None = None,
        ctx: Any = None,
    ) -> tuple[AsyncIterator[Thread], int | None]:
        metadata = metadata or {}
        values = values or {}

        auth_filters = await Threads.handle_event(
            ctx,
            "search",
            {
                "metadata": metadata,
                "values": values,
                "status": status,
                "limit": limit,
                "offset": offset,
            },
        )

        request_kwargs: dict[str, Any] = {
            "filters": auth_filters,
            "metadata_json": json_dumpb_optional(metadata),
            "values_json": json_dumpb_optional(values),
            "limit": limit,
            "offset": offset,
            "sort_by": _map_threads_sort_by(sort_by),
            "sort_order": _map_sort_order(sort_order),
        }

        if status:
            mapped_status = _map_thread_status(status)
            if mapped_status is None:
                raise HTTPException(
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    detail=f"Invalid thread status: {status}",
                )
            request_kwargs["status"] = mapped_status

        if select:
            request_kwargs["select"] = select

        if ids:
            request_kwargs["ids"] = [
                pb.UUID(value=_normalize_uuid(thread_id)) for thread_id in ids
            ]

        if extract:
            request_kwargs["extract"] = extract

        client = await get_shared_client()
        response = await client.threads.Search(
            pb.SearchThreadsRequest(**request_kwargs)
        )

        threads = [proto_to_thread(thread) for thread in response.threads]
        cursor = offset + limit if len(threads) == limit else None

        async def generate_results():
            for thread in threads:
                yield _filter_thread_fields(thread, select)

        return generate_results(), cursor

    @staticmethod
    async def count(
        conn,  # Not used
        *,
        metadata: MetadataInput,
        values: MetadataInput,
        status: ThreadStatus | None,
        ctx: Any = None,
    ) -> int:
        metadata = metadata or {}
        values = values or {}

        auth_filters = await Threads.handle_event(
            ctx,
            "search",
            {
                "metadata": metadata,
                "values": values,
                "status": status,
            },
        )

        request_kwargs: dict[str, Any] = {
            "filters": auth_filters,
            "metadata_json": json_dumpb_optional(metadata),
            "values_json": json_dumpb_optional(values),
        }
        if status:
            mapped_status = _map_thread_status(status)
            if mapped_status is None:
                raise HTTPException(
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    detail=f"Invalid thread status: {status}",
                )
            request_kwargs["status"] = mapped_status

        client = await get_shared_client()
        response = await client.threads.Count(pb.CountThreadsRequest(**request_kwargs))

        return int(response.count)

    @staticmethod
    async def get(
        conn,  # Not used
        thread_id: UUID | str,
        ctx: Auth.types.BaseAuthContext | None = None,
        filters: Auth.types.FilterType | list[pb.AuthFilter] | None = None,
        include_ttl: bool = False,
        read_mask_paths: list[str] | None = None,
    ) -> AsyncIterator[Thread]:
        """Get a thread by ID.

        Args:
            conn: Not used (required for interface compatibility)
            thread_id: Thread ID
            ctx: Auth context
            filters: Additional auth filters to merge with auth context filters.
                     Accepts either raw dict filters (FilterType) or pre-processed
                     proto filters (list[pb.AuthFilter]).
            include_ttl: When True, include TTL information in the response.
            read_mask_paths: Optional list of field names to fetch from the DB.
                None (default) fetches all columns. When set, only those columns
                (plus always-included identity/status columns) are queried.
                Use ["metadata", "config"] to skip the large "values" column, or
                [] for an auth/existence check that discards the thread body.
        """
        auth_filters = await Threads.handle_event(
            ctx, "read", {"thread_id": str(thread_id)}
        )
        # Merge auth filters with any additional parent filters provided.
        # (Parent filters take precedence.)
        if filters:
            if isinstance(filters, list):
                # Already proto format
                auth_filters = (auth_filters or []) + filters
            else:
                # Raw dict format, convert to proto
                auth_filters = (auth_filters or []) + _filters_to_proto(filters)

        read_mask = (
            field_mask_pb2.FieldMask(paths=read_mask_paths)
            if read_mask_paths is not None
            else None
        )
        request = pb.GetThreadRequest(
            thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
            filters=auth_filters,
            include_ttl=include_ttl if include_ttl else None,
            read_mask=read_mask,
        )
        client = await get_shared_client()
        response = await client.threads.Get(request)

        thread = proto_to_thread(response)

        async def generate_result():
            yield thread

        return generate_result()

    @staticmethod
    async def put(
        conn,  # Not used
        thread_id: UUID | str,
        *,
        metadata: MetadataInput,
        if_exists: OnConflictBehavior,
        ttl: dict[str, Any] | None = None,
        ctx: Any = None,
    ) -> AsyncIterator[Thread]:
        metadata = metadata or {}

        auth_filters = await Threads.handle_event(
            ctx,
            "create",
            {
                "thread_id": str(thread_id),
                "metadata": metadata,
                "if_exists": if_exists,
            },
        )

        request = pb.CreateThreadRequest(
            thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
            filters=auth_filters,
            if_exists=map_if_exists(if_exists),
            metadata_json=json_dumpb_optional(metadata),
            encryption_context=build_encryption_context("thread"),
        )
        ttl_config = ttl if ttl is not None else THREAD_TTL
        mapped_ttl_config = _map_thread_ttl(ttl_config)
        if mapped_ttl_config is not None:
            request.ttl.CopyFrom(mapped_ttl_config)

        client = await get_shared_client()
        response = await client.threads.Create(request)
        thread = proto_to_thread(response)

        async def generate_result():
            yield thread

        return generate_result()

    @staticmethod
    async def patch(
        conn,  # Not used
        thread_id: UUID | str,
        *,
        metadata: MetadataInput,
        ttl: dict[str, Any] | None = None,
        ctx: Any = None,
        read_mask_paths: list[str] | None = None,
    ) -> AsyncIterator[Thread]:
        metadata = metadata or {}

        auth_filters = await Threads.handle_event(
            ctx,
            "update",
            {
                "thread_id": str(thread_id),
                "metadata": metadata,
            },
        )

        request = pb.PatchThreadRequest(
            thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
            filters=auth_filters,
            metadata_json=json_dumpb_optional(metadata),
            encryption_context=build_encryption_context("thread"),
        )

        ttl_config = _map_thread_ttl(ttl)
        if ttl_config is not None:
            request.ttl.CopyFrom(ttl_config)

        if read_mask_paths is not None:
            request.read_mask.CopyFrom(field_mask_pb2.FieldMask(paths=read_mask_paths))

        client = await get_shared_client()
        response = await client.threads.Patch(request)

        thread = proto_to_thread(response)

        async def generate_result():
            yield thread

        return generate_result()

    @staticmethod
    async def delete(
        conn,  # Not used
        thread_id: UUID | str,
        ctx: Any = None,
    ) -> AsyncIterator[UUID]:
        auth_filters = await Threads.handle_event(
            ctx,
            "delete",
            {
                "thread_id": str(thread_id),
            },
        )

        request = pb.DeleteThreadRequest(
            thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
            filters=auth_filters,
        )

        client = await get_shared_client()
        response = await client.threads.Delete(request)

        deleted_id = UUID(response.value)

        # The Go layer deletes its own checkpoint tables, but custom
        # checkpointers store data elsewhere (e.g. Redis). Clean that up too.
        if USE_CUSTOM_CHECKPOINTER:
            checkpointer = await api_checkpointer.get_checkpointer()
            await checkpointer.adelete_thread(str(deleted_id))

        async def generate_result():
            yield deleted_id

        return generate_result()

    @staticmethod
    async def prune(
        thread_ids: Sequence[str] | Sequence[UUID],
        strategy: Literal["delete", "keep_latest"] = "delete",
        batch_size: int = 100,
        ctx: Any = None,
    ) -> int:
        """Prune threads via gRPC.

        Args:
            thread_ids: List of thread IDs to prune
            strategy: "delete" to remove entirely, "keep_latest" to prune checkpoints
            batch_size: Batch size for operations
            ctx: Auth context for permission checks

        Returns:
            Number of threads successfully pruned
        """

        if not thread_ids:
            return 0

        str_ids = [str(tid) for tid in thread_ids]
        client = await get_shared_client()

        # Validate delete authorization for all threads before pruning.
        # Auth filters are based on user/action, so we only need to get them once.
        auth_filters = await Threads.handle_event(
            ctx,
            "delete",
            {"thread_ids": str_ids},
        )

        # Only validate access if auth filters are present
        if auth_filters:

            async def validate_thread_access(thread_id: str) -> None:
                request = pb.GetThreadRequest(
                    thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
                    filters=auth_filters,
                )
                await client.threads.Get(request)

            await asyncio.gather(*[validate_thread_access(tid) for tid in str_ids])

        if strategy == "delete":
            # threads.Delete() handles Go-side checkpoint cleanup, but custom
            # checkpointers store data elsewhere. Clean that up too.
            checkpointer = (
                await api_checkpointer.get_checkpointer()
                if USE_CUSTOM_CHECKPOINTER
                else None
            )

            async def _delete_thread(tid: str) -> bool:
                try:
                    await client.threads.Delete(
                        pb.DeleteThreadRequest(
                            thread_id=pb.UUID(value=_normalize_uuid(tid)),
                            filters=auth_filters,
                        )
                    )
                except Exception:
                    await logger.aexception("Failed to delete thread.", thread_id=tid)
                    return False
                if checkpointer is not None:
                    try:
                        await checkpointer.adelete_thread(tid)
                    except Exception:
                        await logger.awarning(
                            "Failed to clean up custom checkpointer data for deleted thread.",
                            thread_id=tid,
                        )
                return True

            processed = 0
            for i in range(0, len(str_ids), batch_size):
                batch = str_ids[i : i + batch_size]
                results = await asyncio.gather(*[_delete_thread(tid) for tid in batch])
                processed += sum(results)
        else:
            stub = client.checkpointer
            processed = 0
            for i in range(0, len(str_ids), batch_size):
                batch = str_ids[i : i + batch_size]
                try:
                    request = checkpointer_pb2.PruneRequest(
                        thread_ids=batch,
                        strategy=checkpointer_pb2.PruneRequest.PruneStrategy.KEEP_LATEST,
                    )
                    await stub.Prune(request)
                    processed += len(batch)
                except Exception:
                    await logger.aexception("Failed to prune thread. Skipping batch.")

        return processed

    @staticmethod
    async def copy(
        conn,  # Not used
        thread_id: UUID | str,
        ctx: Any = None,
    ) -> AsyncIterator[Thread]:
        auth_filters = await Threads.handle_event(
            ctx,
            "read",
            {
                "thread_id": str(thread_id),
            },
        )
        # Validate that the user also has create permissions
        # Filters will be the same as the read filters, so we can toss these
        await Threads.handle_event(
            ctx,
            "create",
            {
                "thread_id": str(thread_id),
            },
        )

        request = pb.CopyThreadRequest(
            thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
            filters=auth_filters,
        )

        client = await get_shared_client()
        response = await client.threads.Copy(request)

        thread = proto_to_thread(response)

        # The Go layer copies checkpoint data from the Postgres checkpoints
        # table, but custom checkpointers store data elsewhere (e.g. Redis).
        # Call the custom checkpointer's acopy_thread to copy that data too.
        if USE_CUSTOM_CHECKPOINTER:
            checkpointer = await api_checkpointer.get_checkpointer()
            await checkpointer.acopy_thread(str(thread_id), str(thread["thread_id"]))

        async def generate_result():
            yield thread

        return generate_result()

    @staticmethod
    async def set_status(
        conn,  # Not used in gRPC implementation
        thread_id: UUID | str,
        checkpoint: dict[str, Any] | None,
        exception: BaseException | dict[str, Any] | None,
        expected_status: ThreadStatus | Sequence[ThreadStatus] | None = None,
    ) -> None:
        """Set thread status via gRPC.

        Args:
            conn: Not used (required for interface compatibility)
            thread_id: Thread ID
            checkpoint: Checkpoint payload containing values, next, tasks, etc.
            exception: Exception to store on thread (BaseException or serialized dict)
            expected_status: Expected current status(es) for optimistic locking
        """
        request_kwargs: dict[str, Any] = {
            "thread_id": pb.UUID(value=_normalize_uuid(thread_id)),
        }

        # Map checkpoint to proto (with optional encryption)
        checkpoint_proto = await _thread_status_checkpoint_to_proto(checkpoint)
        if checkpoint_proto is not None:
            request_kwargs["checkpoint"] = checkpoint_proto

        # Map exception to JSON bytes (with optional encryption)
        encrypted_exception = await _encrypt_thread_field(exception, "error")
        if encrypted_exception:
            request_kwargs["exception_json"] = json_dumpb(encrypted_exception)

        # Map expected_status to enum values
        if expected_status:
            if isinstance(expected_status, str):
                expected_status = [expected_status]
            status_enums = []
            for status in expected_status:
                mapped = THREAD_STATUS_TO_PB.get(status)
                if mapped is not None:
                    status_enums.append(mapped)
            if status_enums:
                request_kwargs["expected_status"] = status_enums

        enc_ctx = build_encryption_context("thread")
        if enc_ctx is not None:
            request_kwargs["encryption_context"] = enc_ctx

        client = await get_shared_client()
        await client.threads.SetStatus(pb.SetThreadStatusRequest(**request_kwargs))

    @staticmethod
    async def set_joint_status(
        conn,  # Not used in gRPC implementation
        thread_id: UUID | str,
        run_id: UUID | str,
        run_status: str,  # RunStatus enum value or "rollback"
        graph_id: str,
        checkpoint: dict[str, Any] | None = None,
        exception: BaseException | dict[str, Any] | None = None,
    ) -> None:
        """Set thread and run status atomically via gRPC.

        This is used to update both thread and run status in a single atomic
        operation, minimizing round trips and ensuring consistency.

        Args:
            conn: Not used (required for interface compatibility)
            thread_id: Thread ID
            run_id: Run ID
            run_status: New run status (e.g., "pending", "running", "error",
                        "success", "rollback"). "rollback" is a special value
                        that deletes the run and its checkpoints.
            graph_id: Graph ID to store in thread metadata
            checkpoint: Checkpoint payload containing values, next, tasks, etc.
            exception: Exception to store on thread (BaseException or serialized dict)
        """
        request_kwargs: dict[str, Any] = {
            "thread_id": pb.UUID(value=_normalize_uuid(thread_id)),
            "run_id": pb.UUID(value=_normalize_uuid(run_id)),
            "run_status": run_status,
            "graph_id": graph_id,
        }

        # Map checkpoint to proto if provided (reuses same helper as set_status)
        checkpoint_proto = await _thread_status_checkpoint_to_proto(checkpoint)
        if checkpoint_proto is not None:
            request_kwargs["checkpoint"] = checkpoint_proto

        # Map exception to JSON bytes
        encrypted_exception = await _encrypt_thread_field(exception, "error")
        if encrypted_exception:
            request_kwargs["exception_json"] = json_dumpb(encrypted_exception)

        enc_ctx = build_encryption_context("thread")
        if enc_ctx is not None:
            request_kwargs["encryption_context"] = enc_ctx

        client = await get_shared_client()
        await client.threads.SetJointStatus(
            pb.SetThreadJointStatusRequest(**request_kwargs)
        )

    @staticmethod
    async def get_graph_id(
        thread_id: UUID | str,
    ) -> str | None:
        """Get the graph ID for the latest run in a thread."""
        request = pb.GetGraphIDRequest(
            thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
        )

        client = await get_shared_client()
        response = await client.threads.GetGraphID(request)

        return response.graph_id if response.graph_id else None

    class Stream(Authenticated):
        """Stream operations for threads."""

        resource = "threads"

        @staticmethod
        async def join(
            thread_id: UUID | str,
            *,
            stream_modes: list[str] | None = None,
            last_event_id: str | None = None,
            ctx: Any = None,
        ):
            """Stream events from a thread via gRPC.

            Args:
                thread_id: Thread ID to stream events from
                stream_modes: Optional list of stream modes to filter by
                last_event_id: Optional last event ID for resumable streaming

            Yields:
                Tuples of (event_bytes, message_bytes, stream_id_bytes|None)
            """
            async for (
                event_bytes,
                message_bytes,
                stream_id_bytes,
                _run_id,
            ) in Threads.Stream.join_event_streaming(
                thread_id,
                stream_modes=stream_modes,
                last_event_id=last_event_id,
                ctx=ctx,
            ):
                yield (event_bytes, message_bytes, stream_id_bytes)

        @staticmethod
        async def join_event_streaming(
            thread_id: UUID | str,
            *,
            stream_modes: list[str] | None = None,
            last_event_id: str | None = None,
            ctx: Any = None,
        ):
            """Stream thread events with their run id for protocol demux."""
            auth_filters = await Threads.Stream.handle_event(
                ctx,
                "read",
                Auth.types.ThreadsRead(thread_id=UUID(str(thread_id))),
            )

            stream_modes_pb = _map_thread_stream_modes(stream_modes)

            request = pb.StreamThreadRequest(
                thread_id=pb.UUID(value=_normalize_uuid(thread_id)),
                filters=auth_filters if auth_filters else [],
                stream_modes=stream_modes_pb or [],
            )
            if last_event_id is not None:
                request.last_event_id = last_event_id

            client = await get_shared_client()

            try:
                async for event in client.threads.Stream(request):
                    # Convert protobuf StreamEvent to tuple format
                    event_bytes = event.event_type.encode("utf-8")
                    message_bytes = event.message
                    stream_id_bytes = (
                        event.stream_id.encode("utf-8")
                        if event.HasField("stream_id")
                        else None
                    )
                    run_id = event.run_id if event.HasField("run_id") else None

                    # Transform error events from gRPC format to older Python format
                    if event.event_type == "error":
                        message_bytes = transform_grpc_error_event(message_bytes)

                    yield (event_bytes, message_bytes, stream_id_bytes, run_id)
            except Exception as e:
                if isinstance(e, AioRpcError):
                    _handle_grpc_error(e)
                raise

    class State(Authenticated):
        # treat this like threads resource
        resource = "threads"

        @staticmethod
        async def get(
            conn,  # Still needed for checkpointer
            config: dict[str, Any],
            subgraphs: bool,
            ctx: Any = None,
        ) -> StateSnapshot:
            """Get state snapshot for a thread (*internal only*, no auth)."""
            checkpointer = await api_checkpointer.get_checkpointer(
                conn=conn, unpack_hook=_msgpack_ext_hook_to_json
            )

            thread_id = config["configurable"]["thread_id"]

            async with conn.pipeline():
                thread, checkpoint_iter, graph_id = await asyncio.gather(
                    Threads.get(
                        conn,
                        thread_id,
                        ctx=ctx,
                        read_mask_paths=THREAD_METADATA_READ_MASK_PATHS,
                    ),
                    checkpointer.aget_iter(config),
                    Threads.get_graph_id(thread_id),
                )

            thread = await fetchone(thread)
            metadata = json_loads(thread["metadata"])
            thread_config = json_loads(thread["config"])
            thread_config = {
                **thread_config,
                "configurable": {
                    **thread_config.get("configurable", {}),
                    **config.get("configurable", {}),
                },
            }
            graph_id = graph_id or metadata.get("graph_id")

            if graph_id:
                # format latest checkpoint for response
                checkpointer.latest_iter = checkpoint_iter
                async with get_graph(
                    graph_id,
                    thread_config,
                    checkpointer=checkpointer,
                    store=(await api_store.get_store()),
                    access_context="threads.read",
                ) as graph:
                    return await graph.aget_state(config, subgraphs=subgraphs)
            else:
                _kwargs: dict[str, Any] = {
                    "values": {},
                    "next": tuple(),
                    "config": None,
                    "metadata": None,
                    "created_at": None,
                    "parent_config": None,
                    "tasks": tuple(),
                }
                _kwargs.update(_snapshot_defaults())
                return StateSnapshot(**_kwargs)

        @staticmethod
        async def post(
            conn,  # Still needed for checkpointer and run count check
            config: dict[str, Any],
            values: Any,
            as_node: str | None = None,
            ctx: Any = None,
        ) -> ThreadUpdateResponse:
            """Update thread state."""
            thread_id = UUID(config["configurable"]["thread_id"])
            filters = await Threads.State.handle_event(
                ctx,
                "update",
                Auth.types.ThreadsUpdate(thread_id=thread_id),
            )

            checkpointer = await api_checkpointer.get_checkpointer(
                conn=conn, use_direct_connection=True
            )

            async with conn.pipeline():
                thread, checkpoint_iter, graph_id, run_count = await asyncio.gather(
                    Threads.get(
                        conn,
                        thread_id,
                        ctx=ctx,
                        filters=filters,
                        read_mask_paths=THREAD_METADATA_READ_MASK_PATHS,
                    ),
                    checkpointer.aget_iter(config),
                    Threads.get_graph_id(thread_id),
                    Runs.count(thread_id=thread_id, statuses=["pending", "running"]),
                )

            thread = await fetchone(thread)
            metadata = json_loads(thread["metadata"])
            thread_config = json_loads(thread["config"])
            graph_id = graph_id or metadata.get("graph_id")

            # Check if thread is busy before allowing state update
            if run_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail="Thread is busy with a running job. Cannot update state.",
                )

            if graph_id:
                # update state
                config["configurable"].setdefault("graph_id", graph_id)
                checkpointer.latest_iter = checkpoint_iter
                async with AsyncExitStack() as stack:
                    graph = await stack.enter_async_context(
                        get_graph(
                            graph_id,
                            thread_config,
                            checkpointer=checkpointer,
                            store=(await api_store.get_store()),
                            access_context="threads.update",
                        )
                    )
                    await stack.enter_async_context(conn.transaction())
                    next_config = await graph.aupdate_state(
                        config, values, as_node=as_node
                    )
                    # update thread values
                    state = await Threads.State.get(
                        conn, config, subgraphs=False, ctx=ctx
                    )
                    await Threads.set_status(
                        conn,
                        thread_id,
                        state_snapshot_to_thread_state(state),
                        None,
                        # Accept if NOT busy
                        expected_status=("interrupted", "idle", "error"),
                    )

                    event_data = {
                        "state": state_snapshot_to_thread_state(state),
                        "thread_id": str(thread_id),
                    }
                    await Runs.Stream.publish(
                        "*",
                        "state_update",
                        json_dumpb(event_data),
                        thread_id=thread_id,
                        resumable=True,
                    )

                    return {
                        "checkpoint": next_config["configurable"],
                        # below are deprecated
                        **next_config,
                        "checkpoint_id": next_config["configurable"]["checkpoint_id"],
                    }
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Thread '{thread['thread_id']}' has no assigned graph ID. This usually occurs when no runs have been made on this particular thread."
                    " This operation requires a graph ID. Please ensure a run has been made for the thread or manually update the thread metadata (by setting the 'graph_id' field) before running this operation.",
                )

        @staticmethod
        async def bulk(
            conn,  # Still needed for checkpointer
            config: dict[str, Any],
            supersteps: Any,
            ctx: Any = None,
        ) -> ThreadUpdateResponse:
            """Update a thread with a batch of state updates."""
            thread_id = UUID(config["configurable"]["thread_id"])
            filters = await Threads.State.handle_event(
                ctx,
                "update",
                Auth.types.ThreadsUpdate(thread_id=thread_id),
            )

            checkpointer = await api_checkpointer.get_checkpointer(conn=conn)

            async with conn.pipeline():
                thread, graph_id = await asyncio.gather(
                    Threads.get(
                        conn,
                        thread_id,
                        ctx=ctx,
                        filters=filters,
                        read_mask_paths=THREAD_METADATA_READ_MASK_PATHS,
                    ),
                    Threads.get_graph_id(config["configurable"]["thread_id"]),
                )
            thread = await fetchone(thread)
            thread_config = json_loads(thread["config"])
            metadata = json_loads(thread["metadata"])
            graph_id = graph_id or metadata.get("graph_id")

            if graph_id:
                # update state
                config["configurable"].setdefault("graph_id", graph_id)
                config["configurable"].setdefault("checkpoint_ns", "")

                async with AsyncExitStack() as stack:
                    graph = await stack.enter_async_context(
                        get_graph(
                            graph_id,
                            thread_config,
                            checkpointer=checkpointer,
                            store=(await api_store.get_store()),
                            access_context="threads.update",
                        )
                    )

                    await stack.enter_async_context(conn.transaction())
                    next_config = await graph.abulk_update_state(
                        config,
                        [
                            [
                                StateUpdate(
                                    (
                                        map_cmd(update.get("command"))
                                        if update.get("command")
                                        else update.get("values")
                                    ),
                                    update.get("as_node"),
                                )
                                for update in superstep.get("updates", [])
                            ]
                            for superstep in supersteps
                        ],
                    )

                    # update thread values
                    state = await Threads.State.get(
                        conn, config, subgraphs=False, ctx=ctx
                    )

                    await Threads.set_status(
                        conn,
                        thread_id,
                        state_snapshot_to_thread_state(state),
                        None,
                    )

                    event_data = {
                        "state": state_snapshot_to_thread_state(state),
                        "thread_id": str(thread_id),
                    }
                    await Runs.Stream.publish(
                        "*",
                        "state_update",
                        json_dumpb(event_data),
                        thread_id=thread_id,
                        resumable=True,
                    )

                    return ThreadUpdateResponse(checkpoint=next_config["configurable"])
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Thread '{thread['thread_id']}' has no assigned graph ID. This usually occurs when no runs have been made on this particular thread."
                    " This operation requires a graph ID. Please ensure a run has been made for the thread or manually update the thread metadata (by setting the 'graph_id' field) before running this operation.",
                )

        @staticmethod
        async def list(
            conn,  # Still needed for checkpointer
            *,
            config: dict[str, Any],
            limit: int = 1,
            before: Any = None,
            metadata: Any = None,
            ctx: Any = None,
        ) -> list[StateSnapshot]:
            """Get the history of a thread."""
            async with conn.pipeline():
                thread, graph_id = await asyncio.gather(
                    Threads.get(
                        conn,
                        config["configurable"]["thread_id"],
                        ctx=ctx,
                        read_mask_paths=THREAD_METADATA_READ_MASK_PATHS,
                    ),
                    Threads.get_graph_id(config["configurable"]["thread_id"]),
                )
            thread = await fetchone(thread)
            thread_metadata = json_loads(thread["metadata"])
            thread_config = json_loads(thread["config"])
            thread_config = {
                **thread_config,
                "configurable": {
                    **thread_config.get("configurable", {}),
                    **config.get("configurable", {}),
                },
            }
            graph_id = graph_id or thread_metadata.get("graph_id")

            if graph_id:
                async with get_graph(
                    graph_id,
                    thread_config,
                    checkpointer=await api_checkpointer.get_checkpointer(
                        conn=conn, unpack_hook=_msgpack_ext_hook_to_json
                    ),
                    store=(await api_store.get_store()),
                    access_context="threads.read",
                ) as graph:
                    return [
                        c
                        async for c in graph.aget_state_history(
                            config,
                            limit=limit,
                            filter=metadata,
                            before=(
                                {"configurable": {"checkpoint_id": before}}
                                if isinstance(before, str)
                                else before
                            ),
                        )
                    ]
            else:
                return []
