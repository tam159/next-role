import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal, cast
from uuid import UUID

import orjson
import structlog
from starlette.exceptions import HTTPException
from starlette.responses import Response, StreamingResponse

from langgraph_api.asyncio import ValueEvent
from langgraph_api.encryption.context import get_encryption_context
from langgraph_api.encryption.middleware import (
    decrypt_response,
    decrypt_responses,
    encrypt_request,
)
from langgraph_api.encryption.shared import (
    BLOB_ENCRYPTION_CONTEXT_KEY,
    using_aes_encryption,
    using_custom_encryption,
)
from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.graph import _validate_assistant_id
from langgraph_api.models.run import create_valid_run
from langgraph_api.route import ApiRequest, ApiResponse, ApiRoute
from langgraph_api.schema import (
    CRON_ENCRYPTION_FIELDS,
    CRON_FIELDS,
    CRON_PAYLOAD_ENCRYPTION_SUBFIELDS,
    RUN_ENCRYPTION_FIELDS,
    RUN_FIELDS,
)
from langgraph_api.serde import json_dumpb, json_loads
from langgraph_api.sse import EventSourceResponse
from langgraph_api.utils import (
    fetchone,
    get_pagination_headers,
    uuid7,
    validate_select_columns,
    validate_timezone,
    validate_uuid,
)
from langgraph_api.validation import (
    CronCountRequest,
    CronCreate,
    CronPatch,
    CronSearch,
    RunBatchCreate,
    RunCreateStateful,
    RunCreateStateless,
    RunCreateStreamingStateful,
    RunCreateStreamingStateless,
    RunsCancel,
    ThreadCronCreate,
)
from langgraph_api.webhook import validate_webhook_url_or_raise
from langgraph_runtime.database import connect
from langgraph_runtime.retry import retry_db

if IS_POSTGRES_OR_GRPC_BACKEND:
    from langgraph_api.grpc.ops import Crons, Runs, Threads
else:
    from langgraph_runtime.ops import Crons, Runs, Threads

logger = structlog.stdlib.get_logger(__name__)


def parse_stream_mode_param(stream_mode_param: str | None) -> list[str]:
    """Parse stream_mode query parameter. We use this to support the query param format used by the SDK.

    Supports:
    - Single values: "values" -> ["values"]
    - JSON arrays: '["values","messages-tuple","updates"]' -> ["values", "messages-tuple", "updates"]
    - Empty/None: None -> []
    """
    if not stream_mode_param:
        return []

    # Try to parse as JSON array first
    if stream_mode_param.startswith("["):
        try:
            parsed = orjson.loads(stream_mode_param)
            if isinstance(parsed, list):
                return parsed
        except (orjson.JSONDecodeError, ValueError):
            pass
    # Single value
    return [stream_mode_param]


# Type alias for stream handlers (GrpcStreamHandler or ContextQueue).
# Runs is selected at runtime, and the implementations have different
# type signatures, so we use Any for compatibility.
_StreamHandler = Any

_RunResultFallback = Callable[[], Awaitable[bytes]]


def _thread_values_fallback(thread_id: UUID) -> _RunResultFallback:
    async def fetch_thread_values() -> bytes:
        async with connect() as conn:
            thread_iter = await Threads.get(conn, thread_id)
            try:
                row = await anext(thread_iter)
                # Decrypt thread fields (values, interrupts, error) if encryption is enabled
                if IS_POSTGRES_OR_GRPC_BACKEND and not using_aes_encryption():
                    thread = dict(row)
                else:
                    thread = await decrypt_response(
                        dict(row),
                        "thread",
                        ["values", "interrupts", "error"],
                    )
                if row["status"] == "error":
                    return json_dumpb({"__error__": json_loads(thread["error"])})
                if row["status"] == "interrupted":
                    # Surface every stored interrupt rather than only one per thread.
                    try:
                        interrupt_map = json_loads(thread["interrupts"])
                        interrupts: list[Any] = []
                        for interrupt_list in interrupt_map.values():
                            if isinstance(interrupt_list, list):
                                interrupts.extend(interrupt_list)
                        if interrupts:
                            return json_dumpb({"__interrupt__": interrupts})
                    except Exception:
                        # No interrupt, but status is interrupted from a before/after block. Default back to values.
                        pass
                values = json_loads(thread["values"]) if thread["values"] else None
                return json_dumpb(values) if values else b"{}"
            except StopAsyncIteration:
                await logger.awarning(
                    f"No checkpoint found for thread {thread_id}",
                    thread_id=thread_id,
                )
                return b"{}"

    return fetch_thread_values


def _merge_feedback(body: bytes, feedback: bytes | None) -> bytes:
    """Merge feedback URLs into a JSON response body under ``__feedback__``.

    If *feedback* is ``None`` the original *body* is returned unchanged.
    """
    if feedback is None:
        return body
    result = orjson.loads(body)
    result["__feedback__"] = orjson.loads(feedback)
    return orjson.dumps(result)


def _merge_interrupts(chunks: list[bytes]) -> bytes:
    interrupts: list[Any] = []
    for chunk in chunks:
        try:
            payload = orjson.loads(chunk)
        except orjson.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        interrupt_list = payload.get("__interrupt__")
        if isinstance(interrupt_list, list):
            interrupts.extend(interrupt_list)

    return orjson.dumps({"__interrupt__": interrupts})


def _run_result_body(
    *,
    run_id: UUID,
    thread_id: UUID,
    sub: _StreamHandler,
    cancel_on_disconnect: bool = False,
    ignore_404: bool = False,
    fallback: _RunResultFallback | None = None,
    cancel_message: str | None = None,
) -> Callable[[], AsyncIterator[bytes]]:
    last_chunk = ValueEvent()

    async def consume() -> None:
        vchunk: bytes | None = None
        fchunk: bytes | None = None
        interrupt_chunks: list[bytes] = []
        saw_error = False

        try:
            async for mode, chunk, _ in Runs.Stream.join(
                run_id,
                stream_channel=sub,
                cancel_on_disconnect=cancel_on_disconnect,
                thread_id=thread_id,
                ignore_404=ignore_404,
            ):
                if mode == b"values" or (
                    mode == b"updates" and b"__interrupt__" in chunk
                ):
                    vchunk = chunk
                    if b"__interrupt__" in chunk:
                        interrupt_chunks.append(chunk)
                elif mode == b"error":
                    vchunk = orjson.dumps({"__error__": orjson.Fragment(chunk)})
                    saw_error = True
                elif mode == b"feedback":
                    fchunk = chunk

            # Preserve terminal error precedence over interrupt chunks.
            if not saw_error:
                if len(interrupt_chunks) > 1:
                    vchunk = _merge_interrupts(interrupt_chunks)
                elif interrupt_chunks:
                    vchunk = interrupt_chunks[-1]
                elif vchunk is None and fallback is not None:
                    vchunk = await fallback()

            if vchunk is not None:
                last_chunk.set(_merge_feedback(vchunk, fchunk))
            elif fchunk is not None:
                last_chunk.set(orjson.dumps({"__feedback__": orjson.loads(fchunk)}))
            else:
                last_chunk.set(b"{}")
        finally:
            await sub.__aexit__(None, None, None)

    # keep the connection open by sending whitespace every 5 seconds
    # leading whitespace will be ignored by json parsers
    async def body() -> AsyncIterator[bytes]:
        try:
            stream = asyncio.create_task(consume())
            while True:
                try:
                    if stream.done():
                        # raise stream exception if any
                        stream.result()
                    yield await asyncio.wait_for(last_chunk.wait(), timeout=5)
                    break
                except TimeoutError:
                    yield b"\n"
                except asyncio.CancelledError:
                    if cancel_message is not None:
                        stream.cancel(cancel_message)
                    else:
                        stream.cancel()
                    await stream
                    raise
        finally:
            # Make sure to always clean up the pubsub
            await sub.__aexit__(None, None, None)

    return body


@retry_db
async def create_run(request: ApiRequest):
    """Create a run."""
    thread_id = request.path_params["thread_id"]
    payload = await request.json(RunCreateStateful)

    async with connect() as conn:
        run = await create_valid_run(
            conn,
            thread_id,
            payload,
            request.headers,
            request_start_time=request.scope.get("request_start_time_ms"),
        )
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        run = await decrypt_response(run, "run", RUN_ENCRYPTION_FIELDS)
    return ApiResponse(
        run,
        headers={"Content-Location": f"/threads/{thread_id}/runs/{run['run_id']}"},
    )


@retry_db
async def create_stateless_run(request: ApiRequest):
    """Create a run."""
    payload = await request.json(RunCreateStateless)

    async with connect() as conn:
        run = await create_valid_run(
            conn,
            None,
            payload,
            request.headers,
            request_start_time=request.scope.get("request_start_time_ms"),
        )
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        run = await decrypt_response(run, "run", RUN_ENCRYPTION_FIELDS)
    return ApiResponse(
        run,
        headers={"Content-Location": f"/runs/{run['run_id']}"},
    )


async def create_stateless_run_batch(request: ApiRequest):
    """Create a batch of stateless backround runs."""
    batch_payload = await request.json(RunBatchCreate)
    async with connect() as conn:
        # barrier so all queries are sent before fetching any results
        barrier = asyncio.Barrier(len(batch_payload))
        coros = [
            create_valid_run(
                conn,
                None,
                payload,
                request.headers,
                barrier,
                request_start_time=request.scope.get("request_start_time_ms"),
            )
            for payload in batch_payload
        ]
        runs = await asyncio.gather(*coros)
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        runs = await decrypt_responses(list(runs), "run", RUN_ENCRYPTION_FIELDS)
    return ApiResponse(runs)


async def stream_run(
    request: ApiRequest,
):
    """Create a run."""
    thread_id = request.path_params["thread_id"]
    payload = await request.json(RunCreateStreamingStateful)
    on_disconnect = payload.get("on_disconnect", "continue")
    run_id = uuid7()

    sub = await Runs.Stream.subscribe(run_id, thread_id)
    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                thread_id,
                payload,
                request.headers,
                run_id=run_id,
                request_start_time=request.scope.get("request_start_time_ms"),
            )
    except Exception:
        # Clean up the stream handler on errors
        await sub.__aexit__(None, None, None)
        raise

    request.scope["run_id"] = str(run["run_id"])

    async def body():
        try:
            async for event, message, stream_id in Runs.Stream.join(
                run["run_id"],
                thread_id=thread_id,
                cancel_on_disconnect=on_disconnect == "cancel",
                stream_channel=cast("_StreamHandler", sub),
            ):
                yield event, message, stream_id
        finally:
            # Clean up the stream handler
            await sub.__aexit__(None, None, None)

    return EventSourceResponse(
        body(),
        headers={
            "Location": f"/threads/{thread_id}/runs/{run['run_id']}/stream",
            "Content-Location": f"/threads/{thread_id}/runs/{run['run_id']}",
        },
    )


async def stream_run_stateless(
    request: ApiRequest,
):
    """Create a stateless run."""
    payload = await request.json(RunCreateStreamingStateless)
    payload["if_not_exists"] = "create"
    on_disconnect = payload.get("on_disconnect", "continue")
    run_id = uuid7()
    thread_id = uuid7()

    sub = await Runs.Stream.subscribe(run_id, thread_id)
    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                str(thread_id),
                payload,
                request.headers,
                run_id=run_id,
                request_start_time=request.scope.get("request_start_time_ms"),
                temporary=True,
            )
    except Exception:
        # Clean up the stream handler on errors
        await sub.__aexit__(None, None, None)
        raise

    request.scope["run_id"] = str(run["run_id"])

    async def body():
        try:
            async for event, message, stream_id in Runs.Stream.join(
                run["run_id"],
                thread_id=run["thread_id"],
                ignore_404=True,
                cancel_on_disconnect=on_disconnect == "cancel",
                stream_channel=cast("_StreamHandler", sub),
            ):
                yield event, message, stream_id
        finally:
            # Clean up the stream handler
            await sub.__aexit__(None, None, None)

    return EventSourceResponse(
        body(),
        headers={
            "Location": f"/runs/{run['run_id']}/stream",
            "Content-Location": f"/runs/{run['run_id']}",
        },
    )


@retry_db
async def wait_run(request: ApiRequest):
    """Create a run, wait for the output."""
    thread_id = request.path_params["thread_id"]
    payload = await request.json(RunCreateStreamingStateful)
    on_disconnect = payload.get("on_disconnect", "continue")
    run_id = uuid7()
    sub = await Runs.Stream.subscribe(run_id, thread_id)
    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                thread_id,
                payload,
                request.headers,
                run_id=run_id,
                request_start_time=request.scope.get("request_start_time_ms"),
            )
    except Exception:
        # Clean up the stream handler on errors
        await sub.__aexit__(None, None, None)
        raise

    request.scope["run_id"] = str(run["run_id"])

    body = _run_result_body(
        run_id=run["run_id"],
        thread_id=run["thread_id"],
        sub=sub,
        cancel_on_disconnect=on_disconnect == "cancel",
        fallback=_thread_values_fallback(thread_id),
    )

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers={
            "Location": f"/threads/{thread_id}/runs/{run['run_id']}/join",
            "Content-Location": f"/threads/{thread_id}/runs/{run['run_id']}",
        },
    )


@retry_db
async def wait_run_stateless(request: ApiRequest):
    """Create a stateless run, wait for the output."""
    payload = await request.json(RunCreateStreamingStateless)
    payload["if_not_exists"] = "create"
    on_disconnect = payload.get("on_disconnect", "continue")
    run_id = uuid7()
    thread_id = uuid7()

    sub = await Runs.Stream.subscribe(run_id, thread_id)
    try:
        async with connect() as conn:
            run = await create_valid_run(
                conn,
                str(thread_id),
                payload,
                request.headers,
                run_id=run_id,
                request_start_time=request.scope.get("request_start_time_ms"),
                temporary=True,
            )
    except Exception:
        # Clean up the stream handler on errors
        await sub.__aexit__(None, None, None)
        raise

    request.scope["run_id"] = str(run["run_id"])

    async def stateless_fallback() -> bytes:
        await logger.awarning(
            "No checkpoint emitted for stateless run",
            run_id=run["run_id"],
            thread_id=run["thread_id"],
        )
        return b"{}"

    body = _run_result_body(
        run_id=run["run_id"],
        thread_id=run["thread_id"],
        sub=sub,
        cancel_on_disconnect=on_disconnect == "cancel",
        ignore_404=True,
        fallback=stateless_fallback,
        cancel_message="Run stream cancelled",
    )

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers={
            "Location": f"/threads/{run['thread_id']}/runs/{run['run_id']}/join",
            "Content-Location": f"/threads/{run['thread_id']}/runs/{run['run_id']}",
        },
    )


@retry_db
async def list_runs(
    request: ApiRequest,
):
    """List all runs for a thread."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    limit = int(request.query_params.get("limit", 10))
    offset = int(request.query_params.get("offset", 0))
    status = request.query_params.get("status")
    select = validate_select_columns(
        request.query_params.getlist("select") or None, RUN_FIELDS
    )

    async with connect() as conn:
        thread, runs = await asyncio.gather(
            Threads.get(conn, thread_id, read_mask_paths=[]),
            Runs.search(
                conn,
                thread_id,
                limit=limit,
                offset=offset,
                status=status,
                select=select,
            ),
        )
    await fetchone(thread)

    # Collect and decrypt runs
    runs_list = [run async for run in runs]
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        runs_list = await decrypt_responses(runs_list, "run", RUN_ENCRYPTION_FIELDS)
    return ApiResponse(runs_list)


@retry_db
async def get_run(request: ApiRequest):
    """Get a run by ID."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")

    async with connect() as conn:
        thread, run = await asyncio.gather(
            Threads.get(conn, thread_id, read_mask_paths=[]),
            Runs.get(
                conn,
                run_id,
                thread_id=thread_id,
            ),
        )
    await fetchone(thread)
    run_dict = await fetchone(run)

    # Decrypt run metadata and kwargs
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        run_dict = await decrypt_response(run_dict, "run", RUN_ENCRYPTION_FIELDS)

    return ApiResponse(run_dict)


@retry_db
async def join_run(request: ApiRequest):
    """Wait for a run to finish."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")

    # A touch redundant, but to meet the existing signature of join, we need to throw any 404s before we enter the streaming body
    await Runs.Stream.check_run_stream_auth(run_id, thread_id)
    sub = await Runs.Stream.subscribe(run_id, thread_id)
    body = _run_result_body(
        run_id=run_id,
        thread_id=thread_id,
        sub=sub,
        fallback=_thread_values_fallback(thread_id),
    )

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers={
            "Location": f"/threads/{thread_id}/runs/{run_id}/join",
            "Content-Location": f"/threads/{thread_id}/runs/{run_id}",
        },
    )


@retry_db
async def join_run_stream(request: ApiRequest):
    """Wait for a run to finish."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    cancel_on_disconnect_str = request.query_params.get("cancel_on_disconnect", "false")
    cancel_on_disconnect = cancel_on_disconnect_str.lower() in {"true", "yes", "1"}
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")

    stream_mode_param = request.query_params.get("stream_mode")
    stream_mode = parse_stream_mode_param(stream_mode_param)

    last_event_id = request.headers.get("last-event-id") or None

    async def body():
        sub = await Runs.Stream.subscribe(run_id, thread_id)
        try:
            async for event, message, stream_id in Runs.Stream.join(
                run_id,
                thread_id=thread_id,
                cancel_on_disconnect=cancel_on_disconnect,
                stream_channel=cast("_StreamHandler", sub),
                stream_mode=stream_mode,
                last_event_id=last_event_id,
            ):
                yield event, message, stream_id
        finally:
            # Clean up the stream handler
            await sub.__aexit__(None, None, None)

    return EventSourceResponse(
        body(),
        headers={
            "Location": f"/threads/{thread_id}/runs/{run_id}/stream",
            "Content-Location": f"/threads/{thread_id}/runs/{run_id}",
        },
    )


@retry_db
async def cancel_run(
    request: ApiRequest,
):
    """Cancel a run."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")
    wait_str = request.query_params.get("wait", "false")
    wait = wait_str.lower() in {"true", "yes", "1"}
    action_str = request.query_params.get("action", "interrupt")
    action = cast(
        "Literal['interrupt', 'rollback']",
        action_str if action_str in {"interrupt", "rollback"} else "interrupt",
    )

    sub = await Runs.Stream.subscribe(run_id, thread_id) if wait else None
    try:
        async with connect() as conn:
            await Runs.cancel(
                conn,
                [run_id],
                action=action,
                thread_id=thread_id,
            )
    except Exception:
        if sub is not None:
            await sub.__aexit__(None, None, None)
        raise
    if not wait or sub is None:
        return Response(status_code=202)

    body = _run_result_body(
        run_id=run_id,
        thread_id=thread_id,
        sub=sub,
    )

    return StreamingResponse(
        body(),
        media_type="application/json",
        headers={
            "Location": f"/threads/{thread_id}/runs/{run_id}/join",
            "Content-Location": f"/threads/{thread_id}/runs/{run_id}",
        },
    )


@retry_db
async def cancel_runs(
    request: ApiRequest,
):
    """Cancel a run."""
    body = await request.json(RunsCancel)
    status = body.get("status")
    if status:
        status = status.lower()
        if status not in ("pending", "running", "all"):
            raise HTTPException(
                status_code=422,
                detail="Invalid status: must be 'pending', 'running', or 'all'",
            )
        if body.get("thread_id") or body.get("run_ids"):
            raise HTTPException(
                status_code=422,
                detail="When providing a 'status', 'thread_id' and 'run_ids' must be omitted. "
                "The 'status' parameter cancels all runs with the given status, regardless of thread or run ID.",
            )
        run_ids = None
        thread_id = None
    else:
        thread_id = body.get("thread_id")
        run_ids = body.get("run_ids")
        validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
        for rid in run_ids:
            validate_uuid(rid, "Invalid run ID: must be a UUID")
    action_str = request.query_params.get("action", "interrupt")
    action = cast(
        "Literal['interrupt', 'rollback']",
        action_str if action_str in ("interrupt", "rollback") else "interrupt",
    )

    async with connect() as conn:
        await Runs.cancel(
            conn,
            run_ids,
            action=action,
            thread_id=thread_id,
            status=status,
        )
    return Response(status_code=204)


@retry_db
async def delete_run(request: ApiRequest):
    """Delete a run by ID."""
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    validate_uuid(run_id, "Invalid run ID: must be a UUID")

    async with connect() as conn:
        rid = await Runs.delete(
            conn,
            run_id,
            thread_id=thread_id,
        )
    await fetchone(rid)
    return Response(status_code=204)


@retry_db
async def create_cron(request: ApiRequest):
    """Create a cron with new thread."""
    payload = await request.json(CronCreate)
    if webhook := payload.get("webhook"):
        await validate_webhook_url_or_raise(str(webhook))
    _validate_assistant_id(payload.get("assistant_id"))
    timezone = validate_timezone(payload.get("timezone"))

    # Store encryption context at payload root so cron scheduler can extract it
    # regardless of which fields (metadata, input, config, context) are present.
    # Use a separate variable to avoid shadowing the typed payload.
    enc_ctx = get_encryption_context()
    payload_for_encryption: dict = (
        {**payload, BLOB_ENCRYPTION_CONTEXT_KEY: enc_ctx} if enc_ctx else payload
    )

    if IS_POSTGRES_OR_GRPC_BACKEND and using_custom_encryption():
        effective_payload = payload_for_encryption
    else:
        effective_payload = await encrypt_request(
            payload_for_encryption,
            "cron",
            CRON_PAYLOAD_ENCRYPTION_SUBFIELDS,
        )

    enabled = payload.get("enabled", True)

    async with connect(supports_core_api=False) as conn:
        cron = await Crons.put(
            conn,
            thread_id=None,
            on_run_completed=payload.get("on_run_completed", "delete"),
            end_time=payload.get("end_time"),
            schedule=payload.get("schedule"),
            payload=effective_payload,
            metadata=effective_payload.get("metadata"),
            enabled=enabled,
            timezone=timezone,
        )
    cron_dict = await fetchone(cron)
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        cron_dict = await decrypt_response(cron_dict, "cron", CRON_ENCRYPTION_FIELDS)

    return ApiResponse(cron_dict)


@retry_db
async def create_thread_cron(request: ApiRequest):
    """Create a thread specific cron."""
    thread_id = request.path_params["thread_id"]
    validate_uuid(thread_id, "Invalid thread ID: must be a UUID")
    payload = await request.json(ThreadCronCreate)
    if webhook := payload.get("webhook"):
        await validate_webhook_url_or_raise(str(webhook))
    _validate_assistant_id(payload.get("assistant_id"))
    timezone = validate_timezone(payload.get("timezone"))

    # Store encryption context at payload root so cron scheduler can extract it
    # regardless of which fields (metadata, input, config, context) are present.
    # Use a separate variable to avoid shadowing the typed payload.
    enc_ctx = get_encryption_context()
    payload_for_encryption: dict = (
        {**payload, BLOB_ENCRYPTION_CONTEXT_KEY: enc_ctx} if enc_ctx else payload
    )

    if IS_POSTGRES_OR_GRPC_BACKEND and using_custom_encryption():
        effective_payload = payload_for_encryption
    else:
        effective_payload = await encrypt_request(
            payload_for_encryption,
            "cron",
            CRON_PAYLOAD_ENCRYPTION_SUBFIELDS,
        )

    async with connect(supports_core_api=False) as conn:
        cron = await Crons.put(
            conn,
            thread_id=thread_id,
            on_run_completed=None,
            end_time=payload.get("end_time"),
            schedule=payload.get("schedule"),
            payload=effective_payload,
            metadata=effective_payload.get("metadata"),
            enabled=payload.get("enabled", True),
            timezone=timezone,
        )
    cron_dict = await fetchone(cron)
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        cron_dict = await decrypt_response(cron_dict, "cron", CRON_ENCRYPTION_FIELDS)

    return ApiResponse(cron_dict)


@retry_db
async def get_cron(request: ApiRequest) -> ApiResponse:
    """Get a cron by ID."""
    cron_id = request.path_params["cron_id"]
    validate_uuid(cron_id, "Invalid cron ID: must be a UUID")
    async with connect() as conn:
        cron = await Crons.get(conn, cron_id)
    cron_dict = await fetchone(cron)
    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        cron_dict = await decrypt_response(cron_dict, "cron", CRON_ENCRYPTION_FIELDS)
    return ApiResponse(cron_dict)


@retry_db
async def patch_cron(request: ApiRequest):
    """Update a cron by ID."""
    cron_id = request.path_params["cron_id"]
    validate_uuid(cron_id, "Invalid cron ID: must be a UUID")

    payload = await request.json(CronPatch)
    if not payload:
        raise HTTPException(status_code=400, detail="Request body cannot be empty")
    timezone = validate_timezone(payload.get("timezone"))

    if webhook := payload.get("webhook"):
        await validate_webhook_url_or_raise(str(webhook))

    # Encrypt payload subfields before storage
    if IS_POSTGRES_OR_GRPC_BACKEND and using_custom_encryption():
        effective_payload = payload
    else:
        effective_payload = await encrypt_request(
            payload,
            "cron",
            CRON_PAYLOAD_ENCRYPTION_SUBFIELDS,
        )

    async with connect(supports_core_api=False) as conn:
        cron = await Crons.update(
            conn,
            cron_id=cron_id,
            schedule=payload.get("schedule"),
            end_time=payload.get("end_time"),
            enabled=payload.get("enabled"),
            on_run_completed=payload.get("on_run_completed"),
            payload=effective_payload,
            metadata=effective_payload.get("metadata"),
            timezone=timezone,
        )
    cron_dict = await fetchone(cron)

    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        cron_dict = await decrypt_response(cron_dict, "cron", CRON_ENCRYPTION_FIELDS)

    return ApiResponse(cron_dict)


@retry_db
async def delete_cron(request: ApiRequest):
    """Delete a cron by ID."""
    cron_id = request.path_params["cron_id"]
    validate_uuid(cron_id, "Invalid cron ID: must be a UUID")

    try:
        async with connect(supports_core_api=False) as conn:
            cid = await Crons.delete(
                conn,
                cron_id=cron_id,
            )
        await fetchone(cid)
    except Exception as e:
        await logger.aexception("Failed to delete cron", cron_id=cron_id)
        raise e
    return Response(status_code=204)


@retry_db
async def search_crons(request: ApiRequest):
    """List all cron jobs for an assistant"""
    payload = await request.json(CronSearch)
    select = validate_select_columns(payload.get("select") or None, CRON_FIELDS)
    if assistant_id := payload.get("assistant_id"):
        validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    if thread_id := payload.get("thread_id"):
        validate_uuid(thread_id, "Invalid thread ID: must be a UUID")

    offset = int(payload.get("offset", 0))
    async with connect(supports_core_api=False) as conn:
        crons_iter, next_offset = await Crons.search(
            conn,
            assistant_id=assistant_id,
            thread_id=thread_id,
            enabled=payload.get("enabled", None),
            metadata=payload.get("metadata"),
            limit=int(payload.get("limit", 10)),
            offset=offset,
            sort_by=payload.get("sort_by"),
            sort_order=payload.get("sort_order"),
            select=select,
        )
    crons, response_headers = await get_pagination_headers(
        crons_iter, next_offset, offset
    )

    if not IS_POSTGRES_OR_GRPC_BACKEND or using_aes_encryption():
        crons = await decrypt_responses(crons, "cron", CRON_ENCRYPTION_FIELDS)

    return ApiResponse(crons, headers=response_headers)


@retry_db
async def count_crons(request: ApiRequest):
    """Count cron jobs."""
    payload = await request.json(CronCountRequest)
    if assistant_id := payload.get("assistant_id"):
        validate_uuid(assistant_id, "Invalid assistant ID: must be a UUID")
    if thread_id := payload.get("thread_id"):
        validate_uuid(thread_id, "Invalid thread ID: must be a UUID")

    async with connect(supports_core_api=False) as conn:
        count = await Crons.count(
            conn,
            assistant_id=assistant_id,
            thread_id=thread_id,
            metadata=payload.get("metadata"),
        )
    return ApiResponse(count)


runs_routes = [
    ApiRoute("/runs/stream", stream_run_stateless, methods=["POST"]),
    ApiRoute("/runs/wait", wait_run_stateless, methods=["POST"]),
    ApiRoute("/runs", create_stateless_run, methods=["POST"]),
    ApiRoute("/runs/batch", create_stateless_run_batch, methods=["POST"]),
    ApiRoute("/runs/cancel", cancel_runs, methods=["POST"]),
    ApiRoute("/runs/crons", create_cron, methods=["POST"]),
    ApiRoute("/runs/crons/search", search_crons, methods=["POST"]),
    ApiRoute("/runs/crons/count", count_crons, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs/{run_id}/join", join_run, methods=["GET"]),
    ApiRoute(
        "/threads/{thread_id}/runs/{run_id}/stream",
        join_run_stream,
        methods=["GET"],
    ),
    ApiRoute("/threads/{thread_id}/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs/{run_id}", get_run, methods=["GET"]),
    ApiRoute("/threads/{thread_id}/runs/{run_id}", delete_run, methods=["DELETE"]),
    ApiRoute("/threads/{thread_id}/runs/stream", stream_run, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs/wait", wait_run, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs", create_run, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs/crons", create_thread_cron, methods=["POST"]),
    ApiRoute("/threads/{thread_id}/runs", list_runs, methods=["GET"]),
    ApiRoute("/runs/crons/{cron_id}", get_cron, methods=["GET"]),
    ApiRoute("/runs/crons/{cron_id}", patch_cron, methods=["PATCH"]),
    ApiRoute("/runs/crons/{cron_id}", delete_cron, methods=["DELETE"]),
]

runs_routes = [route for route in runs_routes if route is not None]
