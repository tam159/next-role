from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from google.protobuf.empty_pb2 import Empty  # ty: ignore[unresolved-import]
from langgraph_grpc_common.conversion.config import config_from_proto, config_to_proto
from langgraph_grpc_common.proto import core_api_pb2 as pb
from langgraph_grpc_common.proto import (
    enum_cron_on_run_completed_pb2 as cron_orc_pb2,
)
from langgraph_grpc_common.proto import (
    enum_multitask_strategy_pb2 as ms_pb2,
)
from langgraph_sdk import Auth

from langgraph_api.graph import SYSTEM_ASSISTANT_IDS, get_assistant_id
from langgraph_api.grpc.client import get_shared_client
from langgraph_api.grpc.ops import (
    Authenticated,
    _map_sort_order,
    _static_interrupt_config_from_proto,
    build_encryption_context,
    grpc_error_guard,
)
from langgraph_api.grpc.ops.assistants import Assistants
from langgraph_api.grpc.ops.threads import Threads
from langgraph_api.serde import json_dumpb_optional, json_loads_optional
from langgraph_api.utils import get_auth_ctx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph_api.schema import Cron, CronSelectField, MetadataInput


def _ensure_datetime(value: datetime | str | None) -> datetime | None:
    """Coerce an ISO-8601 string (from JSON) to a datetime, passing through datetimes and None."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _on_run_completed_to_str(
    proto_cron: pb.Cron,
) -> str | None:
    """Convert on_run_completed enum to string, or None if unset."""
    if not proto_cron.HasField("on_run_completed"):
        return None
    return cron_orc_pb2.CronOnRunCompleted.Name(proto_cron.on_run_completed)


def _on_run_completed_to_enum(
    value: Literal["delete", "keep"] | None,
) -> cron_orc_pb2.CronOnRunCompleted.ValueType | None:
    """Convert on_run_completed string to proto enum value."""
    if value is None:
        return None
    return cron_orc_pb2.CronOnRunCompleted.Value(value)


def _payload_dict_to_proto(payload: dict) -> pb.CronPayload:
    """Convert a payload dict to a CronPayload proto.

    Simple fields (assistant_id, input, context, metadata, webhook) are placed
    in their typed proto fields. Complex fields (config, interrupts, etc.) and
    any unknown fields go into extra_json as JSON bytes.
    """
    proto_payload = pb.CronPayload()
    if "assistant_id" in payload:
        proto_payload.assistant_id = str(payload["assistant_id"])
    if "input" in payload and payload["input"] is not None:
        proto_payload.input_json = json.dumps(payload["input"]).encode()
    if "context" in payload and payload["context"] is not None:
        proto_payload.context_json = json.dumps(payload["context"]).encode()
    if "metadata" in payload and payload["metadata"] is not None:
        proto_payload.metadata_json = json.dumps(payload["metadata"]).encode()
    if "webhook" in payload and payload["webhook"] is not None:
        proto_payload.webhook = payload["webhook"]
    if "config" in payload and payload["config"] is not None:
        proto_config = config_to_proto(payload["config"])
        if proto_config is not None:
            proto_payload.config.CopyFrom(proto_config)

    # Complex/proto-typed fields and unknowns go into extra_json.
    # The Go side merges extra_json back into the Fragment at the top level.
    simple_keys = {"assistant_id", "input", "context", "metadata", "webhook", "config"}
    for key, val in payload.items():
        if key not in simple_keys and val is not None:
            proto_payload.extra_json[key] = json.dumps(val).encode()

    return proto_payload


def _payload_proto_to_dict(proto_payload: pb.CronPayload) -> dict:
    """Convert a CronPayload proto to a payload dict."""
    result: dict = {}
    if proto_payload.assistant_id:
        result["assistant_id"] = proto_payload.assistant_id
    if proto_payload.input_json:
        result["input"] = json.loads(proto_payload.input_json)
    if proto_payload.context_json:
        result["context"] = json.loads(proto_payload.context_json)
    if proto_payload.metadata_json:
        result["metadata"] = json.loads(proto_payload.metadata_json)
    if proto_payload.HasField("webhook"):
        result["webhook"] = proto_payload.webhook
    if proto_payload.HasField("config"):
        result["config"] = dict(config_from_proto(proto_payload.config))
    if proto_payload.HasField("interrupt_before"):
        result["interrupt_before"] = _static_interrupt_config_from_proto(
            proto_payload.interrupt_before
        )
    if proto_payload.HasField("interrupt_after"):
        result["interrupt_after"] = _static_interrupt_config_from_proto(
            proto_payload.interrupt_after
        )
    if proto_payload.HasField("multitask_strategy"):
        result["multitask_strategy"] = ms_pb2.MultitaskStrategy.Name(
            proto_payload.multitask_strategy
        )
    for key, val in proto_payload.extra_json.items():
        result[key] = json.loads(val)
    return result


def proto_to_cron(proto_cron: pb.Cron) -> Cron:
    """Convert protobuf Cron to dictionary format."""
    thread_id = (
        UUID(proto_cron.thread_id.value) if proto_cron.HasField("thread_id") else None
    )
    end_time = (
        proto_cron.end_time.ToDatetime(tzinfo=UTC)
        if proto_cron.HasField("end_time")
        else None
    )
    user_id = proto_cron.user_id if proto_cron.HasField("user_id") else None

    return {
        "cron_id": UUID(proto_cron.cron_id.value),
        "assistant_id": UUID(proto_cron.assistant_id),
        "thread_id": thread_id,
        "on_run_completed": _on_run_completed_to_str(proto_cron),
        "end_time": end_time,
        "schedule": proto_cron.schedule,
        "timezone": proto_cron.timezone if proto_cron.HasField("timezone") else None,
        "created_at": proto_cron.created_at.ToDatetime(tzinfo=UTC),
        "updated_at": proto_cron.updated_at.ToDatetime(tzinfo=UTC),
        "user_id": user_id,
        "payload": _payload_proto_to_dict(proto_cron.payload),
        "next_run_date": proto_cron.next_run_date.ToDatetime(tzinfo=UTC),
        "metadata": json_loads_optional(proto_cron.metadata_json),
        "enabled": proto_cron.enabled,
    }


_CRONS_SORT_BY_MAPPING: dict[str, pb.CronsSortBy] = {
    "cron_id": pb.CronsSortBy.CRONS_SORT_BY_CRON_ID,
    "assistant_id": pb.CronsSortBy.CRONS_SORT_BY_ASSISTANT_ID,
    "thread_id": pb.CronsSortBy.CRONS_SORT_BY_THREAD_ID,
    "next_run_date": pb.CronsSortBy.CRONS_SORT_BY_NEXT_RUN_DATE,
    "end_time": pb.CronsSortBy.CRONS_SORT_BY_END_TIME,
    "created_at": pb.CronsSortBy.CRONS_SORT_BY_CREATED_AT,
    "updated_at": pb.CronsSortBy.CRONS_SORT_BY_UPDATED_AT,
}


def _map_crons_sort_by(sort_by: str | None) -> pb.CronsSortBy:
    """Map string sort_by to protobuf enum."""
    if not sort_by:
        return pb.CronsSortBy.CRONS_SORT_BY_CREATED_AT

    sort_by_lower = sort_by.lower()
    return _CRONS_SORT_BY_MAPPING.get(
        sort_by_lower, pb.CronsSortBy.CRONS_SORT_BY_CREATED_AT
    )


@grpc_error_guard
class Crons(Authenticated):
    """gRPC-based crons operations."""

    resource = "crons"

    @staticmethod
    async def put(
        conn,  # Not used in gRPC implementation
        *,
        payload: dict,
        schedule: str,
        cron_id: UUID | None = None,
        thread_id: UUID | None = None,
        on_run_completed: Literal["delete", "keep"] | None = None,
        end_time: datetime | str | None = None,
        metadata: dict | None = None,
        enabled: bool,
        timezone: str | None = None,
        ctx: Any = None,
    ) -> AsyncIterator[Cron]:
        """Create cron via gRPC."""
        if "assistant_id" in payload:
            resolved_assistant_id = get_assistant_id(payload["assistant_id"])
            payload = {**payload, "assistant_id": resolved_assistant_id}

        end_time_dt = _ensure_datetime(end_time)
        metadata = metadata if metadata is not None else {}

        # Ensure config.configurable exists in payload before auth handlers
        # run, matching the postgres runtime path.
        config = payload.setdefault("config", {})
        config.setdefault("configurable", {})

        # Extract user_id from auth context
        auth_ctx = get_auth_ctx()
        user_id = auth_ctx.user.identity if auth_ctx is not None else None

        # Cron-scoped auth filters
        cron_request_data = Auth.types.CronsCreate(
            payload=payload,
            schedule=schedule,
            cron_id=cron_id,
            thread_id=thread_id,
            on_run_completed=on_run_completed,
            end_time=end_time_dt,
            metadata=metadata,
        )
        cron_filters = await Crons.handle_event(ctx, "create", cron_request_data)

        assistant_filters: list[Any] = []
        if payload["assistant_id"] not in SYSTEM_ASSISTANT_IDS:
            # Do not populate `metadata` on the AssistantsRead request value:
            # client-supplied cron metadata must not influence the auth
            # decision (defense vs. forged payloads), mirroring Runs.put.
            # The handler is expected to return a filter that is matched
            # against the persisted assistant metadata server-side.
            assistant_request_data = Auth.types.AssistantsRead(
                assistant_id=str(payload["assistant_id"])
            )
            assistant_filters = await Assistants.handle_event(
                ctx, "read", assistant_request_data
            )

        # Thread-scoped auth filters (only when thread_id is provided)
        thread_filters: list[Any] = []
        if thread_id is not None:
            thread_request_data = Auth.types.ThreadsRead(thread_id=thread_id)
            thread_request_data["metadata"] = metadata
            thread_filters = await Threads.handle_event(
                ctx, "read", thread_request_data
            )

        request = pb.CreateCronRequest(
            filters=cron_filters,
            cron_id=pb.UUID(value=str(cron_id)) if cron_id is not None else None,
            thread_id=pb.UUID(value=str(thread_id)) if thread_id is not None else None,
            schedule=schedule,
            payload=_payload_dict_to_proto(payload),
            metadata_json=json_dumpb_optional(metadata or {}),
            on_run_completed=_on_run_completed_to_enum(on_run_completed),
            end_time=end_time_dt,
            enabled=enabled,
            assistant_filters=assistant_filters,
            thread_filters=thread_filters,
            user_id=user_id,
            encryption_context=build_encryption_context("cron"),
            timezone=timezone,
        )

        client = await get_shared_client()
        response = await client.crons.Create(request)

        cron = proto_to_cron(response)

        async def generate_result():
            yield cron

        return generate_result()

    @staticmethod
    async def get(
        conn,  # Not used in gRPC implementation
        cron_id: UUID | str,
        ctx: Any = None,
    ) -> AsyncIterator[Cron]:
        """Get cron by ID via gRPC."""
        auth_filters = await Crons.handle_event(
            ctx, "read", Auth.types.CronsRead(cron_id=cron_id)
        )

        request = pb.GetCronRequest(
            cron_id=pb.UUID(value=str(cron_id)),
            filters=auth_filters,
        )

        client = await get_shared_client()
        response = await client.crons.Get(request)

        cron = proto_to_cron(response)

        async def generate_result():
            yield cron

        return generate_result()

    @staticmethod
    async def search(
        conn,  # Not used in gRPC implementation
        *,
        assistant_id: UUID | None,
        thread_id: UUID | None,
        enabled: bool | None,
        limit: int,
        offset: int,
        sort_by: str | None = None,
        sort_order: str | None = None,
        select: list[CronSelectField] | None = None,
        ctx: Any = None,
        metadata: MetadataInput = None,
    ) -> tuple[AsyncIterator[Cron], int | None]:
        """Search crons via gRPC."""
        auth_filters = await Crons.handle_event(
            ctx,
            "search",
            {
                "assistant_id": assistant_id,
                "thread_id": thread_id,
                "limit": limit,
                "offset": offset,
                "metadata": metadata or {},
            },
        )

        # Thread-scoped auth filters
        if thread_id is not None:
            thread_request_data = Auth.types.ThreadsRead(thread_id=thread_id)
            thread_filters = await Threads.handle_event(
                ctx, "read", thread_request_data
            )
        else:
            thread_filters = await Threads.handle_event(
                ctx, "search", Auth.types.ThreadsSearch()
            )

        request = pb.SearchCronsRequest(
            filters=auth_filters,
            thread_filters=thread_filters,
            assistant_id=pb.UUID(value=str(assistant_id))
            if assistant_id is not None
            else None,
            thread_id=pb.UUID(value=str(thread_id)) if thread_id is not None else None,
            enabled=enabled,
            limit=limit,
            offset=offset,
            sort_by=_map_crons_sort_by(sort_by),
            sort_order=_map_sort_order(sort_order),
            select=select or [],
            metadata_json=json_dumpb_optional(metadata),
        )

        client = await get_shared_client()
        response = await client.crons.Search(request)

        crons = [proto_to_cron(cron) for cron in response.crons]
        cursor = offset + limit if len(crons) == limit else None

        async def generate_results():
            for cron in crons:
                if select:
                    yield {k: v for k, v in cron.items() if k in select}
                else:
                    yield cron

        return generate_results(), cursor

    @staticmethod
    async def update(
        conn,  # Not used in gRPC implementation
        *,
        cron_id: UUID,
        schedule: str | None = None,
        end_time: datetime | str | None = None,
        enabled: bool | None = None,
        on_run_completed: Literal["delete", "keep"] | None = None,
        payload: dict | None = None,
        metadata: dict | None = None,
        timezone: str | None = None,
        ctx: Any = None,
    ) -> AsyncIterator[Cron]:
        """Update cron via gRPC."""
        end_time_dt = _ensure_datetime(end_time)
        metadata = metadata if metadata is not None else {}

        auth_filters = await Crons.handle_event(
            ctx,
            "update",
            {
                "cron_id": cron_id,
                "schedule": schedule,
                "end_time": end_time_dt,
                "enabled": enabled,
                "on_run_completed": on_run_completed,
                "payload": payload,
                "metadata": metadata,
            },
        )

        request = pb.PatchCronRequest(
            cron_id=pb.UUID(value=str(cron_id)),
            filters=auth_filters,
            schedule=schedule,
            end_time=end_time_dt,
            enabled=enabled,
            on_run_completed=_on_run_completed_to_enum(on_run_completed),
            payload=_payload_dict_to_proto(payload) if payload else None,
            metadata_json=json_dumpb_optional(metadata) if metadata else None,
            encryption_context=build_encryption_context("cron"),
            timezone=timezone,
        )

        client = await get_shared_client()
        response = await client.crons.Patch(request)

        cron = proto_to_cron(response)

        async def generate_result():
            yield cron

        return generate_result()

    @staticmethod
    async def delete(
        conn,  # Not used in gRPC implementation
        cron_id: UUID,
        ctx: Any = None,
    ) -> AsyncIterator[UUID]:
        """Delete cron via gRPC."""
        auth_filters = await Crons.handle_event(ctx, "delete", {"cron_id": cron_id})

        request = pb.DeleteCronRequest(
            cron_id=pb.UUID(value=str(cron_id)),
            filters=auth_filters,
        )

        client = await get_shared_client()
        await client.crons.Delete(request)

        async def generate_result():
            yield cron_id

        return generate_result()

    @staticmethod
    async def count(
        conn,  # Not used in gRPC implementation
        *,
        assistant_id: UUID | None = None,
        thread_id: UUID | None = None,
        ctx: Any = None,
        metadata: MetadataInput = None,
    ) -> int:
        """Count crons via gRPC."""
        auth_filters = await Crons.handle_event(
            ctx,
            "search",
            {
                "assistant_id": assistant_id,
                "thread_id": thread_id,
                "metadata": metadata or {},
            },
        )

        # Thread-scoped auth filters
        if thread_id is not None:
            thread_request_data = Auth.types.ThreadsRead(thread_id=thread_id)
            thread_filters = await Threads.handle_event(
                ctx, "read", thread_request_data
            )
        else:
            thread_filters = await Threads.handle_event(
                ctx, "search", Auth.types.ThreadsSearch()
            )

        request = pb.CountCronsRequest(
            filters=auth_filters,
            thread_filters=thread_filters,
            assistant_id=pb.UUID(value=str(assistant_id))
            if assistant_id is not None
            else None,
            thread_id=pb.UUID(value=str(thread_id)) if thread_id is not None else None,
            metadata_json=json_dumpb_optional(metadata),
        )

        client = await get_shared_client()
        response = await client.crons.Count(request)

        return int(response.count)

    @staticmethod
    async def next(
        conn,  # Not used in gRPC implementation
    ) -> AsyncIterator[tuple[Cron, dict | None]]:
        """Get next crons ready to run via gRPC (internal API, no auth filters).

        Yields (cron, encryption_context) tuples.  The encryption context is
        extracted by Go from the raw encrypted payload before decryption
        (which strips it) so the cron scheduler can propagate it to runs.
        ``None`` means no encryption context was present.
        """

        client = await get_shared_client()
        response = await client.crons.Next(Empty())

        for cron_with_now in response.crons:
            cron = proto_to_cron(cron_with_now.cron)
            cron["now"] = cron_with_now.now.ToDatetime(tzinfo=UTC)

            enc_ctx: dict | None = None
            if cron_with_now.HasField("encryption_context"):
                enc_ctx = {
                    k: json.loads(v)
                    for k, v in cron_with_now.encryption_context.metadata.items()
                }
            yield cron, enc_ctx

    @staticmethod
    async def set_next_run_date(
        conn,  # Not used in gRPC implementation
        cron_id: UUID,
        next_run_date: datetime,
    ) -> None:
        """Update next run date for a cron via gRPC (internal API, no auth filters)."""
        request = pb.SetNextRunDateRequest(
            cron_id=pb.UUID(value=str(cron_id)),
            next_run_date=next_run_date,
        )

        client = await get_shared_client()
        await client.crons.SetNextRunDate(request)
