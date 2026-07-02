"""Protocol v2 streaming implementation, isolated from the legacy paths.

Lives apart from :mod:`langgraph_api.stream` so the v2 hot path can be
gated behind ``FF_V2_EVENT_STREAMING`` (route registration in
``api/__init__.py``) and reviewed independently. The legacy ``astream`` /
``astream_events`` paths in :mod:`stream` retain a small amount of
v2-aware branching (forwarding native ``messages`` v2 events,
suppressing legacy reconstruction); those branches call into the
helpers here so the per-shape logic lives in one place.

Capability gating (does the installed ``langgraph`` actually support v2?)
is in :mod:`langgraph_api.event_streaming.capabilities` — the flag controls
whether the server *exposes* v2 routes; the probe controls whether the
runtime can serve them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog

from langgraph_api.asyncio import wait_if_not_done
from langgraph_api.feature_flags import USE_RUNTIME_CONTEXT_API

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AsyncExitStack

    from langchain_core.runnables import RunnableConfig
    from langgraph.pregel.debug import CheckpointPayload, TaskResultPayload

    from langgraph_api.asyncio import ValueEvent

logger = structlog.stdlib.get_logger(__name__)


def is_v2_messages_payload(data: Any) -> bool:
    """Return True iff *data* is a v2 content-block-shaped ``MessagesData`` dict.

    V2 events are dicts with an ``"event"`` discriminator string (e.g.
    ``"message-start"``, ``"content-block-delta"``). The core mux
    sometimes exposes the same payload wrapped in its
    ``(payload, metadata)`` transport tuple; callers should unwrap the
    first element before calling this helper.
    """
    return isinstance(data, dict) and isinstance(data.get("event"), str)


def normalize_v2_messages_data(data: Any) -> Any | None:
    """Return the v2 payload dict if *data* carries one, else ``None``.

    Handles both the bare v2 dict and the mux's ``(payload, metadata)``
    transport tuple. Non-v2 legacy ``messages`` stream shapes return
    ``None``; Protocol v2 intentionally does not reconstruct them.
    """
    if is_v2_messages_payload(data):
        return data
    if isinstance(data, tuple) and len(data) == 2 and is_v2_messages_payload(data[0]):
        return data[0]
    return None


def coerce_stream_transformer_factory(
    item: Any, transformer_base: type
) -> Callable[[tuple[str, ...]], Any]:
    """Normalize a user ``stream_transformers`` list entry into a factory.

    ``langgraph.stream.StreamMux`` accepts factories shaped as
    ``(scope: tuple[str, ...]) -> StreamTransformer``. Classes subclassing
    ``StreamTransformer`` satisfy this directly. For compatibility with
    the original in-repo convention (where ``stream_transformers()``
    returned a list of **already-built instances**), any bare instance
    is wrapped in a closure that returns the same instance regardless
    of the scope the mux was constructed for. That keeps the
    transformer's ``custom:<name>`` channel identity stable across any
    mini-muxes spawned for subgraphs instead of spraying a new channel
    per scope.
    """
    if isinstance(item, transformer_base):
        return lambda _scope, _inst=item: _inst
    if callable(item):
        return item
    raise TypeError(
        f"stream_transformers must yield StreamTransformer instances or "
        f"factories; got {type(item).__name__}"
    )


def normalize_protocol_event(
    event: Any,
    *,
    on_checkpoint: Callable[[CheckpointPayload | None], None],
    on_task_result: Callable[[TaskResultPayload], None],
) -> tuple[str, dict] | None:
    """Convert one native v3 ``ProtocolEvent`` into a ``(name, event)`` pair.

    Shared by the native-graph path (``astream_state_v2``) and the remote
    (JS sidecar) path in :mod:`langgraph_api.stream`. Both receive the same
    ``{"type": "event", "method", "params"}`` ProtocolEvent shape — for
    native graphs from ``langgraph``'s v3 stream, for remote graphs from the
    sidecar's ``streamEvents`` v3 mode — so the per-event handling lives in
    one place.

    Returns ``None`` when the event should be dropped (e.g. a legacy
    ``messages`` shape that Protocol v2 does not reconstruct). Side effects:
    feeds checkpoint / task_result frames into ``on_checkpoint`` /
    ``on_task_result`` so retention + persistence hooks still fire under v2.
    """
    # Local import to avoid a circular ``stream <-> stream_v2`` cycle.
    from langgraph_api.stream import _preprocess_debug_checkpoint  # noqa: PLC0415

    event = cast("dict", event)
    method = event.get("method", "")
    params = event.get("params") or {}
    namespace = params.get("namespace") or []
    if method == "debug":
        chunk = params.get("data") or {}
        if isinstance(chunk, dict):
            if chunk.get("type") == "checkpoint":
                checkpoint = _preprocess_debug_checkpoint(chunk.get("payload"))
                chunk["payload"] = checkpoint
                on_checkpoint(checkpoint)
            elif chunk.get("type") == "task_result":
                on_task_result(chunk.get("payload"))
    # Protocol v2 only forwards native content-block-shaped messages;
    # legacy whole-message/chunk tuples are for the old endpoints and are
    # intentionally ignored here.
    if method == "messages":
        raw_data = params.get("data")
        payload = normalize_v2_messages_data(raw_data)
        if payload is None:
            return None
        if payload is not raw_data:
            params = {**params, "data": payload}
            event = {**event, "params": params}
    # Carry the namespace in the stream event name so ``parse_event_name``
    # in ``session.py`` can recover it, matching the raw ``astream`` path.
    ns_suffix = f"|{'|'.join(namespace)}" if namespace else ""
    return f"{method}{ns_suffix}", event


def is_v2_messages_chunk(chunk: Any) -> bool:
    """Heuristically detect a ``StreamMessagesHandlerV2`` chunk.

    The v2 handler emits ``(event_dict, metadata_dict)`` tuples where the
    first element is a protocol ``messages`` event (``message-start`` /
    ``content-block-*`` / ``message-finish``). v1 emits
    ``(AIMessageChunk, metadata)`` tuples instead. We distinguish on the
    first element's shape: only v2 uses a dict with an ``event`` string.
    """
    if not isinstance(chunk, tuple) or len(chunk) != 2:
        return False
    head, _meta = chunk
    return isinstance(head, dict) and isinstance(head.get("event"), str)


async def astream_state_v2(
    *,
    graph: Any,
    input: Any,
    config: RunnableConfig,
    configurable: dict[str, Any],
    kwargs: dict[str, Any],
    context: dict[str, Any] | None,
    stack: AsyncExitStack,
    done: ValueEvent,
    on_checkpoint: Callable[[CheckpointPayload | None], None],
    on_task_result: Callable[[TaskResultPayload], None],
) -> AsyncIterator[tuple[str, Any]]:
    """Drive a Protocol v2 run via langgraph's native v3 stream framework.

    Reached when the run config opted into v2 (``__event_streaming_v2``).
    The marker is only set by the v2 routes, which are themselves gated
    on ``FF_V2_EVENT_STREAMING`` at registration time. The API layer keeps
    responsibility for transport, replay IDs, subscription filtering, and
    retention hooks for debug checkpoint/task frames; the framework
    handles message construction, namespace propagation, and transformer
    wiring.
    """
    # ``langgraph.stream._types`` is alpha-only; ``capabilities.py``
    # rejects v2 runs on older langgraph before this branch fires.
    from langgraph.stream._types import (  # noqa: PLC0415  # ty: ignore[unresolved-import]
        StreamTransformer,
    )

    # Local import to avoid a circular ``stream <-> stream_v2`` cycle and
    # keep this module's import graph small.
    from langgraph_api.graph import GRAPH_STREAM_TRANSFORMERS  # noqa: PLC0415

    user_transformers: list[Any] | None = None
    factory = GRAPH_STREAM_TRANSFORMERS.get(configurable.get("graph_id"))
    if factory is not None:
        try:
            produced = factory()
        except Exception as exc:
            await logger.aexception(
                "stream_transformers factory raised; falling back to built-ins only",
                graph_id=configurable.get("graph_id"),
                error=str(exc),
            )
            produced = None
        if isinstance(produced, list | tuple) and produced:
            # ``_build_stream_factories`` expects **factories** — callables
            # of the form ``(scope: tuple[str, ...]) -> StreamTransformer``.
            # Classes that subclass ``StreamTransformer`` are themselves
            # valid factories (the base ``__init__`` accepts ``scope``).
            # Preserve back-compat with the original in-repo convention
            # where ``stream_transformers()`` returned a list of
            # already-instantiated transformers by auto-wrapping each bare
            # instance in a factory that returns the same instance for
            # every scope — mini-muxes spawned for subgraphs will re-use
            # it, keeping the transformer's ``custom:<name>`` channel
            # stable across the whole run instead of fragmenting into a
            # fresh channel per subgraph.
            user_transformers = [
                coerce_stream_transformer_factory(item, StreamTransformer)
                for item in produced
            ]

    if USE_RUNTIME_CONTEXT_API:
        kwargs["context"] = context
    # ``subgraphs`` is owned by ``version="v3"`` (forced True so nested
    # namespaces flow through scoped muxes); strip before forwarding so
    # the run's caller-set value doesn't trip the v3 invariant check in
    # langgraph >=1.2.0a6. ``stream_mode`` was already popped by the
    # caller.
    kwargs.pop("subgraphs", None)
    # ``version="v3"`` and the awaitable return are alpha-only;
    # ``capabilities.py`` gates the v3 path at runtime so older
    # langgraph never reaches this call.
    run_stream = await graph.astream_events(  # ty: ignore[invalid-await]
        input,
        config,
        version="v3",
        transformers=user_transformers,
        **kwargs,
    )
    async with stack, run_stream:
        sentinel = object()
        iterator = run_stream.__aiter__()
        while True:
            event = await wait_if_not_done(anext(iterator, sentinel), done)
            if event is sentinel:
                break
            normalized = normalize_protocol_event(
                event,
                on_checkpoint=on_checkpoint,
                on_task_result=on_task_result,
            )
            if normalized is not None:
                yield normalized
