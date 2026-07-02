import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.errors import GraphRecursionError
from langgraph.pregel.debug import CheckpointPayload, TaskResultPayload
from starlette.exceptions import HTTPException
from typing_extensions import TypedDict

import langgraph_api.logging as lg_logging
from langgraph_api.auth.custom import SimpleUser, normalize_user
from langgraph_api.config import (
    BG_JOB_MAX_RETRIES,
    BG_JOB_TIMEOUT_SECS,
    USE_CUSTOM_CHECKPOINTER,
)
from langgraph_api.encryption.context import set_encryption_context
from langgraph_api.encryption.middleware import (
    decrypt_response,
    extract_blob_encryption_context,
)
from langgraph_api.errors import UserInterrupt, UserRollback, UserTimeout
from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.graph import restore_dd_trace_context
from langgraph_api.js.errors import RemoteException
from langgraph_api.metadata import incr_runs
from langgraph_api.metrics_datadog import (
    COUNTER_GRAPH_RECURSION_LIMIT_ERROR,
    COUNTER_RUN_ATTEMPT_STARTED,
    COUNTER_RUN_CANCELED_BY_REQUEST,
    COUNTER_RUN_EXCEED_MAX_ATTEMPTS_AT_START,
    COUNTER_RUN_FAILED_AFTER_RETRY,
    COUNTER_RUN_FAILED_RETRIABLE,
    COUNTER_RUN_SET_STATUS_ERROR,
    COUNTER_RUN_SUCCESS,
    LATENCY_RUN_EXECUTION,
    get_datadog_metrics_reporter,
)
from langgraph_api.otel_context import restore_otel_trace_context
from langgraph_api.schema import RUN_KWARGS_ENCRYPTION_SUBFIELDS, Run, StreamMode
from langgraph_api.state import state_snapshot_to_thread_state
from langgraph_api.stream import AnyStream, astream_state, consume
from langgraph_api.utils import with_user
from langgraph_runtime.database import connect
from langgraph_runtime.retry import RETRIABLE_EXCEPTIONS

if IS_POSTGRES_OR_GRPC_BACKEND:
    from langgraph_api.grpc.ops import Runs, Threads
    from langgraph_api.grpc.ops.runs import GrpcRetryableException

    GRPC_RETRIABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
        GrpcRetryableException,
    )
else:
    from langgraph_runtime.ops import Runs, Threads

    GRPC_RETRIABLE_EXCEPTIONS = ()

logger = structlog.stdlib.get_logger(__name__)

ALL_RETRIABLE_EXCEPTIONS = (
    asyncio.CancelledError,
    *RETRIABLE_EXCEPTIONS,
    *GRPC_RETRIABLE_EXCEPTIONS,
)


class WorkerResult(TypedDict):
    checkpoint: CheckpointPayload | None
    status: str | None
    exception: Exception | None
    run: Run
    webhook: str | None
    run_started_at: str
    run_ended_at: str | None


@asynccontextmanager
async def set_auth_ctx_for_run(
    run_kwargs: dict, user_id: str | None = None
) -> AsyncGenerator[None, None]:
    # user_id is a fallback.
    try:
        permissions = (
            run_kwargs["config"]["configurable"].get("langgraph_auth_permissions") or []
        )
        user = run_kwargs["config"]["configurable"].get("langgraph_auth_user")
        if not user:
            user = SimpleUser(user_id) if user_id is not None else None
        else:
            user = normalize_user(user)
        # Reapply normalization to the kwargs
        run_kwargs["config"]["configurable"]["langgraph_auth_user"] = user
    except Exception:
        logger.warning("Failed to extract user from run kwargs", exc_info=True)
        user = SimpleUser(user_id) if user_id is not None else None
        permissions = None
    if user is not None:
        async with with_user(user, permissions):
            yield None
    else:
        yield None


async def worker(
    run: Run,
    attempt: int,
    main_loop: asyncio.AbstractEventLoop,
    *,
    encryption_context: dict[str, Any] | None = None,
) -> WorkerResult:
    reporter = get_datadog_metrics_reporter()
    run_id = run["run_id"]
    if attempt == 1:
        incr_runs()

    # Extract and set encryption context BEFORE decryption (decrypt_response strips this key)
    if encryption_context is None:
        encryption_context = extract_blob_encryption_context(
            run["kwargs"].get("config")
        )
    if encryption_context:
        set_encryption_context(encryption_context)

    # Decrypt kwargs fields FIRST, before any access to run["kwargs"]
    run["kwargs"] = await decrypt_response(
        run["kwargs"], "run", RUN_KWARGS_ENCRYPTION_SUBFIELDS
    )

    checkpoint: CheckpointPayload | None = None
    exception: Exception | asyncio.CancelledError | None = None
    status: str | None = None
    webhook = run["kwargs"].get("webhook", None)
    request_created_at: int | None = run["kwargs"]["config"]["configurable"].get(
        "__request_start_time_ms__"
    )
    after_seconds = run["kwargs"]["config"]["configurable"].get("__after_seconds__", 0)
    run_started_at_dt = datetime.now(UTC)
    run_started_at = run_started_at_dt.isoformat()
    run_ended_at_dt: datetime | None = None
    run_ended_at: str | None = None

    # Note that "created_at" is inclusive of the `after_seconds`
    run_creation_ms = (
        int(
            ((run["created_at"].timestamp() - after_seconds) * 1_000)
            - request_created_at
        )
        if request_created_at is not None
        else None
    )
    temporary = run["kwargs"].get("temporary", False)
    resumable = run["kwargs"].get("resumable", False)
    run_created_at_dt = run["created_at"]
    run_created_at = run["created_at"].isoformat()
    thread_id = str(run.get("thread_id"))
    reporter.inc_counter(
        COUNTER_RUN_ATTEMPT_STARTED,
    )
    lg_logging.set_logging_context(
        {
            "run_id": str(run_id),
            "run_attempt": attempt,
            "thread_id": thread_id,
            "assistant_id": str(run.get("assistant_id")),
            "graph_id": str(_get_graph_id(run)),
            "request_id": str(_get_request_id(run)),
        }
    )
    run_stream_started_at_dt = datetime.now(UTC)
    await logger.ainfo(
        "Starting background run",
        run_started_at=run_started_at,
        run_creation_ms=run_creation_ms,
        run_queue_ms=ms(run_started_at_dt, run["created_at"]),
        run_stream_start_ms=ms(run_stream_started_at_dt, run_started_at_dt),
        temporary=temporary,
        resumable=resumable,
    )

    def on_checkpoint(checkpoint_arg: CheckpointPayload | None):
        nonlocal checkpoint
        if checkpoint_arg is None:
            logger.warning("Null checkpoint received")
        checkpoint = checkpoint_arg

    def on_task_result(task_result: TaskResultPayload):
        if checkpoint is not None:
            for task in checkpoint["tasks"]:
                if task["id"] == task_result["id"]:
                    task.update(task_result)
                    break

    # Wrap the graph execution to separate user errors from server errors
    async def wrap_user_errors(
        stream: AnyStream,
        run_id: str | uuid.UUID,
        resumable: bool,
        stream_modes: set[StreamMode],
    ):
        try:
            await consume(
                stream, run_id, resumable, stream_modes, thread_id=run["thread_id"]
            )
        except Exception as e:
            if not isinstance(e, UserRollback | UserInterrupt):
                logger.exception(
                    f"Run encountered an error in graph: {type(e)}({e})",
                    run_id=str(run_id),
                    thread_id=thread_id,
                )
            # TimeoutError is a special case where we rely on asyncio.wait_for to timeout runs
            # Convert user TimeoutErrors to a custom class so we can distinguish and later convert back
            if isinstance(e, TimeoutError):
                raise UserTimeout(e) from e
            raise

    async with Runs.enter(run_id, run["thread_id"], main_loop, resumable) as done:
        # attempt the run
        try:
            if attempt > BG_JOB_MAX_RETRIES:
                reporter.inc_counter(COUNTER_RUN_EXCEED_MAX_ATTEMPTS_AT_START)
                await logger.aerror(
                    "Run exceeded max attempts",
                    run_id=str(run["run_id"]),
                    run_completed_in_ms=(
                        int((time.time() * 1_000) - request_created_at)
                        if request_created_at is not None
                        else None
                    ),
                )

                error_message = (
                    f"Run {run['run_id']} exceeded max attempts ({BG_JOB_MAX_RETRIES}).\n\n"
                    "This usually means the pod processing the run became unhealthy and the run was re-queued repeatedly. "
                    "Check for OOM kills, pod restarts, liveness/readiness probe failures, or other signs of pod instability "
                    "in your deployment's logs and metrics.\n\n"
                )

                raise RuntimeError(error_message)
            configurable = run["kwargs"].get("config", {}).get("configurable", {})
            async with set_auth_ctx_for_run(run["kwargs"]):
                with (
                    restore_otel_trace_context(
                        configurable, run_id=str(run_id), thread_id=str(thread_id)
                    ),
                    restore_dd_trace_context(
                        configurable, run_id=str(run_id), thread_id=str(thread_id)
                    ),
                ):
                    if temporary:
                        stream = astream_state(run, attempt, done)
                    else:
                        stream = astream_state(
                            run,
                            attempt,
                            done,
                            on_checkpoint=on_checkpoint,
                            on_task_result=on_task_result,
                        )
                    stream_modes: set[StreamMode] = set(
                        run["kwargs"].get("stream_mode", [])
                    )
                    await asyncio.wait_for(
                        wrap_user_errors(stream, run_id, resumable, stream_modes),
                        BG_JOB_TIMEOUT_SECS,
                    )
        except (Exception, asyncio.CancelledError) as ee:
            exception = ee
        except BaseException as eee:
            await logger.aerror(
                "Bubbling failed background run",
                run_id=str(run_id),
                exception_type=str(type(eee)),
                exception=str(eee),
            )
            raise
        finally:
            run_ended_at_dt = datetime.now(UTC)
            run_ended_at = run_ended_at_dt.isoformat()

        # handle exceptions and set status
        async with connect() as conn:
            graph_id = run["kwargs"]["config"]["configurable"]["graph_id"]
            log_info = {
                "run_id": str(run_id),
                "run_attempt": attempt,
                "run_created_at": run_created_at,
                "run_started_at": run_started_at,
                "run_ended_at": run_ended_at,
                "run_exec_ms": ms(run_ended_at_dt, run_started_at_dt),
                "run_completed_in_ms": (
                    int((run_ended_at_dt.timestamp() * 1_000) - request_created_at)
                    if request_created_at is not None
                    else None
                ),
                "run_wait_time_ms": ms(run_started_at_dt, run_created_at_dt),
            }

            if exception is None:
                status = "success"
                reporter.inc_counter(COUNTER_RUN_SUCCESS)

                await logger.ainfo(
                    "Background run succeeded",
                    **log_info,
                )
                # If a stateful run succeeded but no checkpoint was returned, likely
                # there was a retriable exception that resumed right at the end
                if checkpoint is None and not temporary:
                    await logger.ainfo(
                        "Fetching missing checkpoint for webhook",
                        run_id=str(run_id),
                        run_attempt=attempt,
                    )
                    try:
                        # We actually still need a connection for the python checkpointer
                        # This only doubles up connections for inmem which is fine
                        async with connect(supports_core_api=False) as conn:
                            state_snapshot = await Threads.State.get(
                                conn, run["kwargs"]["config"], subgraphs=False
                            )
                            checkpoint = state_snapshot_to_thread_state(state_snapshot)
                    except Exception:
                        await logger.aerror(
                            "Failed to fetch missing checkpoint for webhook. Continuing...",
                            exc_info=True,
                            run_id=str(run_id),
                            run_attempt=attempt,
                        )
                if not temporary:
                    await Threads.set_joint_status(
                        conn,
                        run["thread_id"],
                        run_id,
                        status,
                        graph_id=graph_id,
                        checkpoint=checkpoint,
                    )
            elif isinstance(exception, TimeoutError):
                status = "timeout"
                reporter.inc_counter(
                    COUNTER_RUN_FAILED_AFTER_RETRY,
                    attributes={"status": status},
                )
                await logger.awarning(
                    "Background run timed out. To increase the timeout, set the BG_JOB_TIMEOUT_SECS environment variable (integer, defaults to 86400).",
                    **log_info,
                )
                if not temporary:
                    await Threads.set_joint_status(
                        conn,
                        run["thread_id"],
                        run_id,
                        status,
                        graph_id=graph_id,
                        checkpoint=checkpoint,
                        exception=exception,
                    )
            elif isinstance(exception, UserRollback):
                status = "rollback"
                reporter.inc_counter(
                    COUNTER_RUN_CANCELED_BY_REQUEST,
                    attributes={"status": status},
                )
                if not temporary:
                    try:
                        await Threads.set_joint_status(
                            conn,
                            run["thread_id"],
                            run_id,
                            status,
                            graph_id=graph_id,
                            checkpoint=None,
                        )
                        await logger.ainfo(
                            "Background run rolled back",
                            **log_info,
                        )
                    except HTTPException as e:
                        if e.status_code == 404:
                            await logger.ainfo(
                                "Ignoring rollback error for missing run",
                                **log_info,
                            )
                        else:
                            raise

                    # The Go layer deletes checkpoints from Postgres tables,
                    # but custom checkpointers store data elsewhere (e.g. Redis).
                    # Clean up the custom checkpointer's data for this run.
                    if USE_CUSTOM_CHECKPOINTER:
                        try:
                            from langgraph_api import (  # noqa: PLC0415
                                _checkpointer as api_checkpointer,
                            )

                            checkpointer = await api_checkpointer.get_checkpointer()
                            await checkpointer.adelete_for_runs([str(run_id)])
                        except Exception:
                            await logger.aerror(
                                "Failed to clean up custom checkpointer "
                                "data for rolled-back run. Thread state may "
                                "reflect the rolled-back run until a new run "
                                "completes.",
                                exc_info=True,
                                run_id=str(run_id),
                                thread_id=thread_id,
                            )

                    checkpoint = None  # reset the checkpoint
            elif isinstance(exception, UserInterrupt):
                status = "interrupted"
                reporter.inc_counter(
                    COUNTER_RUN_CANCELED_BY_REQUEST,
                    attributes={"status": status},
                )
                await logger.ainfo(
                    "Background run interrupted",
                    **log_info,
                )
                if not temporary:
                    await Threads.set_joint_status(
                        conn,
                        run["thread_id"],
                        run_id,
                        status,
                        graph_id,
                        checkpoint,
                        exception,
                    )
            elif isinstance(exception, ALL_RETRIABLE_EXCEPTIONS):
                status = "retry"
                reporter.inc_counter(COUNTER_RUN_FAILED_RETRIABLE)
                await logger.awarning(
                    f"Background run failed, will retry. Exception: {type(exception)}({exception})",
                    **log_info,
                )
                # Don't update thread status yet.
                # Apply this even for temporary runs, so we retry
                try:
                    await Runs.set_status(conn, run_id, "pending")
                except Exception:
                    reporter.inc_counter(COUNTER_RUN_SET_STATUS_ERROR)
                    raise
            else:
                status = "error"
                reporter.inc_counter(
                    COUNTER_RUN_FAILED_AFTER_RETRY,
                    attributes={"status": status},
                )
                if isinstance(exception, GraphRecursionError):
                    reporter.inc_counter(COUNTER_GRAPH_RECURSION_LIMIT_ERROR)

                # Convert UserTimeout to TimeoutError for customers
                if isinstance(exception, UserTimeout):
                    exception = exception.timeout_error

                # We're outside the original `except` block, so build exc_info manually.
                # This preserves traceback logging for non-remote exceptions.
                exc_info = None
                if not isinstance(exception, RemoteException):
                    exc_info = (
                        type(exception),
                        exception,
                        exception.__traceback__,
                    )

                await logger.aerror(
                    f"Background run failed. Exception: {type(exception)}({exception})",
                    exc_info=exc_info,
                    **log_info,
                )
                if not temporary:
                    await Threads.set_joint_status(
                        conn,
                        run["thread_id"],
                        run_id,
                        status,
                        graph_id,
                        checkpoint,
                        exception,
                    )

            # delete thread if it's temporary and we don't want to retry
            if temporary and not isinstance(exception, ALL_RETRIABLE_EXCEPTIONS):
                await Threads.delete(conn, run["thread_id"])

            reporter.record_latency(
                LATENCY_RUN_EXECUTION,
                float(ms(run_ended_at_dt, run_started_at_dt)) / 1000.0,
                attributes={"status": status or "unknown"},
            )

        if isinstance(exception, ALL_RETRIABLE_EXCEPTIONS):
            await logger.awarning("RETRYING", exc_info=exception)
            # re-raise so Runs.enter knows not to mark as done
            # Runs.enter will catch the exception, but what triggers the retry
            # is setting the status to "pending"
            raise exception

    return WorkerResult(
        checkpoint=checkpoint,
        status=status,
        exception=exception,
        run=run,
        webhook=webhook,
        run_started_at=run_started_at,
        run_ended_at=run_ended_at,
    )


def ms(after: datetime, before: datetime) -> int:
    return int((after - before).total_seconds() * 1000)


def _get_request_id(run: Run) -> str | None:
    try:
        return run["kwargs"]["config"]["configurable"]["langgraph_request_id"]
    except Exception:
        return None


def _get_graph_id(run: Run) -> str | None:
    try:
        return run["kwargs"]["config"]["configurable"]["graph_id"]
    except Exception:
        logger.info(f"Failed to get graph_id from run {run['run_id']}")
        return "Unknown"
