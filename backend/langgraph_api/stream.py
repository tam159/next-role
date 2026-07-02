import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, aclosing, asynccontextmanager, suppress
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

import langgraph.version
import langsmith
import structlog
from langchain_core.messages import (
    AIMessageChunk,
    # TODO: Remove explicit dependency
    BaseMessage,
    BaseMessageChunk,
    ToolMessageChunk,
    convert_to_messages,
    message_chunk_to_message,
)
from langgraph.errors import (
    EmptyChannelError,
    EmptyInputError,
    GraphRecursionError,
    InvalidUpdateError,
)
from langgraph.pregel.debug import CheckpointPayload, TaskResultPayload
from langsmith.utils import get_tracer_project
from pydantic import ValidationError
from pydantic.v1 import ValidationError as ValidationErrorLegacy

from langgraph_api import __version__
from langgraph_api import _checkpointer as api_checkpointer
from langgraph_api import store as api_store
from langgraph_api.asyncio import ValueEvent, wait_if_not_done
from langgraph_api.command import map_cmd
from langgraph_api.config import FF_ASYNC_PUBLISH_QUEUE, LSD_PUBLISH_QUEUE_SIZE
from langgraph_api.feature_flags import (
    IS_POSTGRES_OR_GRPC_BACKEND,
    UPDATES_NEEDED_FOR_INTERRUPTS,
    USE_DURABILITY,
    USE_RUNTIME_CONTEXT_API,
)
from langgraph_api.graph import get_graph
from langgraph_api.grpc.ops.runs import StreamPublishException
from langgraph_api.js.base import BaseRemotePregel
from langgraph_api.metadata import HOST, PLAN, USER_API_URL, incr_nodes
from langgraph_api.metrics_datadog import (
    COUNTER_STREAMING_DATA_LOSS,
    GAUGE_PUBLISH_QUEUE_AVAILABILITY,
    HISTOGRAM_STREAM_DATA_SIZE,
    LATENCY_STREAM_PUBLISH,
    get_datadog_metrics_reporter,
)
from langgraph_api.schema import Run, StreamMode
from langgraph_api.serde import json_dumpb
from langgraph_api.stream_v2 import (
    astream_state_v2,
    is_v2_messages_chunk,
    normalize_protocol_event,
)
from langgraph_api.utils.config import run_in_executor

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

if IS_POSTGRES_OR_GRPC_BACKEND:
    from langgraph_api.grpc.ops import Runs
else:
    from langgraph_runtime.ops import Runs


logger = structlog.stdlib.get_logger(__name__)


async def _filter_context_by_schema(
    context: dict[str, Any], context_schema: dict | None
) -> dict[str, Any]:
    """Filter context parameters based on the context schema.

    Args:
        context: The context dictionary to filter
        context_schema: The JSON schema for valid context parameters

    Returns:
        Filtered context dictionary containing only valid parameters
    """
    if not context_schema or not context:
        return context

    # Extract valid properties from the schema
    properties = context_schema.get("properties", {})
    if not properties:
        return context

    # Filter context to only include parameters defined in the schema
    filtered_context = {}
    for key, value in context.items():
        if key in properties:
            filtered_context[key] = value
        else:
            await logger.adebug(
                f"Filtering out context parameter '{key}' not found in context schema",
                context_key=key,
                available_keys=list(properties.keys()),
            )

    return filtered_context


AnyStream = AsyncIterator[tuple[str, Any]]


def _preproces_debug_checkpoint_task(task: dict[str, Any]) -> dict[str, Any]:
    if (
        "state" not in task
        or not task["state"]
        or "configurable" not in task["state"]
        or not task["state"]["configurable"]
    ):
        return task

    task["checkpoint"] = task["state"]["configurable"]
    del task["state"]
    return task


def _preprocess_debug_checkpoint(
    payload: CheckpointPayload | None,
) -> dict[str, Any] | None:
    from langgraph_api.state import runnable_config_to_checkpoint  # noqa: PLC0415

    if not payload:
        return None

    # TODO: deprecate the `config`` and `parent_config`` fields
    return {
        **payload,
        "checkpoint": runnable_config_to_checkpoint(payload["config"]),
        "parent_checkpoint": runnable_config_to_checkpoint(
            payload.get("parent_config", None)
        ),
        "tasks": [_preproces_debug_checkpoint_task(t) for t in payload["tasks"]],
    }


@asynccontextmanager
async def async_tracing_context(*args, **kwargs):
    with langsmith.tracing_context(*args, **kwargs):
        yield


async def astream_state(
    run: Run,
    attempt: int,
    done: ValueEvent,
    *,
    on_checkpoint: Callable[[CheckpointPayload | None], None] = lambda _: None,
    on_task_result: Callable[[TaskResultPayload], None] = lambda _: None,
) -> AnyStream:
    """Stream messages from the runnable."""
    run_id = str(run["run_id"])
    # extract args from run
    kwargs = run["kwargs"].copy()
    kwargs.pop("webhook", None)
    kwargs.pop("resumable", False)
    if USE_DURABILITY:
        checkpoint_during = kwargs.pop("checkpoint_during")
        if not kwargs.get("durability") and checkpoint_during:
            kwargs["durability"] = "async" if checkpoint_during else "exit"
    else:
        durability = kwargs.pop("durability")
        if not kwargs.get("checkpoint_during") and durability in ("async", "exit"):
            kwargs["checkpoint_during"] = durability == "async"
    subgraphs = kwargs.get("subgraphs", False)
    temporary = kwargs.pop("temporary", False)
    context = kwargs.pop("context", None)
    config = cast("RunnableConfig", kwargs.pop("config"))
    configurable = config["configurable"]
    stack = AsyncExitStack()
    graph = await stack.enter_async_context(
        get_graph(
            configurable["graph_id"],
            config,
            store=(await api_store.get_store()),
            checkpointer=None
            if temporary
            else await api_checkpointer.get_checkpointer(),
            access_context="threads.create_run",
            run_id=run_id,
        )
    )

    # Filter context parameters based on context schema if available
    if context and USE_RUNTIME_CONTEXT_API and not isinstance(graph, BaseRemotePregel):
        try:
            context_schema = graph.get_context_jsonschema()
            context = await _filter_context_by_schema(context, context_schema)
        except Exception as e:
            await logger.adebug(
                f"Failed to get context schema for filtering: {e}", exc_info=e
            )

    input = kwargs.pop("input")
    if cmd := kwargs.pop("command"):
        input = map_cmd(cmd)
    stream_mode: list[StreamMode] = kwargs.pop("stream_mode")
    # Go maps unrecognized ``StreamMode`` enum values to ``unknown``; the
    # round-trip converts those to ``None``.
    stream_mode = [mode for mode in stream_mode if mode is not None]
    feedback_keys = kwargs.pop("feedback_keys", None)
    stream_modes_set: set[StreamMode] = set(stream_mode) - {"events"}
    # This code path runs for every run, legacy and v2. The per-run
    # opt-in is the ``__event_streaming_v2`` configurable marker,
    # set only by the v2 endpoints (see ``event_streaming/service.py``). Once
    # set, the wire shape of ``messages`` events is incompatible with
    # v1 — v2 emits a single content-block-shaped ``event_dict`` under
    # mode ``messages``, while v1 emits ``messages/metadata`` plus
    # ``messages/partial``/``messages/complete`` with ``BaseMessage``
    # payloads — so the protocol contract is fixed for the lifetime
    # of the run. ``FF_V2_EVENT_STREAMING`` gates v2 *route
    # registration* in ``api/__init__.py``; we deliberately do not
    # re-check it here, because downgrading a queued run mid-flight
    # would break the v2 consumer that is already parsing v2-shaped
    # events from the same stream.
    event_streaming_v2_run = bool(configurable.get("__event_streaming_v2"))
    is_remote_pregel = isinstance(graph, BaseRemotePregel)
    if is_remote_pregel:
        # ``"tools"`` / ``"lifecycle"`` are public StreamMode literals
        # (``schema.py``) but the remote pregel runtime does not
        # implement them. Strip silently so a user request like
        # ``stream_mode=["tools", "lifecycle"]`` against a remote graph
        # does not get forwarded as an unsupported mode list.
        # ``capabilities.py`` documents this behavior — keep them in sync.
        stream_modes_set.discard("tools")
        stream_modes_set.discard("lifecycle")
    if "debug" not in stream_modes_set:
        stream_modes_set.add("debug")
    if "messages-tuple" in stream_modes_set and not is_remote_pregel:
        stream_modes_set.remove("messages-tuple")
        stream_modes_set.add("messages")
    if "updates" not in stream_modes_set and UPDATES_NEEDED_FOR_INTERRUPTS:
        stream_modes_set.add("updates")
        only_interrupt_updates = True
    else:
        only_interrupt_updates = False
    # attach attempt metadata
    config["metadata"]["run_attempt"] = attempt
    # attach langgraph metadata
    config["metadata"]["langgraph_version"] = langgraph.version.__version__
    config["metadata"]["langgraph_api_version"] = __version__
    config["metadata"]["langgraph_plan"] = PLAN
    config["metadata"]["langgraph_host"] = HOST
    config["metadata"]["langgraph_api_url"] = USER_API_URL
    # attach node counter
    if not is_remote_pregel:
        configurable["__pregel_node_finished"] = incr_nodes

    # attach run_id to config
    # for attempts beyond the first, use a fresh, unique run_id
    config = {**config, "run_id": run["run_id"]} if attempt == 1 else config
    # set up state
    checkpoint: CheckpointPayload | None = None
    messages: dict[str, BaseMessageChunk] = {}
    # Protocol v2 runs against a remote (JS sidecar) graph drive the sidecar
    # through LangGraphJS's native v3 stream (``stream_protocol: "v3"``) so
    # protocol-aligned events (messages content blocks, tools, custom:*,
    # checkpoints) arrive ready to forward — no legacy ``on_chain_stream``
    # reconstruction. ``hasattr`` keeps any other ``BaseRemotePregel`` impl on
    # the legacy path. Legacy stream endpoints keep ``version="v2"``.
    use_remote_v3 = (
        event_streaming_v2_run
        and is_remote_pregel
        and hasattr(graph, "astream_protocol_events")
    )
    use_astream_events = (
        "events" in stream_mode or is_remote_pregel
    ) and not use_remote_v3
    use_stream_events_v3 = (
        event_streaming_v2_run and not use_astream_events and not is_remote_pregel
    )
    # yield metadata chunk
    yield "metadata", {"run_id": run_id, "attempt": attempt}

    #  is a langsmith tracing project is specified, additionally pass that in to tracing context
    if ls_project := configurable.get("__langsmith_project__"):
        updates = None
        if example_id := configurable.get("__langsmith_example_id__"):
            updates = {"reference_example_id": example_id}

        await stack.enter_async_context(
            async_tracing_context(
                replicas=[
                    {
                        "project_name": ls_project,
                        "updates": updates,
                    },
                    {
                        "project_name": get_tracer_project(),
                        "updates": None,
                    },
                ]
            )
        )

    # stream run
    if use_astream_events:
        if USE_RUNTIME_CONTEXT_API:
            kwargs["context"] = context
        async with (
            stack,
            aclosing(
                graph.astream_events(
                    input,
                    config,
                    version="v2",
                    stream_mode=list(stream_modes_set),
                    **kwargs,
                )
            ) as stream,
        ):
            sentinel = object()
            while True:
                event = await wait_if_not_done(anext(stream, sentinel), done)
                if event is sentinel:
                    break
                event = cast("dict", event)
                if event.get("tags") and "langsmith:hidden" in event["tags"]:
                    continue
                if (
                    "messages" in stream_mode
                    and isinstance(graph, BaseRemotePregel)
                    and event["event"] == "on_custom_event"
                    and event["name"]
                    in (
                        "messages/complete",
                        "messages/partial",
                        "messages/metadata",
                    )
                ):
                    yield event["name"], event["data"]
                # TODO support messages-tuple for js graphs
                if event["event"] == "on_chain_stream" and event["run_id"] == run_id:
                    if subgraphs:
                        ns, mode, chunk = event["data"]["chunk"]
                    else:
                        mode, chunk = event["data"]["chunk"]
                        ns = None
                    # --- begin shared logic with astream ---
                    if mode == "debug":
                        if chunk["type"] == "checkpoint":
                            checkpoint = _preprocess_debug_checkpoint(chunk["payload"])
                            chunk["payload"] = checkpoint
                            on_checkpoint(checkpoint)
                        elif chunk["type"] == "task_result":
                            on_task_result(chunk["payload"])
                    if mode == "messages":
                        if event_streaming_v2_run and is_v2_messages_chunk(chunk):
                            event_dict, _meta = chunk
                            if subgraphs and ns:
                                yield f"messages|{'|'.join(ns)}", event_dict
                            else:
                                yield "messages", event_dict
                        elif (
                            not event_streaming_v2_run
                            and "messages-tuple" in stream_mode
                        ):
                            if subgraphs and ns:
                                yield f"messages|{'|'.join(ns)}", chunk
                            else:
                                yield "messages", chunk
                        elif not event_streaming_v2_run:
                            msg_, meta = cast(
                                "tuple[BaseMessage | dict, dict[str, Any]]", chunk
                            )
                            is_chunk = False
                            if isinstance(msg_, dict):
                                if (
                                    "chunk" in msg_.get("type", "").lower()
                                    or "chunk" in msg_.get("role", "").lower()
                                ):
                                    if "ai" in msg_.get("role", "").lower():
                                        msg = AIMessageChunk(**msg_)
                                    elif "tool" in msg_.get("role", "").lower():
                                        msg = ToolMessageChunk(**msg_)
                                    else:
                                        msg = BaseMessageChunk(**msg_)
                                    is_chunk = True
                                else:
                                    msg = convert_to_messages([msg_])[0]
                            else:
                                msg = msg_
                            if msg.id in messages:
                                messages[msg.id] += msg
                            else:
                                messages[msg.id] = msg
                                yield "messages/metadata", {msg.id: {"metadata": meta}}
                            yield (
                                (
                                    "messages/partial"
                                    if isinstance(msg, BaseMessageChunk)
                                    else "messages/complete"
                                ),
                                [
                                    (
                                        message_chunk_to_message(messages[msg.id])
                                        if not is_chunk
                                        else messages[msg.id]
                                    )
                                ],
                            )
                    elif mode in stream_mode or (
                        event_streaming_v2_run and mode in stream_modes_set
                    ):
                        # Forward natively-emitted ``tools`` / ``lifecycle``
                        # events for protocol v2 runs even when the client
                        # didn't explicitly request them — the session
                        # uses them for namespace registration and
                        # agent.getTree ordering.
                        if subgraphs and ns:
                            yield f"{mode}|{'|'.join(ns)}", chunk
                        else:
                            yield mode, chunk
                    elif (
                        mode == "updates"
                        and isinstance(chunk, dict)
                        and "__interrupt__" in chunk
                        and len(chunk["__interrupt__"]) > 0
                        and only_interrupt_updates
                    ):
                        # If the interrupt doesn't have any actions (e.g. interrupt before or after a node is specified), we don't return the interrupt at all today.
                        if subgraphs and ns:
                            yield f"values|{'|'.join(ns)}", chunk
                        else:
                            yield "values", chunk
                    # --- end shared logic with astream ---
                elif "events" in stream_mode:
                    yield "events", event
    elif use_stream_events_v3:
        async for ev in astream_state_v2(
            graph=graph,
            input=input,
            config=config,
            configurable=configurable,
            kwargs=kwargs,
            context=context,
            stack=stack,
            done=done,
            on_checkpoint=on_checkpoint,
            on_task_result=on_task_result,
        ):
            yield ev
    elif use_remote_v3:
        # Remote (JS) graph under Protocol v2: consume native v3
        # ProtocolEvents from the sidecar and forward them exactly like the
        # native-graph v3 path (``astream_state_v2``) does, so the
        # ``EventStreamingSession`` normalization is identical regardless of
        # graph language.
        if USE_RUNTIME_CONTEXT_API:
            kwargs["context"] = context
        async with (
            stack,
            aclosing(
                graph.astream_protocol_events(
                    input,
                    config,
                    stream_mode=list(stream_modes_set),
                    **kwargs,
                )
            ) as stream,
        ):
            sentinel = object()
            while True:
                event = await wait_if_not_done(anext(stream, sentinel), done)
                if event is sentinel:
                    break
                normalized = normalize_protocol_event(
                    cast("dict", event),
                    on_checkpoint=on_checkpoint,
                    on_task_result=on_task_result,
                )
                if normalized is not None:
                    yield normalized
    else:
        output_keys = kwargs.pop("output_keys", graph.output_channels)
        if USE_RUNTIME_CONTEXT_API:
            kwargs["context"] = context
        async with (
            stack,
            aclosing(
                graph.astream(
                    input,
                    config,
                    stream_mode=list(stream_modes_set),
                    output_keys=output_keys,
                    **kwargs,
                )
            ) as stream,
        ):
            sentinel = object()
            while True:
                event = await wait_if_not_done(anext(stream, sentinel), done)
                if event is sentinel:
                    break
                if subgraphs:
                    ns, mode, chunk = cast("tuple[str, str, dict[str, Any]]", event)
                else:
                    mode, chunk = cast("tuple[str, dict[str, Any]]", event)
                    ns = None
                # --- begin shared logic with astream_events ---
                if mode == "debug":
                    if chunk["type"] == "checkpoint":
                        checkpoint = _preprocess_debug_checkpoint(chunk["payload"])
                        chunk["payload"] = checkpoint
                        on_checkpoint(checkpoint)
                    elif chunk["type"] == "task_result":
                        on_task_result(chunk["payload"])
                if mode == "messages":
                    if event_streaming_v2_run and is_v2_messages_chunk(chunk):
                        event_dict, _meta = chunk
                        if subgraphs and ns:
                            yield f"messages|{'|'.join(ns)}", event_dict
                        else:
                            yield "messages", event_dict
                    elif not event_streaming_v2_run and "messages-tuple" in stream_mode:
                        if subgraphs and ns:
                            yield f"messages|{'|'.join(ns)}", chunk
                        else:
                            yield "messages", chunk
                    elif not event_streaming_v2_run:
                        msg_, meta = cast(
                            "tuple[BaseMessage | dict, dict[str, Any]]", chunk
                        )
                        is_chunk = False
                        if isinstance(msg_, dict):
                            if (
                                "chunk" in msg_.get("type", "").lower()
                                or "chunk" in msg_.get("role", "").lower()
                            ):
                                if "ai" in msg_.get("role", "").lower():
                                    msg = AIMessageChunk(**msg_)
                                elif "tool" in msg_.get("role", "").lower():
                                    msg = ToolMessageChunk(**msg_)
                                else:
                                    msg = BaseMessageChunk(**msg_)
                                is_chunk = True
                            else:
                                msg = convert_to_messages([msg_])[0]
                        else:
                            msg = msg_
                        if msg.id in messages:
                            messages[msg.id] += msg
                        else:
                            messages[msg.id] = msg
                            yield "messages/metadata", {msg.id: {"metadata": meta}}
                        yield (
                            (
                                "messages/partial"
                                if isinstance(msg, BaseMessageChunk)
                                else "messages/complete"
                            ),
                            [
                                (
                                    message_chunk_to_message(messages[msg.id])
                                    if not is_chunk
                                    else messages[msg.id]
                                )
                            ],
                        )
                elif mode in stream_mode or (
                    event_streaming_v2_run and mode in stream_modes_set
                ):
                    # Forward natively-emitted ``tools`` / ``lifecycle``
                    # events for protocol v2 runs even when the client
                    # didn't explicitly request them.
                    if subgraphs and ns:
                        yield f"{mode}|{'|'.join(ns)}", chunk
                    else:
                        yield mode, chunk
                elif (
                    mode == "updates"
                    and isinstance(chunk, dict)
                    and "__interrupt__" in chunk
                    and len(chunk["__interrupt__"]) > 0
                    and only_interrupt_updates
                ):
                    # If the interrupt doesn't have any actions (e.g. interrupt before or after a node is specified), we don't return the interrupt at all today.
                    if subgraphs and ns:
                        yield f"values|{'|'.join(ns)}", chunk
                    else:
                        yield "values", chunk
                # --- end shared logic with astream_events ---

    if is_remote_pregel:
        # increment the remote runs
        try:
            nodes_executed = await graph.fetch_nodes_executed()
            incr_nodes(None, incr=nodes_executed)
        except Exception as e:
            logger.warning(f"Failed to fetch nodes executed for {graph.graph_id}: {e}")
    else:
        await logger.adebug("Graph is not an instance of BaseRemotePregel")

    # Get feedback URLs
    if feedback_keys:
        feedback_urls = await run_in_executor(
            None, get_feedback_urls, run_id, feedback_keys
        )
        yield "feedback", feedback_urls


def get_stream_event_type(mode: str) -> str:
    return mode.split("|", maxsplit=1)[0]


async def consume(
    stream: AnyStream,
    run_id: str | uuid.UUID,
    resumable: bool = False,
    stream_modes: set[StreamMode] | None = None,
    *,
    thread_id: str | uuid.UUID,
) -> None:
    reporter = get_datadog_metrics_reporter()
    stream_modes = stream_modes or set()
    if "messages-tuple" in stream_modes:
        stream_modes.add("messages")
    stream_modes.add("metadata")

    if FF_ASYNC_PUBLISH_QUEUE:
        await _consume_async_queue(
            stream,
            run_id,
            resumable,
            stream_modes,
            thread_id=thread_id,
            reporter=reporter,
        )
    else:
        await _consume_inline(
            stream,
            run_id,
            resumable,
            stream_modes,
            thread_id=thread_id,
            reporter=reporter,
        )


async def _consume_inline(
    stream: AnyStream,
    run_id: str | uuid.UUID,
    resumable: bool,
    stream_modes: set[StreamMode],
    *,
    thread_id: str | uuid.UUID,
    reporter: Any,
) -> None:
    """Publish each stream event inline (synchronous with stream consumption).

    This preserves natural backpressure: each event must be published before the
    next one is consumed.
    """
    async with aclosing(stream):
        try:
            async for mode, payload in stream:
                event_type = get_stream_event_type(mode)
                payload_bytes = await run_in_executor(None, json_dumpb, payload)
                is_resumable = resumable and mode.split("|")[0] in stream_modes
                with reporter.track_latency_ms(
                    LATENCY_STREAM_PUBLISH,
                    attributes={"stream_event_type": event_type},
                ):
                    await Runs.Stream.publish(
                        run_id,
                        mode,
                        payload_bytes,
                        thread_id=thread_id,
                        resumable=is_resumable,
                    )
                reporter.record_histogram(
                    HISTOGRAM_STREAM_DATA_SIZE,
                    float(len(payload_bytes)),
                    attributes={"stream_event_type": event_type},
                )
        except Exception as e:
            if isinstance(e, ExceptionGroup):
                e = e.exceptions[0]
            with suppress(Exception):
                await Runs.Stream.publish(
                    run_id,
                    "error",
                    await run_in_executor(None, json_dumpb, e),
                    thread_id=thread_id,
                )
            if isinstance(e, StreamPublishException):
                await logger.aerror(
                    "Suppressing stream consume exception",
                    run_id=str(run_id),
                    thread_id=str(thread_id),
                    error_type=type(e).__name__,
                    error=str(e),
                )
                reporter.inc_counter(COUNTER_STREAMING_DATA_LOSS)
                return
            raise e


async def _consume_async_queue(
    stream: AnyStream,
    run_id: str | uuid.UUID,
    resumable: bool,
    stream_modes: set[StreamMode],
    *,
    thread_id: str | uuid.UUID,
    reporter: Any,
) -> None:
    """Publish stream events via a background asyncio queue.

    Decouples stream consumption from publishing so that slow publishes don't
    block graph execution. Enable with FF_ASYNC_PUBLISH_QUEUE=true.
    """
    publish_queue: asyncio.Queue[tuple[str, bytes, str, bool] | object] = asyncio.Queue(
        maxsize=LSD_PUBLISH_QUEUE_SIZE
    )
    publish_sentinel = object()

    async def _publish_worker() -> None:
        while True:
            item = await publish_queue.get()
            event_type = "unknown"
            try:
                if item is publish_sentinel:
                    return
                mode, payload_bytes, event_type, is_resumable = cast(
                    "tuple[str, bytes, str, bool]", item
                )
                with reporter.track_latency_ms(
                    LATENCY_STREAM_PUBLISH,
                    attributes={"stream_event_type": event_type},
                ):
                    await Runs.Stream.publish(
                        run_id,
                        mode,
                        payload_bytes,
                        thread_id=thread_id,
                        resumable=is_resumable,
                    )
                reporter.record_histogram(
                    HISTOGRAM_STREAM_DATA_SIZE,
                    float(len(payload_bytes)),
                    attributes={"stream_event_type": event_type},
                )
            except Exception:
                await logger.aerror(
                    "Publish failed, skipping event",
                    run_id=str(run_id),
                    thread_id=str(thread_id),
                    stream_event_type=event_type,
                )
                reporter.inc_counter(
                    COUNTER_STREAMING_DATA_LOSS,
                    attributes={"stream_event_type": event_type},
                )
            finally:
                publish_queue.task_done()

    async def _enqueue(item: tuple[str, bytes, str, bool] | object) -> None:
        reporter.record_gauge(
            GAUGE_PUBLISH_QUEUE_AVAILABILITY,
            float(publish_queue.maxsize - publish_queue.qsize()),
        )
        await publish_queue.put(item)

    publisher_task = asyncio.create_task(_publish_worker())
    try:
        async with aclosing(stream):
            async for mode, payload in stream:
                event_type = get_stream_event_type(mode)
                payload_bytes = await run_in_executor(None, json_dumpb, payload)
                is_resumable = resumable and mode.split("|")[0] in stream_modes
                await _enqueue((mode, payload_bytes, event_type, is_resumable))

    except Exception as e:
        if isinstance(e, ExceptionGroup):
            e = e.exceptions[0]
        with suppress(Exception):
            error_payload = await run_in_executor(None, json_dumpb, e)
            await _enqueue(("error", error_payload, "error", False))
        raise e
    finally:
        with suppress(Exception):
            await _enqueue(publish_sentinel)
        if not publisher_task.done():
            await publisher_task


def get_feedback_urls(run_id: str, feedback_keys: list[str]) -> dict[str, str]:
    client = get_langsmith_client()
    tokens = client.create_presigned_feedback_tokens(run_id, feedback_keys)
    return {key: token.url for key, token in zip(feedback_keys, tokens, strict=False)}


@lru_cache(maxsize=1)
def get_langsmith_client() -> langsmith.Client:
    return langsmith.Client()


EXPECTED_ERRORS = (
    ValueError,
    InvalidUpdateError,
    GraphRecursionError,
    EmptyInputError,
    EmptyChannelError,
    ValidationError,
    ValidationErrorLegacy,
)
