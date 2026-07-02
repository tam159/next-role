import asyncio
import contextlib
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from uuid import UUID

import structlog
from starlette.exceptions import HTTPException
from typing_extensions import TypedDict

from langgraph_api.auth.noop import UnauthenticatedUser
from langgraph_api.encryption.middleware import encrypt_request
from langgraph_api.encryption.shared import (
    using_custom_encryption,
)
from langgraph_api.event_streaming.constants import EVENT_STREAMING_V2_CONFIG_KEY
from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.graph import (
    GRAPHS,
    get_assistant_id,
    inject_current_dd_trace_context,
)
from langgraph_api.otel_context import inject_current_trace_context
from langgraph_api.schema import (
    All,
    Config,
    Context,
    IfNotExists,
    MetadataInput,
    MultitaskStrategy,
    OnCompletion,
    Run,
    RunCommand,
    StreamMode,
)
from langgraph_api.utils import AsyncConnectionProto, get_auth_ctx, get_user_id
from langgraph_api.utils.headers import get_configurable_headers
from langgraph_api.utils.uuids import uuid7
from langgraph_api.webhook import validate_webhook_url_or_raise

if IS_POSTGRES_OR_GRPC_BACKEND:
    from langgraph_api.grpc.ops import Runs
else:
    from langgraph_runtime.ops import Runs

if TYPE_CHECKING:
    from starlette.authentication import BaseUser

logger = structlog.stdlib.get_logger(__name__)


class LangSmithTracer(TypedDict, total=False):
    """Configuration for LangSmith tracing."""

    example_id: str | None
    project_name: str | None


class RunCreateDict(TypedDict):
    """Payload for creating a run."""

    assistant_id: str
    """Assistant ID to use for this run."""
    checkpoint_id: str | None
    """Checkpoint ID to start from. Defaults to the latest checkpoint."""
    input: Sequence[dict] | dict[str, Any] | None
    """Input to the run. Pass null to resume from the current state of the thread."""
    command: RunCommand | None
    """One or more commands to update the graph's state and send messages to nodes."""
    metadata: MetadataInput
    """Metadata for the run."""
    config: Config | None
    """Additional configuration for the run."""
    context: Context | None
    """Static context for the run."""
    webhook: str | None
    """Webhook to call when the run is complete."""

    interrupt_before: All | list[str] | None
    """Interrupt execution before entering these nodes."""
    interrupt_after: All | list[str] | None
    """Interrupt execution after leaving these nodes."""

    multitask_strategy: MultitaskStrategy
    """Strategy to handle concurrent runs on the same thread. Only relevant if
    there is a pending/inflight run on the same thread. One of:
    - "reject": Reject the new run.
    - "interrupt": Interrupt the current run, keeping steps completed until now,
       and start a new one.
    - "rollback": Cancel and delete the existing run, rolling back the thread to
      the state before it had started, then start the new run.
    - "enqueue": Queue up the new run to start after the current run finishes.
    """
    on_completion: OnCompletion
    """What to do when the run completes. One of:
    - "keep": Keep the thread in the database.
    - "delete": Delete the thread from the database.
    """
    stream_mode: list[StreamMode] | StreamMode
    """One or more of "values", "messages", "updates" or "events".
    - "values": Stream the thread state any time it changes.
    - "messages": Stream chat messages from thread state and calls to chat models,
      token-by-token where possible.
    - "updates": Stream the state updates returned by each node.
    - "events": Stream all events produced by sub-runs (eg. nodes, LLMs, etc.).
    - "custom": Stream custom events produced by your nodes.

    Note: __interrupt__ events are always included in the updates stream, even when "updates"
    is not explicitly requested, to ensure interrupt events are always visible.
    """
    stream_subgraphs: bool | None
    """Stream output from subgraphs. By default, streams only the top graph."""
    stream_resumable: bool | None
    """Whether to persist the stream chunks in order to resume the stream later."""
    feedback_keys: list[str] | None
    """Pass one or more feedback_keys if you want to request short-lived signed URLs
    for submitting feedback to LangSmith with this key for this run."""
    after_seconds: int | None
    """Start the run after this many seconds. Defaults to 0."""
    if_not_exists: IfNotExists
    """Create the thread if it doesn't exist. If False, reply with 404."""
    langsmith_tracer: LangSmithTracer | None
    """Configuration for additional tracing with LangSmith."""
    durability: str | None
    """Durability level for the run. Must be one of 'sync', 'async', or 'exit'."""


def ensure_ids(
    assistant_id: str | UUID,
    thread_id: str | UUID | None,
    payload: RunCreateDict,
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None]:
    try:
        results = [
            assistant_id if isinstance(assistant_id, UUID) else UUID(assistant_id)
        ]
    except ValueError:
        keys = ", ".join(GRAPHS.keys())
        raise HTTPException(
            status_code=422,
            detail=f"Invalid assistant: '{assistant_id}'. Must be either:\n"
            f"- A valid assistant UUID, or\n"
            f"- One of the registered graphs: {keys}",
        ) from None
    if thread_id:
        try:
            results.append(
                thread_id if isinstance(thread_id, UUID) else UUID(thread_id)
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid thread ID") from None
    else:
        results.append(None)
    if checkpoint_id := payload.get("checkpoint_id"):
        try:
            results.append(
                checkpoint_id
                if isinstance(checkpoint_id, UUID)
                else UUID(checkpoint_id)
            )
        except ValueError:
            raise HTTPException(
                status_code=422, detail="Invalid checkpoint ID"
            ) from None
    else:
        results.append(None)
    return tuple(results)


def assign_defaults(
    payload: RunCreateDict,
):
    if payload.get("stream_mode"):
        stream_mode = (
            payload["stream_mode"]
            if isinstance(payload["stream_mode"], list)
            else [payload["stream_mode"]]
        )
    else:
        stream_mode = ["values"]
    multitask_strategy = payload.get("multitask_strategy") or "enqueue"
    prevent_insert_if_inflight = multitask_strategy == "reject"
    return stream_mode, multitask_strategy, prevent_insert_if_inflight


async def create_valid_run(
    conn: AsyncConnectionProto,
    thread_id: str | None,
    payload: RunCreateDict,
    headers: Mapping[str, str],
    barrier: asyncio.Barrier | None = None,
    run_id: UUID | None = None,
    request_start_time: float | None = None,
    temporary: bool = False,
) -> Run:
    request_id = headers.get("x-request-id")  # Will be null in the crons scheduler.
    (
        assistant_id,
        thread_id_,
        checkpoint_id,
        run_id,
    ) = _get_ids(
        thread_id,
        payload,
        run_id=run_id,
    )
    if (
        (thread_id_ is None or temporary)
        and (command := payload.get("command"))
        and command.get("resume")
    ):
        raise HTTPException(
            status_code=400,
            detail="You must provide a thread_id when resuming.",
        )
    temporary = (temporary or thread_id_ is None) and payload.get(
        "on_completion", "delete"
    ) == "delete"
    stream_resumable = payload.get("stream_resumable", False)
    stream_mode, multitask_strategy, prevent_insert_if_inflight = assign_defaults(
        payload
    )
    # assign custom headers and checkpoint to config
    config = payload.get("config") or {}
    context = payload.get("context") or {}
    configurable = config.setdefault("configurable", {})

    if configurable and context:
        raise HTTPException(
            status_code=400,
            detail="Cannot specify both configurable and context. Prefer setting context alone. Context was introduced in LangGraph 0.6.0 and is the long term planned replacement for configurable.",
        )

    # ``tools`` and ``lifecycle`` are emitted only by the protocol v2
    # streaming pipeline (``stream_v2.py`` / ``StreamMessagesHandlerV2``
    # plus the registered stream transformers). Accepting them on legacy
    # streaming routes would either no-op (``lifecycle``, never emitted
    # without a transformer) or leak v2-shaped events into v1 consumers
    # (``tools``). Reject up front so callers get a clear 422 instead of
    # a silently empty stream.
    if not configurable.get(EVENT_STREAMING_V2_CONFIG_KEY):
        v2_only = {"tools", "lifecycle"}.intersection(stream_mode)
        if v2_only:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"stream_mode entries {sorted(v2_only)!r} are only "
                    "supported on the v2 event streaming endpoints."
                ),
            )

    # Keep config and context in sync for user provided params
    if context:
        configurable = context.copy()
        config["configurable"] = configurable
    else:
        context = configurable.copy()

    if checkpoint_id:
        configurable["checkpoint_id"] = str(checkpoint_id)
    if checkpoint := payload.get("checkpoint"):
        configurable.update(checkpoint)
    configurable.update(get_configurable_headers(headers))
    inject_current_trace_context(configurable)
    inject_current_dd_trace_context(configurable)
    ctx = get_auth_ctx()
    if ctx:
        user = cast("BaseUser | None", ctx.user)
        user_id = get_user_id(user)
        # Store user as-is; encryption middleware will serialize if needed.
        # UnauthenticatedUser is stored as None for consistency.
        configurable["langgraph_auth_user"] = (
            None if isinstance(user, UnauthenticatedUser) else user
        )
        configurable["langgraph_auth_user_id"] = user_id
        configurable["langgraph_auth_permissions"] = ctx.permissions
    else:
        user_id = None
    if not configurable.get("langgraph_request_id"):
        configurable["langgraph_request_id"] = request_id
    if ls_tracing := payload.get("langsmith_tracer"):
        configurable["__langsmith_project__"] = ls_tracing.get("project_name")
        configurable["__langsmith_example_id__"] = ls_tracing.get("example_id")
    if request_start_time:
        configurable["__request_start_time_ms__"] = request_start_time
    after_seconds = cast("int", payload.get("after_seconds", 0))
    configurable["__after_seconds__"] = after_seconds
    # Note: encryption context is injected by encrypt_request → encrypt_json_if_needed
    # as the __encryption_context__ marker. Worker reads it before decryption.
    put_time_start = time.time()
    if_not_exists = payload.get("if_not_exists", "reject")

    durability = payload.get("durability")
    if durability is None:
        checkpoint_during = payload.get("checkpoint_during")
        durability = "async" if checkpoint_during in (None, True) else "exit"

    if webhook := payload.get("webhook"):
        await validate_webhook_url_or_raise(str(webhook))

    # We can't pass payload directly because config and context have
    # been modified above (with auth context, checkpoint info, etc.)
    fields_to_encrypt = {
        "metadata": payload.get("metadata"),
        "input": payload.get("input"),
        "config": config,
        "context": context,
        "command": payload.get("command"),
    }
    if IS_POSTGRES_OR_GRPC_BACKEND and using_custom_encryption():
        encrypted = fields_to_encrypt  # Go layer handles encryption at rest
    else:
        encrypted = await encrypt_request(
            fields_to_encrypt,
            "run",
            ["metadata", "input", "config", "context", "command"],
        )

    run_coro = Runs.put(
        conn,
        assistant_id,
        {
            "input": encrypted.get("input"),
            "command": encrypted.get("command"),
            "config": encrypted.get("config"),
            "context": encrypted.get("context"),
            "stream_mode": stream_mode,
            "interrupt_before": payload.get("interrupt_before"),
            "interrupt_after": payload.get("interrupt_after"),
            "webhook": payload.get("webhook"),
            "feedback_keys": payload.get("feedback_keys"),
            "temporary": temporary,
            "subgraphs": payload.get("stream_subgraphs", False),
            "resumable": stream_resumable,
            "checkpoint_during": payload.get("checkpoint_during", True),
            "durability": durability,
        },
        metadata=encrypted.get("metadata"),
        status="pending",
        user_id=user_id,
        thread_id=thread_id_,
        run_id=run_id,
        multitask_strategy=multitask_strategy,
        prevent_insert_if_inflight=prevent_insert_if_inflight,
        after_seconds=after_seconds,
        if_not_exists=if_not_exists,
    )
    run_ = await run_coro

    if barrier:
        await barrier.wait()

    # abort if thread, assistant, etc not found
    try:
        first = await anext(run_)
    except StopAsyncIteration:
        raise HTTPException(
            status_code=404, detail="Thread or assistant not found."
        ) from None

    # handle multitask strategy
    inflight_runs = [run async for run in run_]
    if first["run_id"] == run_id:
        logger.info(
            "Created run",
            run_id=str(run_id),
            thread_id=str(thread_id_),
            assistant_id=str(assistant_id),
            multitask_strategy=multitask_strategy,
            stream_mode=stream_mode,
            temporary=temporary,
            after_seconds=after_seconds,
            if_not_exists=if_not_exists,
            stream_resumable=stream_resumable,
            run_create_ms=(
                int(time.time() * 1_000) - request_start_time
                if request_start_time
                else None
            ),
            run_put_ms=int((time.time() - put_time_start) * 1_000),
            checkpoint_id=str(checkpoint_id),
        )
        # inserted, proceed
        if multitask_strategy in ("interrupt", "rollback") and inflight_runs:
            with contextlib.suppress(HTTPException):
                # if we can't find the inflight runs again, we can proceeed
                await Runs.cancel(
                    conn,
                    [run["run_id"] for run in inflight_runs],
                    thread_id=thread_id_,
                    action=multitask_strategy,
                )
        return first
    elif multitask_strategy == "reject":
        raise HTTPException(
            status_code=409,
            detail="Thread is already running a task. Wait for it to finish or choose a different multitask strategy.",
        )
    else:
        raise NotImplementedError


class _Ids(NamedTuple):
    assistant_id: uuid.UUID
    thread_id: uuid.UUID | None
    checkpoint_id: uuid.UUID | None
    run_id: uuid.UUID


def _get_ids(
    thread_id: str | None,
    payload: RunCreateDict,
    run_id: UUID | None = None,
) -> _Ids:
    # get assistant_id
    assistant_id = get_assistant_id(payload["assistant_id"])

    # ensure UUID validity defaults
    assistant_id, thread_id_, checkpoint_id = ensure_ids(
        assistant_id, thread_id, payload
    )

    run_id = run_id or uuid7()

    return _Ids(
        assistant_id,
        thread_id_,
        checkpoint_id,
        run_id,
    )
