"""gRPC-based runs operations."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from contextlib import asynccontextmanager
from datetime import UTC
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from google.protobuf.empty_pb2 import Empty  # ty: ignore[unresolved-import]
from grpc import StatusCode
from grpc.aio import EOF, AioRpcError
from langgraph_grpc_common.conversion.config import config_from_proto
from langgraph_grpc_common.proto import (
    core_api_pb2 as pb,
)
from langgraph_grpc_common.proto import (
    enum_cancel_run_action_pb2 as enum_cancel_run_action,
)
from langgraph_grpc_common.proto import (
    enum_control_signal_pb2 as enum_control_signal,
)
from langgraph_grpc_common.proto import (
    enum_multitask_strategy_pb2 as enum_multitask_strategy,
)
from langgraph_grpc_common.proto import (
    enum_run_status_pb2 as enum_run_status,
)
from langgraph_grpc_common.proto import (
    enum_stream_mode_pb2 as enum_stream_mode,
)
from langgraph_sdk import Auth
from starlette.exceptions import HTTPException

from langgraph_api.asyncio import ValueEvent
from langgraph_api.auth.custom import handle_event as auth_handle_event
from langgraph_api.config import (
    STREAM_PUBLISH_RETRY_BACKOFF_FACTOR,
    STREAM_PUBLISH_RETRY_INITIAL_INTERVAL_SECS,
    STREAM_PUBLISH_RETRY_JITTER,
    STREAM_PUBLISH_RETRY_MAX_DURATION_SECS,
    STREAM_PUBLISH_RETRY_MAX_INTERVAL_SECS,
)
from langgraph_api.errors import UserInterrupt, UserRollback
from langgraph_api.graph import SYSTEM_ASSISTANT_IDS
from langgraph_api.grpc.client import get_shared_client
from langgraph_api.grpc.ops import (
    GRPC_STATUS_TO_HTTP_STATUS,
    Authenticated,
    _filters_to_proto,
    _handle_grpc_error,
    _static_interrupt_config_from_proto,
    build_encryption_context,
    extract_encryption_context,
    grpc_error_guard,
    transform_grpc_error_event,
)
from langgraph_api.serde import (
    json_dumpb,
    json_dumpb_optional,
    json_loads_optional,
)
from langgraph_api.utils import get_auth_ctx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from langgraph_api.schema import (
        IfNotExists,
        MetadataInput,
        MultitaskStrategy,
        PoolStats,
        QueueStats,
        Run,
        RunSelectField,
        RunStatus,
    )


RUN_STATUS_TO_PB = {
    "pending": enum_run_status.pending,
    "running": enum_run_status.running,
    "error": enum_run_status.error,
    "success": enum_run_status.success,
    "timeout": enum_run_status.timeout,
    "interrupted": enum_run_status.interrupted,
    # This is a pseudo-status that is not exposed to the user
    # but is used internally to indicate a rollback.
    # We should never return it from the API, as it should never be persisted.
    "rollback": enum_run_status.rollback,
}

RUN_STATUS_FROM_PB = {v: k for k, v in RUN_STATUS_TO_PB.items()}

CANCEL_STATUS_TO_PB = {
    "pending": pb.CancelRunStatus.CANCEL_RUN_STATUS_PENDING,
    "running": pb.CancelRunStatus.CANCEL_RUN_STATUS_RUNNING,
    "all": pb.CancelRunStatus.CANCEL_RUN_STATUS_ALL,
}


def _map_run_status(status: RunStatus | None) -> enum_run_status.RunStatus | None:
    """Map string status to protobuf enum."""
    return None if status is None else RUN_STATUS_TO_PB.get(status)


MULTITASK_STRATEGY_TO_PB = {
    "reject": enum_multitask_strategy.reject,
    "interrupt": enum_multitask_strategy.interrupt,
    "rollback": enum_multitask_strategy.rollback,
    "enqueue": enum_multitask_strategy.enqueue,
}

MULTITASK_STRATEGY_FROM_PB = {v: k for k, v in MULTITASK_STRATEGY_TO_PB.items()}

STREAM_MODE_TO_PB = {
    "unknown": enum_stream_mode.unknown,
    "values": enum_stream_mode.values,
    "updates": enum_stream_mode.updates,
    "checkpoints": enum_stream_mode.checkpoints,
    "tasks": enum_stream_mode.tasks,
    "debug": enum_stream_mode.debug,
    "messages": enum_stream_mode.messages,
    "custom": enum_stream_mode.custom,
    "events": enum_stream_mode.events,
    "messages-tuple": enum_stream_mode.messages_tuple,
    "tools": enum_stream_mode.tools,
    "lifecycle": enum_stream_mode.lifecycle,
}

STREAM_MODE_FROM_PB = {
    **{v: k for k, v in STREAM_MODE_TO_PB.items()},
    # This isn't actually a valid stream mode (it's just a placeholder
    # in the protobuf definition), so if we receive it from the gRPC
    # server for some reason, we should suppress it to avoid exposing it
    # to the user.
    enum_stream_mode.unknown: None,
}


logger = structlog.stdlib.get_logger(__name__)


class GrpcRetryableException(Exception):
    """Exception indicating a gRPC error that should trigger a run retry."""

    pass


class StreamPublishException(Exception):
    """Exception raised when stream event publish fails."""

    def __init__(self, status_code: int, detail: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"stream publish failed ({status_code}): {detail}")


GRPC_RETRIABLE_STATUS_CODES = (
    StatusCode.UNAVAILABLE,
    StatusCode.DEADLINE_EXCEEDED,
)

PUBLISH_RETRIABLE_STATUS_CODES = frozenset(
    {
        StatusCode.CANCELLED,
        StatusCode.UNAVAILABLE,
        StatusCode.DEADLINE_EXCEEDED,
        StatusCode.RESOURCE_EXHAUSTED,
        StatusCode.ABORTED,
        StatusCode.INTERNAL,
    }
)


def _raise_stream_publish_exception(error: AioRpcError) -> None:
    http_status = GRPC_STATUS_TO_HTTP_STATUS.get(
        error.code(),
        HTTPStatus.INTERNAL_SERVER_ERROR,
    )
    detail = str(error.details() or "") or None
    raise StreamPublishException(
        status_code=int(http_status),
        detail=detail,
    ) from error


class GrpcStreamHandler:
    """Handler for a run stream (lifecycle: subscribe -> join -> close)."""

    def __init__(self, run_id: UUID, thread_id: UUID):
        self.run_id = run_id
        self.thread_id = thread_id
        self._stream = None
        self._closed = False

    async def start(self) -> None:
        """Open the bidirectional stream and send a subscribe request."""
        if self._stream is not None:
            return

        client = await get_shared_client()
        self._stream = client.runs.Stream()

        subscribe_msg = pb.StreamRunClientMessage(
            subscribe=pb.SubscribeRunRequest(
                thread_id=pb.UUID(value=str(self.thread_id)),
                run_id=pb.UUID(value=str(self.run_id)),
            )
        )
        await self._stream.write(subscribe_msg)

        # Wait for subscription confirmation
        subscribed_event = await self._stream.read()
        if subscribed_event.event_type == "control":
            control_signal = subscribed_event.message.decode("utf-8")
            if control_signal == "subscribed":
                await logger.adebug(
                    "Subscription confirmed",
                    run_id=self.run_id,
                    thread_id=self.thread_id,
                )
            else:
                await logger.awarning(
                    "Unexpected control signal during subscription",
                    run_id=self.run_id,
                    thread_id=self.thread_id,
                    signal=control_signal,
                )
        else:
            await logger.awarning(
                "Unexpected event type during subscription",
                run_id=self.run_id,
                thread_id=self.thread_id,
                event_type=subscribed_event.event_type,
            )

    async def join(
        self,
        *,
        auth_filters: list[pb.AuthFilter],
        stream_modes: list[str] | None = None,
        ignore_run_not_found: bool = False,
        cancel_on_disconnect: bool = False,
        last_event_id: str | None = None,
    ):
        """Send JoinRunRequest and yield events.

        Yields tuples of (event_bytes, message_bytes, stream_id_bytes|None).
        """
        if self._stream is None:
            raise RuntimeError("Stream not started. Call start() first.")
        if self._closed:
            raise RuntimeError("Stream already closed.")

        join_kwargs: dict[str, Any] = {
            "filters": auth_filters if auth_filters else [],
            "ignore_run_not_found": ignore_run_not_found,
            "cancel_on_disconnect": cancel_on_disconnect,
        }
        if stream_modes:
            join_kwargs["stream_modes"] = stream_modes
        if last_event_id is not None:
            join_kwargs["last_event_id"] = last_event_id

        # Send JoinRunRequest
        join_msg = pb.StreamRunClientMessage(join=pb.JoinRunRequest(**join_kwargs))
        await self._stream.write(join_msg)

        # Half-close: signal we're done sending client messages.
        # Required for bidirectional streaming so the server knows to stop
        # waiting for more messages and can begin its shutdown sequence.
        await self._stream.done_writing()

        # Yield events from the stream using read() instead of async iteration
        # (cannot mix read() from start() with async for)
        while True:
            event = await self._stream.read()
            # read() returns EOF when stream ends
            if event == EOF:
                break

            # Convert protobuf StreamEvent to tuple format
            event_bytes = event.event_type.encode("utf-8")
            message_bytes = event.message
            stream_id_bytes = (
                event.stream_id.encode("utf-8") if event.HasField("stream_id") else None
            )

            # Transform error events from gRPC format to older Python format
            if event.event_type == "error":
                message_bytes = transform_grpc_error_event(message_bytes)

            yield (event_bytes, message_bytes, stream_id_bytes)

    async def close(self) -> None:
        """Close the stream and clean up resources."""
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.cancel()
            self._stream = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


def _map_multitask_strategy(
    strategy: MultitaskStrategy | None,
) -> enum_multitask_strategy.MultitaskStrategy | None:
    """Map string multitask strategy to protobuf enum."""
    return None if strategy is None else MULTITASK_STRATEGY_TO_PB.get(strategy)


def _map_if_not_exists(
    if_not_exists: IfNotExists | None,
) -> pb.CreateRunBehavior | None:
    """Map if_not_exists string to protobuf enum."""
    if if_not_exists is None:
        return None
    return (
        pb.CreateRunBehavior.CREATE_THREAD_IF_THREAD_NOT_EXISTS
        if if_not_exists == "create"
        else pb.CreateRunBehavior.REJECT_RUN_IF_THREAD_NOT_EXISTS
    )


def _map_stream_modes(
    stream_mode: list[str] | None,
) -> list[enum_stream_mode.StreamMode]:
    """Map stream mode string(s) to protobuf enum list (filtering invalid modes)."""
    if stream_mode is None:
        return []

    result = []
    for mode in stream_mode:
        proto_mode = STREAM_MODE_TO_PB.get(mode)
        if proto_mode is None:
            sanitized = str(mode)[:50] + ("..." if len(str(mode)) > 50 else "")
            logger.error("Got invalid stream mode '%s', ignoring", sanitized)
        else:
            result.append(proto_mode)

    return result


def proto_to_run(proto_run: pb.Run) -> Run:
    """Convert protobuf Run to dictionary format."""
    return {
        "run_id": UUID(proto_run.run_id.value)
        if proto_run.HasField("run_id")
        else None,
        "thread_id": UUID(proto_run.thread_id.value)
        if proto_run.HasField("thread_id")
        else None,
        "assistant_id": UUID(proto_run.assistant_id.value)
        if proto_run.HasField("assistant_id")
        else None,
        "created_at": proto_run.created_at.ToDatetime(tzinfo=UTC)
        if proto_run.HasField("created_at")
        else None,
        "updated_at": proto_run.updated_at.ToDatetime(tzinfo=UTC)
        if proto_run.HasField("updated_at")
        else None,
        "status": RUN_STATUS_FROM_PB.get(proto_run.status, "pending"),
        "metadata": json_loads_optional(proto_run.metadata.value)
        if proto_run.HasField("metadata")
        else {},
        "kwargs": _proto_kwargs_to_dict(proto_run.kwargs)
        if proto_run.HasField("kwargs")
        else {},
        "multitask_strategy": MULTITASK_STRATEGY_FROM_PB.get(
            proto_run.multitask_strategy
        ),
    }


def _proto_kwargs_to_dict(kwargs: pb.RunKwargs) -> dict:
    """Convert protobuf RunKwargs to dictionary format."""
    result: dict = {
        "input": json_loads_optional(kwargs.input_json)
        if kwargs.HasField("input_json")
        else None,
        "config": dict(config_from_proto(kwargs.config))
        if kwargs.HasField("config")
        else None,
        "context": json_loads_optional(kwargs.context_json)
        if kwargs.HasField("context_json")
        else None,
        "command": json_loads_optional(kwargs.command_json)
        if kwargs.HasField("command_json")
        else None,
        "stream_mode": [STREAM_MODE_FROM_PB.get(mode) for mode in kwargs.stream_mode]
        if kwargs.stream_mode
        else None,
        "interrupt_before": _static_interrupt_config_from_proto(kwargs.interrupt_before)
        if kwargs.HasField("interrupt_before")
        else None,
        "interrupt_after": _static_interrupt_config_from_proto(kwargs.interrupt_after)
        if kwargs.HasField("interrupt_after")
        else None,
        "webhook": kwargs.webhook if kwargs.HasField("webhook") else None,
        "feedback_keys": list(kwargs.feedback_keys) if kwargs.feedback_keys else None,
        "temporary": kwargs.temporary if kwargs.HasField("temporary") else False,
        "subgraphs": kwargs.subgraphs if kwargs.HasField("subgraphs") else False,
        "resumable": kwargs.resumable if kwargs.HasField("resumable") else False,
        "checkpoint_during": kwargs.checkpoint_during
        if kwargs.HasField("checkpoint_during")
        else True,
        "durability": kwargs.durability if kwargs.HasField("durability") else None,
    }
    return result


def _filter_run_fields(run: Run, select: list[RunSelectField] | None) -> dict[str, Any]:
    """Filter run fields based on select list.

    Returns the original run if no fields are provided."""
    if not select:
        return run
    return {field: run[field] for field in select if field in run}


@grpc_error_guard
class Runs(Authenticated):
    """gRPC-based runs operations."""

    # Auth for runs is applied at the thread level.
    resource = "threads"

    @staticmethod
    async def search(
        conn,  # Not used in gRPC implementation
        thread_id: UUID,
        *,
        limit: int = 10,
        offset: int = 0,
        status: RunStatus | None = None,
        select: list[RunSelectField] | None = None,
        ctx: Any = None,
    ) -> AsyncIterator[Run]:
        """List all runs by thread."""
        auth_filters = await Runs.handle_event(
            ctx,
            "search",
            Auth.types.ThreadsSearch(thread_id=thread_id, metadata={}),
        )

        request_kwargs: dict[str, Any] = {
            "filters": auth_filters,
            "thread_id": pb.UUID(value=str(thread_id)),
            "limit": limit,
            "offset": offset,
        }

        mapped_status = _map_run_status(status)
        if mapped_status is not None:
            request_kwargs["status"] = mapped_status

        if select:
            request_kwargs["select"] = select

        client = await get_shared_client()
        response = await client.runs.Search(pb.SearchRunsRequest(**request_kwargs))

        runs = [proto_to_run(run) for run in response.runs]

        async def generate_results():
            for run in runs:
                yield _filter_run_fields(run, select)

        return generate_results()

    @staticmethod
    async def count(
        *,
        thread_id: UUID | str,
        statuses: list[str] | None = None,
    ) -> int:
        """Count runs matching criteria.

        This is an internal method with no auth - used for checking
        if a thread has pending/running runs.

        Args:
            thread_id: Thread ID to count runs for
            statuses: Optional list of statuses to filter by (e.g., ["pending", "running"])

        Returns:
            Count of matching runs
        """
        request = pb.CountRunsRequest(
            thread_id=pb.UUID(value=str(thread_id)),
            statuses=statuses or [],
        )

        client = await get_shared_client()
        response = await client.runs.Count(request)

        return int(response.count)

    @staticmethod
    async def get(
        conn,  # Not used in gRPC implementation
        run_id: UUID,
        *,
        thread_id: UUID,
        ctx: Any = None,
    ) -> AsyncIterator[Run]:
        """Get a run by ID."""
        auth_filters = await Runs.handle_event(
            ctx,
            "read",
            Auth.types.ThreadsRead(run_id=run_id, thread_id=thread_id),
        )

        request = pb.GetRunRequest(
            run_id=pb.UUID(value=str(run_id)),
            thread_id=pb.UUID(value=str(thread_id)),
            filters=auth_filters,
        )

        client = await get_shared_client()
        response = await client.runs.Get(request)

        run = proto_to_run(response)

        async def generate_result():
            yield run

        return generate_result()

    @staticmethod
    async def delete(
        conn,  # Not used in gRPC implementation
        run_id: UUID,
        *,
        thread_id: UUID,
        ctx: Any = None,
    ) -> AsyncIterator[UUID]:
        """Delete a run by ID."""
        auth_filters = await Runs.handle_event(
            ctx,
            "delete",
            Auth.types.ThreadsDelete(run_id=run_id, thread_id=thread_id),
        )

        request = pb.DeleteRunRequest(
            run_id=pb.UUID(value=str(run_id)),
            thread_id=pb.UUID(value=str(thread_id)),
            filters=auth_filters,
        )

        client = await get_shared_client()
        response = await client.runs.Delete(request)

        deleted_id = UUID(response.value)

        async def generate_result():
            yield deleted_id

        return generate_result()

    @staticmethod
    async def put(
        conn,  # Not used in gRPC implementation
        assistant_id: UUID,
        kwargs: dict,
        *,
        thread_id: UUID | None = None,
        user_id: str | None = None,
        run_id: UUID | None = None,
        status: RunStatus | None = "pending",
        metadata: MetadataInput,
        prevent_insert_if_inflight: bool,
        multitask_strategy: MultitaskStrategy = "reject",
        if_not_exists: IfNotExists = "reject",
        after_seconds: int = 0,
        ctx: Any = None,
    ) -> AsyncIterator[Run]:
        """Create a run."""
        metadata = metadata or {}
        kwargs = kwargs or {}
        temporary = kwargs.get("temporary", False)

        create_run_value = Auth.types.RunsCreate(
            thread_id=None if temporary else thread_id,
            assistant_id=assistant_id,
            run_id=run_id,
            status=status,
            metadata=metadata,
            prevent_insert_if_inflight=prevent_insert_if_inflight,
            multitask_strategy=multitask_strategy,
            if_not_exists=if_not_exists,
            after_seconds=after_seconds,
            kwargs=kwargs,
        )
        auth_filters = await Runs.handle_event(
            ctx,
            "create_run",
            create_run_value,
        )
        # Automatically enforce assistant ownership for non-system assistants
        # by calling the user's assistant read auth handler.
        assistant_auth_filters: list[pb.AuthFilter] = []
        if str(assistant_id) not in SYSTEM_ASSISTANT_IDS:
            ctx_ = ctx or get_auth_ctx()
            if ctx_ is not None:
                auth_ctx = Auth.types.AuthContext(
                    resource="assistants",
                    action="read",
                    user=ctx_.user,
                    permissions=ctx_.permissions,
                )
                read_value = Auth.types.AssistantsRead(assistant_id=str(assistant_id))
                raw = await auth_handle_event(auth_ctx, read_value)
                if raw:
                    assistant_auth_filters = _filters_to_proto(raw)

        kwargs_json_bytes = json_dumpb(kwargs)
        request_kwargs: dict[str, Any] = {
            "assistant_id": pb.UUID(value=str(assistant_id)),
            "kwargs_json": kwargs_json_bytes,
            "thread_filters": auth_filters,
            "assistant_filters": assistant_auth_filters,
        }

        if thread_id is not None:
            request_kwargs["thread_id"] = pb.UUID(value=str(thread_id))
        if user_id is not None:
            request_kwargs["user_id"] = user_id
        if run_id is not None:
            request_kwargs["run_id"] = pb.UUID(value=str(run_id))

        mapped_status = _map_run_status(status)
        if mapped_status is not None:
            request_kwargs["status"] = mapped_status
        if metadata:
            request_kwargs["metadata_json"] = json_dumpb_optional(metadata)
        if prevent_insert_if_inflight:
            request_kwargs["prevent_insert_if_inflight"] = prevent_insert_if_inflight

        mapped_strategy = _map_multitask_strategy(multitask_strategy)
        if mapped_strategy is not None:
            request_kwargs["multitask_strategy"] = mapped_strategy

        mapped_if_not_exists = _map_if_not_exists(if_not_exists)
        if mapped_if_not_exists is not None:
            request_kwargs["if_not_exists"] = mapped_if_not_exists

        if after_seconds > 0:
            request_kwargs["after_seconds"] = int(after_seconds)

        # Build encryption context for Go layer
        enc_ctx = build_encryption_context("run")
        if enc_ctx is not None:
            request_kwargs["encryption_context"] = enc_ctx

        client = await get_shared_client()
        response = await client.runs.Create(pb.CreateRunRequest(**request_kwargs))

        async def generate_result():
            for run in response.runs:
                yield proto_to_run(run)

        return generate_result()

    @staticmethod
    async def cancel(
        conn,  # Not used in gRPC implementation
        run_ids: Sequence[UUID] | None = None,
        *,
        action: Literal["interrupt", "rollback"] = "interrupt",
        thread_id: UUID | None = None,
        status: Literal["pending", "running", "all"] | None = None,
        ctx: Any = None,
    ) -> None:
        """Cancel runs.

        Must provide either:
        1) thread_id + run_ids, or
        2) a status (pending, running, all).
        """
        if status is not None:
            if thread_id is not None or run_ids is not None:
                raise HTTPException(
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    detail="Cannot specify 'thread_id' or 'run_ids' when using 'status'",
                )
        elif thread_id is None or run_ids is None:
            raise HTTPException(
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                detail="Please provide a thread_id and run_ids, or a status to cancel",
            )

        auth_filters = await Runs.handle_event(
            ctx,
            "update",
            Auth.types.ThreadsUpdate(
                thread_id=thread_id,
                action=action,
                metadata={"run_ids": run_ids, "status": status},
            ),
        )

        action_enum = (
            enum_cancel_run_action.rollback
            if action == "rollback"
            else enum_cancel_run_action.interrupt
        )

        request_kwargs: dict[str, Any] = {
            "filters": auth_filters,
            "action": action_enum,
        }

        if status is not None:
            request_kwargs["status"] = pb.CancelStatusTarget(
                status=CANCEL_STATUS_TO_PB[status]
            )
        else:
            request_kwargs["run_ids"] = pb.CancelRunIdsTarget(
                thread_id=pb.UUID(value=str(thread_id)),
                run_ids=[pb.UUID(value=str(rid)) for rid in run_ids],
            )

        client = await get_shared_client()
        await client.runs.Cancel(pb.CancelRunRequest(**request_kwargs))

    @staticmethod
    async def stats(conn) -> QueueStats:
        """Get queue statistics (not exposed via API, no auth)."""
        client = await get_shared_client()
        response = await client.runs.Stats(Empty())

        return {
            "n_pending": response.n_pending,
            "n_running": response.n_running,
            "pending_runs_wait_time_max_secs": (
                response.pending_runs_wait_time_max_secs
                if response.HasField("pending_runs_wait_time_max_secs")
                else None
            ),
            "pending_runs_wait_time_med_secs": (
                response.pending_runs_wait_time_med_secs
                if response.HasField("pending_runs_wait_time_med_secs")
                else None
            ),
            "pending_unblocked_runs_wait_time_max_secs": (
                response.pending_unblocked_runs_wait_time_max_secs
                if response.HasField("pending_unblocked_runs_wait_time_max_secs")
                else None
            ),
        }

    @staticmethod
    @grpc_error_guard
    async def pool_stats() -> PoolStats:
        """Get connection pool stats from the Core API (Go) for metrics aggregation."""
        client = await get_shared_client()
        response = await client.runs.PoolStats(Empty())
        out = {}
        if response.HasField("postgres"):
            p = response.postgres
            out["postgres"] = {
                "pool_max": p.pool_max,
                "pool_size": p.pool_size,
                "pool_available": p.pool_available,
                "requests_queued": p.requests_queued,
                "requests_errors": p.requests_errors,
            }
        if response.HasField("redis"):
            r = response.redis
            out["redis"] = {
                "idle_connections": r.idle_connections,
                "in_use_connections": r.in_use_connections,
                "max_connections": r.max_connections,
            }
        return out

    @staticmethod
    async def set_status(
        conn,  # Not used in gRPC implementation
        run_id: UUID,
        status: RunStatus,
    ) -> None:
        """Set the status of a run (not exposed via API, no auth)."""
        mapped_status = _map_run_status(status)
        if mapped_status is None:
            return

        request = pb.SetRunStatusRequest(
            run_id=pb.UUID(value=str(run_id)),
            status=mapped_status,
        )

        client = await get_shared_client()
        await client.runs.SetStatus(request)

    @staticmethod
    async def sweep() -> list[UUID]:
        """Sweep runs that have been in running state for too long (not exposed via API, no auth)."""
        client = await get_shared_client()
        response = await client.runs.Sweep(Empty())

        return [UUID(uuid_pb.value) for uuid_pb in response.run_ids]

    @staticmethod
    async def _mark_done(run_id: UUID, thread_id: UUID, resumable: bool) -> None:
        """Mark a run as done by signaling control and publishing to stream.

        Internal method used by workers. Not exposed via API, no auth.
        """
        request = pb.MarkRunDoneRequest(
            run_id=pb.UUID(value=str(run_id)),
            thread_id=pb.UUID(value=str(thread_id)),
            resumable=resumable,
        )

        client = await get_shared_client()
        await client.runs.MarkDone(request)

    @staticmethod
    async def next(
        wait: bool, limit: int = 1
    ) -> AsyncIterator[tuple[Run, int, dict[str, Any] | None]]:
        """Get the next run from the queue, the attempt number, and encryption context.

        1 is the first attempt, 2 is the first retry, etc.

        Not exposed via API, no auth.

        Returns:
            AsyncIterator of (run, attempt, encryption_context) tuples
        """
        request = pb.NextRunRequest(wait=wait, limit=limit)

        client = await get_shared_client()
        response = await client.runs.Next(request)

        for run_with_attempt in response.runs:
            run = proto_to_run(run_with_attempt.run)
            encryption_context = extract_encryption_context(run_with_attempt)
            yield run, run_with_attempt.attempt, encryption_context

    class Stream(Authenticated):
        """Stream operations for runs."""

        resource = "threads"

        @staticmethod
        async def subscribe(
            run_id: UUID,
            thread_id: UUID | None = None,
        ) -> GrpcStreamHandler:
            """Subscribe to a run stream via bidirectional gRPC.

            Opens the stream and sends SubscribeRunRequest immediately so the
            server starts buffering events. Returns a handler for join().

            Args:
                run_id: The run ID to stream
                thread_id: The thread ID (required for gRPC)

            Returns:
                GrpcStreamHandler for use with join()
            """
            if thread_id is None:
                raise ValueError("thread_id is required for gRPC streaming")

            handler = GrpcStreamHandler(run_id, thread_id)
            await handler.start()
            return handler

        @staticmethod
        async def join(
            run_id: UUID,
            *,
            stream_channel: Any,  # GrpcStreamHandler, but Any for API compatibility
            thread_id: UUID,
            ignore_404: bool = False,
            cancel_on_disconnect: bool = False,
            stream_mode: list[str] | None = None,
            last_event_id: str | None = None,
            ctx: Any = None,
        ):
            """Join the run stream and start receiving events.

            Sends JoinRunRequest with auth filters and config, then yields events.

            Yields tuples of (event_bytes, message_bytes, stream_id_bytes|None).
            """
            handler: GrpcStreamHandler = stream_channel
            auth_filters = await Runs.Stream.handle_event(
                ctx,
                "read",
                Auth.types.ThreadsRead(run_id=run_id, thread_id=thread_id),
            )

            stream_modes_pb = _map_stream_modes(stream_mode)

            try:
                async for event_bytes, message_bytes, stream_id_bytes in handler.join(
                    auth_filters=auth_filters,
                    stream_modes=stream_modes_pb or None,
                    ignore_run_not_found=ignore_404,
                    cancel_on_disconnect=cancel_on_disconnect,
                    last_event_id=last_event_id,
                ):
                    yield (event_bytes, message_bytes, stream_id_bytes)
            except AioRpcError as e:
                # Special handling for NOT_FOUND when ignore_404 is set
                if e.code() == StatusCode.NOT_FOUND and ignore_404:
                    return  # Return empty stream
                # Convert all other gRPC errors to HTTP exceptions
                _handle_grpc_error(e)
            finally:
                await handler.close()

        @staticmethod
        async def check_run_stream_auth(
            run_id: UUID,
            thread_id: UUID,
            ctx: Any = None,
        ) -> None:
            """Check auth for streaming a run.

            Verifies the run exists and auth passes before starting the stream.
            This ensures 404/403 errors are raised before the streaming response.
            """
            auth_filters = await Runs.Stream.handle_event(
                ctx,
                "read",
                Auth.types.ThreadsRead(run_id=run_id, thread_id=thread_id),
            )

            client = await get_shared_client()
            request = pb.GetRunRequest(
                run_id=pb.UUID(value=str(run_id)),
                thread_id=pb.UUID(value=str(thread_id)),
                filters=auth_filters,
            )

            try:
                await client.runs.Get(request)
            except AioRpcError as e:
                _handle_grpc_error(e)

        @staticmethod
        async def publish(
            run_id: UUID | str | None,
            event: str,
            message: bytes,
            thread_id: UUID | str,
            *,
            resumable: bool = False,
        ) -> None:
            """Publish a message to the run stream via gRPC.

            Args:
                run_id: The run ID (* or `None` for thread-level events)
                event: Event type (e.g. 'values', 'updates', 'messages', etc.)
                message: Event payload (serialized JSON or raw bytes)
                thread_id: The thread ID
                resumable: If true, message will be cached with TTL for resumable streaming
            """
            run_id_pb = (
                None if run_id is None or run_id == "*" else pb.UUID(value=str(run_id))
            )
            client = await get_shared_client()
            request = pb.PublishStreamEventRequest(
                run_id=run_id_pb,
                thread_id=pb.UUID(value=str(thread_id)),
                event_type=event,
                message=message,
                resumable=resumable,
            )
            max_duration_secs = max(0.0, STREAM_PUBLISH_RETRY_MAX_DURATION_SECS)
            initial_interval_secs = max(
                0.001, STREAM_PUBLISH_RETRY_INITIAL_INTERVAL_SECS
            )
            max_interval_secs = max(
                initial_interval_secs, STREAM_PUBLISH_RETRY_MAX_INTERVAL_SECS
            )
            backoff_factor = max(1.0, STREAM_PUBLISH_RETRY_BACKOFF_FACTOR)
            jitter = min(1.0, max(0.0, STREAM_PUBLISH_RETRY_JITTER))

            deadline = time.monotonic() + max_duration_secs
            interval_secs = initial_interval_secs
            attempt = 1

            while True:
                try:
                    await client.runs.Publish(request)
                    return
                except AioRpcError as e:
                    if e.code() not in PUBLISH_RETRIABLE_STATUS_CODES:
                        _raise_stream_publish_exception(e)

                    now = time.monotonic()
                    remaining_secs = deadline - now
                    if remaining_secs <= 0:
                        _raise_stream_publish_exception(e)

                    jitter_multiplier = 1 + random.uniform(-jitter, jitter)
                    retry_delay_secs = max(0.0, interval_secs * jitter_multiplier)
                    retry_delay_secs = min(retry_delay_secs, remaining_secs)

                    await logger.awarning(
                        "Retrying stream publish after retriable gRPC error",
                        run_id=str(run_id) if run_id is not None else "*",
                        thread_id=str(thread_id),
                        event_type=event,
                        attempt=attempt,
                        retry_in_secs=retry_delay_secs,
                        grpc_status=e.code().name,
                    )

                    await asyncio.sleep(retry_delay_secs)
                    interval_secs = min(
                        interval_secs * backoff_factor,
                        max_interval_secs,
                    )
                    attempt += 1

    @staticmethod
    def enter(
        run_id: UUID,
        thread_id: UUID,
        loop: Any,  # unused, for API compatibility
        resumable: bool,
    ):
        """Enter a run context manager for execution.

        Opens a streaming Enter RPC that:
        1. Starts heartbeat and Redis cancellation listening on the server
        2. Streams back control signals (interrupt/rollback) when they occur
        3. Returns a ValueEvent that will be set on interrupt/rollback

        Args:
            run_id: The run ID
            thread_id: The thread ID
            loop: The event loop (unused in gRPC implementation)
            resumable: Whether the run is resumable

        Yields:
            ValueEvent that will be set with UserInterrupt() or UserRollback() if cancelled
        """
        from langgraph_runtime.retry import RETRIABLE_EXCEPTIONS  # noqa: PLC0415

        @asynccontextmanager
        async def _enter_impl():
            done = ValueEvent()

            # Open streaming Enter RPC
            client = await get_shared_client()
            enter_request = pb.EnterRunRequest(
                run_id=pb.UUID(value=str(run_id)),
                thread_id=pb.UUID(value=str(thread_id)),
                resumable=resumable,
            )
            enter_stream = client.runs.Enter(enter_request)

            # Background task to listen for control signals from the stream
            async def listen_for_signals():
                try:
                    async for event in enter_stream:
                        if event.action == enum_control_signal.interrupt:
                            logger.info(
                                "Received interrupt signal from gRPC stream",
                                run_id=run_id,
                                thread_id=thread_id,
                            )
                            done.set(UserInterrupt())
                            break
                        elif event.action == enum_control_signal.rollback:
                            logger.info(
                                "Received rollback signal from gRPC stream",
                                run_id=run_id,
                                thread_id=thread_id,
                            )
                            done.set(UserRollback())
                            break
                except Exception as exc:
                    logger.exception(
                        "listen_for_signals failed",
                        run_id=run_id,
                        thread_id=thread_id,
                        exc_info=exc,
                    )
                    done.set(
                        GrpcRetryableException(
                            f"listen_for_signals failed. \nError: {exc!r}"
                        )
                    )
                    raise

            listener: asyncio.Task | None = None
            try:
                listener = asyncio.create_task(listen_for_signals())
                yield done
                # Signal done via gRPC (stops heartbeat and cleanup on server)
                await Runs.mark_done(
                    run_id=run_id, thread_id=thread_id, resumable=resumable
                )
            except GrpcRetryableException:
                logger.info(
                    "Retriable exception, will not signal done",
                    run_id=run_id,
                    thread_id=thread_id,
                )
            except RETRIABLE_EXCEPTIONS:
                logger.info(
                    "Retriable exception, will not signal done",
                    run_id=run_id,
                    thread_id=thread_id,
                )
            except AioRpcError as e:
                if e.code() in GRPC_RETRIABLE_STATUS_CODES:
                    logger.info(
                        "Retriable gRPC error, will not signal done",
                        run_id=run_id,
                        thread_id=thread_id,
                        grpc_code=e.code().name,
                    )
                else:
                    logger.exception(
                        "Non-retriable gRPC error when marking run as done",
                        run_id=run_id,
                        thread_id=thread_id,
                        grpc_code=e.code().name,
                    )
                    raise
            except BaseException:
                logger.exception(
                    "Non-retriable exception when marking run as done",
                    run_id=run_id,
                    thread_id=thread_id,
                )
                raise
            finally:
                # Quiet teardown: cancel listener first, then close stream.
                if listener is not None:
                    listener.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await listener
                if not enter_stream.done():
                    enter_stream.cancel()

        return _enter_impl()

    @staticmethod
    @grpc_error_guard
    async def mark_done(
        run_id: UUID,
        thread_id: UUID,
        resumable: bool,
    ) -> None:
        """Mark a run as done.

        Args:
            run_id: The run ID
            thread_id: The thread ID
            resumable: Whether streaming is resumable
        """
        client = await get_shared_client()
        request = pb.MarkRunDoneRequest(
            run_id=pb.UUID(value=str(run_id)),
            thread_id=pb.UUID(value=str(thread_id)),
            resumable=resumable,
        )
        await client.runs.MarkDone(request)
