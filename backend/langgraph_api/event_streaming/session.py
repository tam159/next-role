"""Per-run protocol session that normalizes raw stream events into protocol frames."""

from __future__ import annotations

import asyncio
import contextlib
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Awaitable, Callable

import orjson
import structlog

from langgraph_api.config import LSD_PROTOCOL_V2_BUFFER_SIZE
from langgraph_api.event_streaming.constants import (
    SUPPORTED_CHANNELS,
)
from langgraph_api.event_streaming.event_normalizers import (
    normalize_input_requested_data,
    normalize_updates_data,
    strip_interrupts_from_values,
    to_lifecycle_status,
)
from langgraph_api.event_streaming.namespace import (
    guess_graph_name,
    is_prefix_match,
    parse_event_name,
    to_namespace_key,
)
from langgraph_api.event_streaming.state_normalizers import (
    normalize_event_streaming_state_payload,
)
from langgraph_api.event_streaming.types import (
    Namespace,
    NamespaceInfo,
    Subscription,
)
from langgraph_api.metrics_datadog import (
    COUNTER_PROTOCOL_V2_BUFFER_EVICTED,
    COUNTER_PROTOCOL_V2_EVENT_EMITTED,
    COUNTER_PROTOCOL_V2_RESUME_GAP,
    COUNTER_PROTOCOL_V2_TRANSPORT_SEND_FAILURE,
    COUNTER_STREAMING_DATA_LOSS,
    GAUGE_PROTOCOL_V2_BUFFER_SIZE,
    HISTOGRAM_PROTOCOL_V2_REPLAYED_EVENTS,
    get_datadog_metrics_reporter,
)

logger = structlog.stdlib.get_logger(__name__)

SourceEvent = tuple[bytes, bytes, bytes | None]

# Per-source-event scratchpad for deriving stable protocol ``event_id``s from
# the upstream core service's Redis-stream entry id.
#
# The Go core tags every ``StreamEvent`` with a ``stream_id`` (Redis stream
# entry id, format ``<ms>-<seq>``) that is durable, monotonic per run, and
# stable across replicas / replays. Using it as the protocol ``event_id``
# makes client-side dedup stable across SSE reconnects and container
# restarts — no in-memory session sharing required.
#
# A single upstream event can expand into multiple wire envelopes (e.g. values
# with stripped interrupts), so we also carry a source-scoped counter: the first
# envelope gets the raw ``stream_id``, subsequent envelopes get ``{stream_id}.{n}``.
# Both sessions replaying the same upstream tape will emit the same envelopes in
# the same order and therefore mint identical ``event_id``s.
_CURRENT_STREAM_ID: ContextVar[str | None] = ContextVar(
    "_CURRENT_STREAM_ID", default=None
)
_CURRENT_SOURCE_COUNTER: ContextVar[list[int] | None] = ContextVar(
    "_CURRENT_SOURCE_COUNTER", default=None
)


def _synth_event_id(run_id: str, *parts: str) -> str:
    """Build a deterministic ``event_id`` for a session-synthesized event.

    Synthetic events (root ``lifecycle.running`` at ``start()``,
    upstream-forwarded ``lifecycle.started`` (re-emitted by
    :meth:`EventStreamingSession._emit_namespace_started`), terminal
    lifecycle cascades, ``input.requested`` from stripped
    interrupts, etc.) don't have an upstream ``stream_id`` to hand back on
    the wire. We derive their ids from the run id plus a discriminator
    tuple so two sessions replaying the same run mint identical ids. All
    parts are joined with ``|`` to keep the id human-readable for logs.
    """
    suffix = "|".join(parts)
    return f"synth:{run_id}:{suffix}"


class ResumeGap(Exception):
    """Raised when a ``since`` cursor falls behind the session's buffer head.

    Protocol v2 sessions retain a bounded ring buffer of recent events
    (``LSD_PROTOCOL_V2_BUFFER_SIZE``). When a reconnecting client
    passes ``since`` older than the buffer can still reach, the server
    cannot guarantee replay of the missing events and signals a
    ``resume_gap`` protocol error instead of silently delivering a
    truncated window. ``min_available_seq`` is the earliest seq still
    in the buffer; the client must reconcile
    from that point (e.g., fetch fresh state) rather than resume.
    """

    def __init__(self, since: int, min_available_seq: int) -> None:
        super().__init__(
            f"resume gap: since={since} is older than min_available_seq="
            f"{min_available_seq}"
        )
        self.since = since
        self.min_available_seq = min_available_seq


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _is_event_streaming_event(value: Any) -> bool:
    return (
        _is_record(value)
        and value.get("type") == "event"
        and isinstance(value.get("method"), str)
        and _is_record(value.get("params"))
    )


def _metric_event_method(event: dict[str, Any]) -> str:
    method = event.get("method")
    return method if isinstance(method, str) and method else "unknown"


def _stringify_tool_output_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    try:
        return orjson.dumps(content).decode("utf-8")
    except Exception:
        return str(content)


def _is_supported_channel(value: str) -> bool:
    """Return True if *value* is a valid CDDL ``Channel`` token.

    The spec allows the eight fixed channel names (``values``, ``updates``,
    ``messages``, ``tools``, ``custom``, ``lifecycle``, ``input``,
    ``tasks``) plus the open-ended ``custom:<name>`` form
    (``tstr .regexp "custom:.+"``) for user-defined namespaced custom
    channels emitted by stream transformers. ``debug`` was removed
    from the protocol — see
    ``constants.DEFAULT_RUN_STREAM_MODES`` for the rationale.
    """
    if value in SUPPORTED_CHANNELS:
        return True
    return value.startswith("custom:") and len(value) > len("custom:")


def _channels_match(event_channel: str, subscribed: set[str]) -> bool:
    """Match an event's ``method`` against subscription channel tokens.

    Used for everything except ``custom`` events — those need to
    inspect ``params.data.name`` for per-name filtering and are routed
    by :func:`_custom_event_matches` instead. Exact match only here.
    """
    return event_channel in subscribed


def _custom_event_matches(data_name: str | None, subscribed: set[str]) -> bool:
    """Return ``True`` if a ``CustomEvent`` matches the subscription.

    The CDDL ``CustomEvent`` always has ``method: "custom"``; the
    ``data.name`` field (from ``CustomData``) identifies the projection
    a user-defined transformer is pushing to. Subscribers listing:

    * ``"custom"`` — receive every ``CustomEvent`` regardless of name.
    * ``"custom:<name>"`` — receive only events whose ``data.name``
      matches ``<name>``. Events without a ``name`` never match this.
    """
    if "custom" in subscribed:
        return True
    if data_name is None:
        return False
    return f"custom:{data_name}" in subscribed


def _coerce_interrupt_requests(
    interrupts: list[Any] | tuple[Any, ...],
) -> list[dict[str, Any]]:
    """Normalize ``ValuesTransformer`` interrupt entries to input-channel shape.

    Upstream entries are framework ``Interrupt`` objects (serialized as
    dicts with ``id`` + optional ``value``); the wire shape on the input
    channel uses ``interrupt_id`` + optional ``payload``.
    """
    result: list[dict[str, Any]] = []
    for entry in interrupts:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        request: dict[str, Any] = {"interrupt_id": entry["id"]}
        if "value" in entry:
            request["payload"] = entry["value"]
        result.append(request)
    return result


class EventStreamingSession:
    """Normalizes one LangGraph run into protocol events.

    Transport-agnostic: callers provide a ``send()`` callback and an
    optional source async iterable from ``Runs.Stream.join()``.
    """

    def __init__(
        self,
        *,
        run_id: str,
        thread_id: str | None = None,
        initial_run: dict[str, Any],
        get_run: Callable[[], Awaitable[dict[str, Any] | None]],
        get_thread_state: Callable[[], Awaitable[dict[str, Any] | None]] | None = None,
        send: Callable[[str], Awaitable[None] | None],
        source: AsyncIterable[SourceEvent] | None = None,
        max_buffer_size: int | None = None,
    ) -> None:
        if max_buffer_size is None:
            max_buffer_size = LSD_PROTOCOL_V2_BUFFER_SIZE
        if max_buffer_size < 1:
            raise ValueError("max_buffer_size must be a positive integer")
        self._run_id = run_id
        self._thread_id = thread_id
        self._initial_run = initial_run
        self._get_run = get_run
        self._get_thread_state = get_thread_state
        self._send = send
        self._source = source

        self.subscriptions: dict[str, Subscription] = {}
        self._namespaces: dict[str, NamespaceInfo] = {}
        # Namespace keys for which a wire ``lifecycle.started`` has been
        # emitted (one per namespace per run). Tracked separately from
        # ``_namespaces[key].status``, which :meth:`_register_namespaces`
        # also flips to ``"started"`` from non-lifecycle handlers without
        # producing a wire event — wire emission is owned exclusively by
        # :meth:`_emit_namespace_started`, driven by upstream
        # ``lifecycle.started`` events.
        self._wire_started_emitted: set[str] = set()
        self._buffer: list[dict[str, Any]] = []
        self._source_task: asyncio.Task[None] | None = None
        self._next_seq = 0
        self._max_buffer_size = max_buffer_size
        self._root_graph_name = "root"
        self._terminal_lifecycle_emitted = False
        # Maps interrupt_id -> namespace that emitted it, so ``input.respond``
        # from the client can be verified against the originating subgraph.
        self._pending_interrupts: dict[str, Namespace] = {}
        # Latches once the underlying transport starts rejecting sends so
        # we don't spam the logs for every subsequent event on a dead
        # WebSocket / SSE stream. The session is torn down by ``close()``
        # from the route handler when the connection drops.
        self._transport_broken = False
        # Set of ``(namespace_key, checkpoint_id)`` pairs already emitted
        # as ``checkpoints``-channel events. Guards against double-emits
        # when the same ``debug(checkpoint)`` chunk arrives on both the
        # Path-1 (raw source) and Path-2 (pre-normalized) intake routes —
        # e.g. during reconnect replay. See CDDL §12 ``Checkpoint`` and
        # :meth:`_emit_checkpoint_envelope`.
        self._emitted_checkpoints: set[tuple[str, str]] = set()
        # Upstream may fan multiple protocol envelopes out of one durable
        # stream id. Keep the suffix cursor session-wide so separately
        # delivered source tuples do not collide and get dropped by SSE
        # event-id dedup.
        self._stream_id_counts: dict[str, int] = {}

    @property
    def source_task(self) -> asyncio.Task[None] | None:
        """The background task draining the run's source stream, if any.

        Exposed for long-lived transports (SSE / WebSocket) that need to
        observe when the currently-bound run's stream has been fully
        consumed — once it finishes, the transport can poll the thread
        for a newer run (e.g. a resume spawned by ``input.respond``) and
        rebind. ``None`` when the session has no source (attached to a
        completed run replay), hasn't started yet, or was built for
        normalize-only fan-in (Phase 8).
        """
        return self._source_task

    @property
    def next_seq(self) -> int:
        """Highest ``seq`` assigned by this session so far.

        Callers (typically :class:`ThreadRunManager`) read this when
        rebinding to a newer run so the successor session can continue
        numbering events monotonically — SSE deduplicates by
        ``event_id`` (``str(seq)``), so restarting at 0 would cause the
        dedup set to drop resumed-run events whose ids collide with the
        completed run's.
        """
        return self._next_seq

    def set_initial_seq(self, value: int) -> None:
        """Seed the session's ``_next_seq`` cursor.

        Used when :class:`ThreadRunManager` rebinds a transport-scoped
        session to a fresh run (``input.respond`` resume): we carry the
        prior session's ``_next_seq`` into the new one so ``event_id``
        values keep growing monotonically. Must be called before
        :meth:`start` — post-start seeding would risk a seq lower than
        one already handed out by the session, breaking the ``since``
        resume cursor's monotonic invariant.
        """
        if value > self._next_seq:
            self._next_seq = value

    async def start(self) -> None:
        """Seed root lifecycle and begin consuming the source stream.

        The seed is always ``running`` regardless of the run row's
        current status. When historical replay is the source — e.g. an
        SSE/WS observer attaches after the run has finished — the run
        row reads ``"success"`` and a literal mapping would push
        ``lifecycle.completed`` ahead of the replayed events, telling
        clients the run is done before they see how it got there. The
        actual terminal status is delivered by
        :meth:`_emit_terminal_lifecycle` once the source stream's
        ``run_done`` metadata arrives, which keeps the SDK's
        ``isLoading`` flip to ``false`` aligned with the end of the
        replay rather than the beginning.
        """
        config = self._initial_run.get("kwargs", {}).get("config", {})
        configurable = config.get("configurable", {}) if _is_record(config) else {}
        self._root_graph_name = (
            configurable.get("graph_id")
            if isinstance(configurable.get("graph_id"), str)
            else self._initial_run.get("assistant_id", "root")
        )

        initial_status = "running"
        self._set_namespace_info(
            [],
            initial_status,
            graph_name=self._root_graph_name,
        )
        await self._push_event(
            self._create_event(
                "lifecycle",
                [],
                {
                    "event": initial_status,
                    "graph_name": self._root_graph_name,
                },
                event_id=_synth_event_id(
                    self._run_id,
                    "lc",
                    to_namespace_key([]),
                    initial_status,
                ),
            )
        )

        if self._source is not None:
            self._source_task = asyncio.create_task(self._consume_source())

    async def close(self) -> None:
        """Stop consuming and wait for queued writes to settle."""
        if self._source_task is not None:
            self._source_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._source_task

    async def complete(self) -> None:
        """Emit terminal lifecycle when an external source reports run completion."""
        await self._emit_terminal_lifecycle()

    async def handle_event_streaming_command(
        self,
        command: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Route a structured protocol command and return a typed response."""
        try:
            method = command.get("method", "")
            if method == "subscription.subscribe":
                return await self._handle_subscribe_for_response(command, meta)
            if method == "subscription.unsubscribe":
                return await self._handle_unsubscribe_for_response(command, meta)
            if method == "agent.getTree":
                return self._success(
                    command["id"], {"tree": self._build_tree([])}, meta
                )
            if method == "subscription.reconnect":
                return await self._handle_reconnect_for_response(command, meta)
            return self._error(
                command["id"],
                "unknown_command",
                f"Unknown protocol command: {method}",
                meta,
            )
        except Exception as exc:
            return self._error(
                command.get("id"),
                "unknown_error",
                str(exc),
                meta,
            )

    async def ingest_source_event(self, event: SourceEvent) -> None:
        """Inject a raw stream event for normalization."""
        await self._handle_source_event(event)

    # ------------------------------------------------------------------
    # Source consumption
    # ------------------------------------------------------------------

    async def _consume_source(self) -> None:
        try:
            async for event in self._source or ():
                await self._handle_source_event(event)
        except Exception as exc:
            await self._send_error(None, "unknown_error", str(exc))
        finally:
            await self._emit_terminal_lifecycle()

    async def _resolve_effective_status(self, run_status: str) -> str:
        """Map a run status to its protocol lifecycle status.

        "success" with pending interrupts on the thread state is
        upgraded to "interrupted" — otherwise clients don't see the
        distinction and assume the run completed normally. Shared by
        ``start()`` (for the initial lifecycle event) and
        ``_emit_terminal_lifecycle`` so both paths agree.
        """
        status = to_lifecycle_status(run_status)
        if status != "completed" or self._get_thread_state is None:
            return status
        try:
            thread_state = await self._get_thread_state()
            if thread_state is None:
                return status
            # ``Threads.State.get`` returns a ``StateSnapshot`` NamedTuple
            # over gRPC and a plain ``dict`` when serialized through the
            # REST API — accept both so this helper is transport-agnostic.
            tasks = (
                thread_state.get("tasks")
                if _is_record(thread_state)
                else getattr(thread_state, "tasks", None)
            ) or ()
            for task in tasks:
                interrupts = (
                    task.get("interrupts")
                    if _is_record(task)
                    else getattr(task, "interrupts", None)
                )
                if isinstance(interrupts, (list, tuple)) and len(interrupts) > 0:
                    return "interrupted"
            return status
        except Exception:
            return status

    async def _emit_terminal_lifecycle(self) -> None:
        if self._terminal_lifecycle_emitted:
            return
        current_run = await self._get_run()
        if current_run is None:
            self._terminal_lifecycle_emitted = True
            return

        status = await self._resolve_effective_status(
            current_run.get("status", "pending")
        )

        if status == "running":
            return

        self._terminal_lifecycle_emitted = True
        # Flush ``lifecycle: completed`` for every still-open subgraph
        # namespace before the root's terminal event. Without this,
        # any namespace whose last event didn't naturally transition
        # out of its own subtree (``tools`` as the final step, a lone
        # root node, etc.) would never receive its ``completed`` frame
        # — the initial streaming report saw only 10 lifecycle events
        # where the JS reference produced 30.
        await self._complete_stale_namespaces([])
        await self._emit_namespace_lifecycle(
            [], status, graph_name=self._root_graph_name
        )

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    async def _handle_source_event(self, event: SourceEvent) -> None:
        event_bytes, message_bytes, stream_id_bytes = event
        event_name = event_bytes.decode("utf-8")

        if event_name == "metadata":
            return
        if event_name == "error":
            self._terminal_lifecycle_emitted = True
            try:
                error_data = orjson.loads(message_bytes)
                error_msg = (
                    error_data.get("message", str(error_data))
                    if _is_record(error_data)
                    else str(error_data)
                )
            except Exception:
                error_msg = message_bytes.decode("utf-8", errors="replace")
            await self._emit_namespace_lifecycle(
                [], "failed", graph_name=self._root_graph_name, error=error_msg
            )
            return

        method, raw_namespace = parse_event_name(event_name)
        namespace: Namespace = raw_namespace

        data = orjson.loads(message_bytes)

        # Scope the upstream Redis stream entry id to this ingest step so
        # every envelope minted while normalizing this source event (the
        # primary event plus any session-synthesized side-effects the
        # branches below emit) can derive a stable ``event_id`` from it.
        # See :func:`_assign_event_id` for the counter semantics.
        stream_id = (
            stream_id_bytes.decode("utf-8")
            if isinstance(stream_id_bytes, (bytes, bytearray)) and stream_id_bytes
            else None
        )
        sid_token = _CURRENT_STREAM_ID.set(stream_id)
        cnt_token = _CURRENT_SOURCE_COUNTER.set(
            [self._stream_id_counts.get(stream_id, 0)] if stream_id else [0]
        )
        try:
            await self._dispatch_source_event(method, namespace, data)
        finally:
            counter = _CURRENT_SOURCE_COUNTER.get()
            if stream_id is not None and counter is not None:
                self._stream_id_counts[stream_id] = counter[0]
            _CURRENT_SOURCE_COUNTER.reset(cnt_token)
            _CURRENT_STREAM_ID.reset(sid_token)

    async def _dispatch_source_event(
        self,
        method: str,
        namespace: Namespace,
        data: Any,
    ) -> None:
        """Fan out a parsed upstream event by method to the correct normalizer.

        Separated from :meth:`_handle_source_event` so the
        ``_CURRENT_STREAM_ID`` / ``_CURRENT_SOURCE_COUNTER`` ContextVar
        scope cleanly wraps every push triggered by this source event.
        """

        # Core ``langgraph.stream`` already emits canonical ProtocolEvent
        # objects. Re-envelope them with this session's seq/event_id so
        # transport replay remains API-owned.
        if _is_event_streaming_event(data):
            await self._handle_normalized_event(method, namespace, data)
            return

        if method == "lifecycle":
            await self._handle_lifecycle_event(namespace, data)
            return

        if method != "messages":
            self._register_namespaces(namespace)

        if method == "values":
            input_requests, cleaned_values = strip_interrupts_from_values(data)
            await self._emit_input_requested_events(namespace, input_requests)
            if self._has_state_payload(cleaned_values):
                await self._push_event(
                    self._create_event("values", namespace, cleaned_values)
                )
        elif method == "messages":
            if namespace:
                self._register_namespaces(namespace)
            if _is_record(data) and isinstance(data.get("event"), str):
                await self._push_event(self._create_event("messages", namespace, data))
        elif method == "updates":
            normalized = normalize_updates_data(data)
            node = normalized.get("node")
            if node == "__interrupt__":
                await self._emit_input_requested_events(
                    namespace, normalize_input_requested_data(data)
                )
                return
            input_requests, cleaned_values = strip_interrupts_from_values(
                normalized.get("values")
            )
            await self._emit_input_requested_events(namespace, input_requests)
            if self._has_state_payload(cleaned_values):
                await self._push_event(
                    self._create_event(
                        "updates",
                        namespace,
                        cleaned_values,
                        node=node,
                    )
                )
            # Close the matching child namespace: the task for ``node``
            # has completed at this level. See :meth:`_emit_child_node_completed`.
            if isinstance(node, str) and node:
                await self._emit_child_node_completed(namespace, node)
        elif method == "custom":
            await self._push_event(
                self._create_event("custom", namespace, {"payload": data})
            )
        elif method == "tasks":
            await self._push_event(self._create_event("tasks", namespace, data))
        elif method == "checkpoints":
            # Dedicated ``checkpoints`` channel (CDDL §12 ``Checkpoint``,
            # introduced in ``@langchain/protocol@0.0.11``). LangGraph
            # core emits ``{id, parent_id?, step, source}`` envelopes
            # natively when ``"checkpoints"`` is in ``stream_mode``; the
            # session forwards them after duplicate-emit guarding.
            if _is_record(data) and isinstance(data.get("id"), str):
                await self._emit_checkpoint_envelope(
                    namespace, cast("dict[str, Any]", data)
                )
            return
        elif method == "tools":
            # Upstream ``StreamToolCallHandler`` emits already-protocol-shaped
            # ``{event: "tool-*", tool_call_id, ...}`` dicts on the ``tools``
            # channel; forward as-is. An optional out-of-band ``__node`` key
            # carries the producing graph node; promote it to ``params.node``.
            node: str | None = None
            if _is_record(data):
                raw_node = data.pop("__node", None)
                if isinstance(raw_node, str) and raw_node:
                    node = raw_node
            await self._push_event(
                self._create_event("tools", namespace, data, node=node)
            )
            await self._emit_tool_output_message(namespace, data)

    async def _handle_normalized_event(
        self,
        method_from_name: str,
        namespace_from_name: Namespace,
        event: dict[str, Any],
    ) -> None:
        """Ingest a ``ProtocolEvent`` pre-normalized by ``StreamingHandler``.

        This path handles the side-effects that upstream's mux does not
        produce (subgraph lifecycle, ``params.interrupts`` splitting,
        debug→checkpoint capture) and then delegates the final wire-shape
        envelope building to
        :meth:`_create_event` — the same primitive Path 1 uses. The
        two paths share a single wire-shaping authority so they cannot
        drift on seq/event_id/timestamp/custom:<name> / normalization
        semantics.

        Upstream's ``_seq`` / ``event_id`` fields on the incoming
        envelope are dropped; this session's monotonic counter is
        authoritative. Upstream's ``timestamp`` is preserved when
        present so Path 2 keeps the transformer-assigned value.
        """
        params = event.get("params") or {}
        raw_namespace = params.get("namespace")
        if isinstance(raw_namespace, list) and all(
            isinstance(s, str) for s in raw_namespace
        ):
            namespace: Namespace = list(raw_namespace)
        else:
            namespace = namespace_from_name

        method = event.get("method") or method_from_name
        if not isinstance(method, str):
            return

        # Dedicated ``checkpoints`` channel (CDDL §12 ``Checkpoint``,
        # added in ``@langchain/protocol@0.0.11``). Inbound pre-normalized
        # ``checkpoints`` events are forwarded verbatim after
        # duplicate-emit guarding.
        if method == "checkpoints":
            data = params.get("data")
            if _is_record(data) and isinstance(data.get("id"), str):
                await self._emit_checkpoint_envelope(
                    namespace, cast("dict[str, Any]", data)
                )
            return

        if method == "lifecycle":
            await self._handle_lifecycle_event(namespace, params.get("data"))
            return

        # Track previously unseen namespace prefixes in the session
        # registry so child-completion / terminal-cascade logic has
        # state to operate on. Wire ``lifecycle.started`` events come
        # from upstream via :meth:`_emit_namespace_started`; this path
        # never synthesizes one.
        if namespace:
            self._register_namespaces(namespace)

        data = params.get("data")

        # Surface pending interrupts on the ``input`` channel. This branch
        # exists for the JS sidecar (hybrid Python API + JS graph) path:
        # the sidecar's v3 stream emits already-normalized ProtocolEvents
        # that reach this handler, and it carries tool-raised interrupts
        # (headless tools, human-in-the-loop) inline on ``updates`` /
        # ``values`` events rather than on a dedicated channel. Without
        # converting them into an ``input`` event here, the SDK renders the
        # interrupting tool call but never learns the interrupt id, so it
        # can't execute or resume the run. The carrier shape varies, so we
        # accept all of them:
        #   * ``params.interrupts`` — native ValuesTransformer convention.
        #   * an ``updates`` event with ``node == "__interrupt__"`` whose
        #     ``data.values`` is the interrupt array — the JS sidecar shape.
        #   * the ``__interrupt__`` key on a ``values`` snapshot payload.
        # ``_emit_input_requested_events`` dedupes by interrupt id, so
        # overlapping shapes for one interrupt collapse to a single event.
        if method == "updates":
            update_node = params.get("node")
            if not isinstance(update_node, str) and _is_record(data):
                candidate = data.get("node")
                update_node = candidate if isinstance(candidate, str) else None
            if update_node == "__interrupt__":
                interrupt_array = data.get("values") if _is_record(data) else None
                await self._emit_input_requested_events(
                    namespace, normalize_input_requested_data(interrupt_array)
                )
                # The ``__interrupt__`` update is purely the interrupt
                # signal, not real state — it's consumed by the ``input``
                # channel above and must not also be forwarded as a plain
                # ``updates`` event.
                return
        elif method == "values":
            # Always strip ``__interrupt__`` from the payload so it never
            # leaks into the forwarded ``values`` event, regardless of
            # which carrier the interrupt also rode in on. Surface
            # interrupts from both carriers — ``_emit_input_requested_events``
            # dedupes by interrupt id, so a single interrupt arriving on
            # both collapses to one event.
            input_requests, data = strip_interrupts_from_values(data)
            interrupts = params.get("interrupts")
            if isinstance(interrupts, (list, tuple)) and interrupts:
                await self._emit_input_requested_events(
                    namespace, _coerce_interrupt_requests(interrupts)
                )
            if input_requests:
                await self._emit_input_requested_events(namespace, input_requests)

        # Delegate envelope construction to the shared primitive. This
        # is the unified wire-shaping step; any envelope change lives
        # in ``_create_event`` and both paths follow.
        raw_timestamp = params.get("timestamp")
        timestamp = raw_timestamp if isinstance(raw_timestamp, int) else None

        # Forward the producing graph node on the wire envelope to keep
        # Path 2 at parity with Path 1 (:meth:`_handle_source_event`).
        # Upstream may attach ``node`` on the envelope params directly,
        # embed it inside the ``updates`` payload, or tag ``tools``
        # events with an out-of-band ``__node`` key — check all three.
        node: str | None = None
        raw_node = params.get("node")
        if isinstance(raw_node, str) and raw_node:
            node = raw_node
        elif method == "updates" and _is_record(data):
            candidate = data.get("node")
            if isinstance(candidate, str) and candidate:
                node = candidate
        elif method == "tools" and _is_record(data):
            candidate = data.pop("__node", None)
            if isinstance(candidate, str) and candidate:
                node = candidate

        await self._push_event(
            self._create_event(
                method,
                namespace,
                data,
                node=node,
                timestamp=timestamp,
            )
        )
        if method == "tools" and _is_record(data):
            await self._emit_tool_output_message(namespace, data)
        # For ``updates`` events carrying a ``node`` name, close the
        # corresponding child namespace. Mirrors the raw-ingest path in
        # :meth:`_handle_source_event` — see :meth:`_emit_child_node_completed`.
        if method == "updates" and node is not None:
            await self._emit_child_node_completed(namespace, node)

    async def _emit_tool_output_message(
        self, namespace: Namespace, data: dict[str, Any]
    ) -> None:
        """Mirror completed tool outputs onto the ``messages`` channel.

        JS emits a ``tool`` role message for every tool result in addition to
        the structured ``tools`` event. Python's native tools channel already
        carries the result, but the message feed needs the mirrored event so
        scoped ``useMessages`` subscriptions see the same tool chatter.
        """
        if data.get("event") != "tool-finished":
            return
        output = data.get("output")
        if not _is_record(output):
            return

        tool_call_id = data.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            raw_tool_call_id = output.get("tool_call_id")
            tool_call_id = (
                raw_tool_call_id if isinstance(raw_tool_call_id, str) else None
            )

        raw_message_id = output.get("id")
        message_id = (
            raw_message_id
            if isinstance(raw_message_id, str) and raw_message_id
            else f"tool-{tool_call_id}"
            if tool_call_id
            else None
        )
        if message_id is None:
            return

        content = _stringify_tool_output_content(output.get("content"))
        start: dict[str, Any] = {
            "event": "message-start",
            "role": "tool",
            "id": message_id,
        }
        if tool_call_id is not None:
            start["tool_call_id"] = tool_call_id

        message_namespace = (
            [*namespace, f"tools:{tool_call_id}"]
            if tool_call_id is not None
            else namespace
        )

        for event_data in (
            start,
            {
                "event": "content-block-start",
                "index": 0,
                "content": {"type": "text", "text": ""},
            },
            {
                "event": "content-block-delta",
                "index": 0,
                "content": {"type": "text", "text": content},
            },
            {
                "event": "content-block-finish",
                "index": 0,
                "content": {"type": "text", "text": content},
            },
            {"event": "message-finish", "metadata": {}},
        ):
            await self._push_event(
                self._create_event("messages", message_namespace, event_data)
            )

    # ------------------------------------------------------------------
    # Event creation and delivery
    # ------------------------------------------------------------------

    def _create_event(
        self,
        method: str,
        namespace: Namespace,
        data: Any,
        *,
        node: str | None = None,
        timestamp: int | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Build a single ``ProtocolEvent`` envelope.

        This is the **single wire-shaping authority** for the session:
        both the raw-astream ingest (``_handle_source_event``) and the
        pre-normalized StreamingHandler ingest (``_handle_normalized_event``)
        funnel through this method, so their outputs cannot drift on
        wire shape. Behavior changes (new params, rename, etc.) must
        land here — never in the ingest paths — so both Path 1 and
        Path 2 move together.

        ``timestamp`` lets Path 2 preserve the upstream transformer's
        timestamp; Path 1 omits it and gets a fresh server-side value.

        ``event_id`` lets synthetic-event callers (lifecycle, input,
        checkpoint, message-finalize) pass a deterministic id derived
        from stable inputs (``run_id`` + namespace/status/interrupt_id)
        so two sessions replaying the same run mint identical ids.
        When not passed, :meth:`_assign_event_id` consults the
        ``_CURRENT_STREAM_ID`` ContextVar populated by the ingest
        wrapper to anchor the id to the upstream Redis stream entry id
        — making client-side dedup stable across SSE reconnects.
        """
        self._next_seq += 1

        # Normalize ``custom:<name>`` pushes from ``StreamChannel`` /
        # user ``StreamTransformer`` into the spec-compliant
        # ``CustomEvent`` shape (``method: "custom"`` +
        # ``data: {name, payload}``). Upstream StreamMux._forward
        # emits the channel name as the method prefix; the CDDL
        # ``CustomEvent`` type (``protocol.cddl``, ``py/langchain_protocol``
        # and ``js/protocol.ts``) expects the normalized form. Also
        # keeps ``custom:<name>`` subscription filtering in
        # ``_matches_subscription`` working via ``data.name``.
        # TODO: remove this branch once upstream emits ``CustomEvent``
        # directly; ``method == "custom"`` + ``data = {name, payload}``
        # will already match.
        if method.startswith("custom:"):
            channel_name = method[len("custom:") :]
            if channel_name:
                data = {"name": channel_name, "payload": data}
                method = "custom"

        event_method = "input.requested" if method == "input" else method
        normalized_data = (
            normalize_event_streaming_state_payload(data)
            if method in ("values", "updates")
            else data
        )
        params: dict[str, Any] = {
            "namespace": namespace,
            "timestamp": (
                timestamp if isinstance(timestamp, int) else int(time.time() * 1000)
            ),
            "data": normalized_data,
        }
        if node is not None:
            params["node"] = node
        resolved_event_id = (
            event_id if event_id is not None else self._assign_event_id()
        )
        return {
            "type": "event",
            "event_id": resolved_event_id,
            "seq": self._next_seq,
            "method": event_method,
            "params": params,
        }

    def _assign_event_id(self) -> str:
        """Derive a stable ``event_id`` from the current ingest scope.

        When the call happens inside :meth:`_handle_source_event` (the
        primary path), :attr:`_CURRENT_STREAM_ID` carries the upstream
        Redis stream entry id for the source event being normalized.
        The first envelope minted for that source event reuses the
        stream id verbatim; subsequent envelopes append ``.{n}`` from
        :attr:`_CURRENT_SOURCE_COUNTER`. Two sessions replaying the
        same run observe the same upstream tape and therefore mint
        identical ids for every envelope.

        Outside an ingest scope (``start()`` before the source is consumed or
        ``_emit_terminal_lifecycle`` after the source closes), callers must pass an explicit
        ``event_id``. If they don't we fall back to
        ``synth:{run_id}:{seq}``, which is deterministic across two
        sessions that consume the same source tape in the same order
        but is *not* stable under refactors — so the explicit-id path
        is strongly preferred.
        """
        upstream = _CURRENT_STREAM_ID.get()
        counter = _CURRENT_SOURCE_COUNTER.get()
        if upstream is not None and counter is not None:
            idx = counter[0]
            counter[0] = idx + 1
            return upstream if idx == 0 else f"{upstream}.{idx}"
        return _synth_event_id(self._run_id, str(self._next_seq))

    async def _push_event(self, event: dict[str, Any]) -> None:
        self._buffer.append(event)
        reporter = get_datadog_metrics_reporter()
        if len(self._buffer) > self._max_buffer_size:
            evicted = len(self._buffer) - self._max_buffer_size
            self._buffer = self._buffer[-self._max_buffer_size :]
            # Signal each buffer eviction as streaming data loss so
            # operators can correlate reconnect failures with chatty
            # graphs overflowing the replay window. The reason tag
            # distinguishes this from publish-side losses in
            # ``stream.py``. Metric failures must never break the
            # event path, so wrap best-effort.
            try:
                reporter.inc_counter(
                    COUNTER_PROTOCOL_V2_BUFFER_EVICTED,
                    value=evicted,
                )
                reporter.inc_counter(
                    COUNTER_STREAMING_DATA_LOSS,
                    value=evicted,
                    attributes={"reason": "protocol_v2_buffer_overflow"},
                )
            except Exception:
                logger.debug(
                    "Failed to record buffer overflow metric",
                    exc_info=True,
                )

        try:
            reporter.inc_counter(
                COUNTER_PROTOCOL_V2_EVENT_EMITTED,
                attributes={"method": _metric_event_method(event)},
            )
            reporter.record_gauge(
                GAUGE_PROTOCOL_V2_BUFFER_SIZE,
                float(len(self._buffer)),
            )
        except Exception:
            logger.debug("Failed to record protocol v2 buffer metric", exc_info=True)

        # All subscriptions on a session share the same underlying transport
        # (``self._send``), so deliver each event at most once even when
        # multiple subscription filters match. The client deduplicates by
        # ``event_id``; redelivering the same event would break ordering
        # guarantees and double-count ``replayed_events``.
        for subscription in self.subscriptions.values():
            if not subscription.active:
                continue
            if not self._matches_subscription(subscription, event):
                continue
            await self._send_json(event)
            return

    async def install_subscription_with_replay(
        self,
        subscription: Subscription,
        *,
        since: int | None = None,
    ) -> int:
        """Install *subscription* and replay matching buffered events.

        The subscription is registered as inactive, the event buffer is
        drained (snapshot + late arrivals) through the session's send
        callback for events with ``seq > since`` that match the filter,
        then the subscription is activated so live events flow.

        Returns the number of events replayed. Used by both the WebSocket
        ``subscription.subscribe`` command and the SSE event stream, which
        accept a body ``since`` cursor to resume after a reconnect.

        Raises :class:`ResumeGap` when *since* is older than the
        session's retained buffer head. This lets callers surface a
        ``resume_gap`` protocol error instead of silently delivering a
        truncated event range. Only non-negative ``since`` values are
        gap-checked; ``since=None`` or ``since=0`` are always valid.
        """
        # Gap check: the ring buffer may have evicted events with
        # ``seq <= since``'s successor. When that happens, the client's
        # local history is authoritative up to ``since`` but cannot be
        # reconciled against the server's stream without a refetch, so
        # we fail loud rather than deliver a partial replay. The check
        # uses the lowest seq still in the buffer as the "earliest we
        # can replay" watermark. If the buffer is empty (nothing ever
        # emitted), any ``since`` is trivially satisfiable.
        if since is not None and self._buffer:
            min_available_seq = self._buffer[0].get("seq", 0)
            if isinstance(min_available_seq, int) and since + 1 < min_available_seq:
                try:
                    get_datadog_metrics_reporter().inc_counter(
                        COUNTER_PROTOCOL_V2_RESUME_GAP
                    )
                except Exception:
                    logger.debug("Failed to record resume gap metric", exc_info=True)
                raise ResumeGap(since=since, min_available_seq=min_available_seq)

        subscription.active = False
        self.subscriptions[subscription.id] = subscription

        cursor = since if since is not None else 0
        replayed = 0
        snapshot_seq = self._next_seq
        while True:
            drain = [
                e
                for e in self._buffer
                if cursor < e.get("seq", 0) <= snapshot_seq
                and self._matches_subscription(subscription, e)
            ]
            if not drain:
                break
            for event in drain:
                await self._send_json(event)
                replayed += 1
            cursor = drain[-1].get("seq", cursor)
            # If new events arrived during replay, extend the horizon so
            # we don't skip them when flipping to live.
            if self._next_seq > snapshot_seq:
                snapshot_seq = self._next_seq
                continue
            break

        subscription.active = True
        try:
            get_datadog_metrics_reporter().record_histogram(
                HISTOGRAM_PROTOCOL_V2_REPLAYED_EVENTS,
                float(replayed),
            )
        except Exception:
            logger.debug("Failed to record replay metric", exc_info=True)
        return replayed

    def _matches_subscription(
        self, subscription: Subscription, event: dict[str, Any]
    ) -> bool:
        raw_method = event.get("method", "")
        channel = "input" if raw_method == "input.requested" else raw_method
        if not _is_supported_channel(channel):
            return False
        if channel == "custom":
            # CDDL ``CustomEvent`` dispatches on ``data.name``; the
            # ``custom:<name>`` channel token filters by that name, and
            # the plain ``"custom"`` token receives everything.
            data = event.get("params", {}).get("data")
            data_name = data.get("name") if isinstance(data, dict) else None
            if not _custom_event_matches(data_name, subscription.channels):
                return False
        elif not _channels_match(channel, subscription.channels):
            return False

        ns = event.get("params", {}).get("namespace", [])
        if subscription.namespaces is None or not subscription.namespaces:
            return True
        return any(
            is_prefix_match(ns, prefix)
            and (
                subscription.depth is None
                or len(ns) - len(prefix) <= subscription.depth
            )
            for prefix in subscription.namespaces
        )

    async def _send_json(self, message: dict[str, Any]) -> None:
        try:
            payload = orjson.dumps(message).decode("utf-8")
        except Exception:
            # Non-serializable payloads reaching this point are a bug in
            # a normalizer — always surface them at warning level with
            # the event metadata so we can trace the offending event.
            # The event is still buffered for replay before this call.
            await logger.awarning(
                "Protocol event failed to serialize; dropping",
                event_id=message.get("event_id"),
                seq=message.get("seq"),
                method=message.get("method"),
                exc_info=True,
            )
            return

        # Short-circuit after the first transport failure. Events continue
        # to accumulate in ``self._buffer`` so a reconnecting subscriber
        # can replay them with the body ``since`` cursor.
        if self._transport_broken:
            return

        try:
            result = self._send(payload)
            if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                await result
        except Exception as exc:
            # Log once per session so a dropped client doesn't spam the
            # logs on every subsequent event. ``close()`` tears the
            # session down shortly after the first failure.
            self._transport_broken = True
            try:
                get_datadog_metrics_reporter().inc_counter(
                    COUNTER_PROTOCOL_V2_TRANSPORT_SEND_FAILURE,
                    attributes={"method": _metric_event_method(message)},
                )
            except Exception:
                logger.debug("Failed to record transport failure metric", exc_info=True)
            await logger.adebug(
                "Protocol transport send failed; marking session broken until close()",
                event_id=message.get("event_id"),
                seq=message.get("seq"),
                method=message.get("method"),
                error=str(exc),
            )

    async def _send_error(
        self,
        cmd_id: int | None,
        error: str,
        message: str,
    ) -> None:
        await self._send_json(
            {"type": "error", "id": cmd_id, "error": error, "message": message}
        )

    # ------------------------------------------------------------------
    # Checkpoints channel
    # ------------------------------------------------------------------

    async def _emit_checkpoint_envelope(
        self,
        namespace: Namespace,
        envelope: dict[str, Any],
    ) -> None:
        """Push a CDDL ``Checkpoint`` envelope onto the ``checkpoints`` channel.

        Called from the ``checkpoints`` intake in
        :meth:`_handle_source_event` / :meth:`_handle_normalized_event`.
        LangGraph core emits pre-shaped ``Checkpoint`` envelopes natively
        when ``"checkpoints"`` is in ``stream_mode``; this method
        forwards them after de-dup.

        Upstream replay of the same envelope (same ``(namespace, id)``
        pair) on both Path 1 and Path 2 — or across a reconnect — would
        otherwise cause duplicate ``checkpoints`` events; the
        ``_emitted_checkpoints`` set is a per-session de-dup guard keyed
        on ``(namespace_key, checkpoint_id)``. An in-session ``fork`` can
        legitimately replay the same id only after a distinct step bump,
        which current runtimes don't exercise; if that changes, widen
        the key to include ``step``.
        """
        cp_id = envelope.get("id")
        if not isinstance(cp_id, str):
            return
        ns_key = to_namespace_key(namespace)
        key = (ns_key, cp_id)
        if key in self._emitted_checkpoints:
            return
        self._emitted_checkpoints.add(key)
        await self._push_event(
            self._create_event(
                "checkpoints",
                namespace,
                envelope,
                event_id=_synth_event_id(self._run_id, "checkpoint", ns_key, cp_id),
            )
        )

    # ------------------------------------------------------------------
    # Namespace management
    # ------------------------------------------------------------------

    async def _handle_lifecycle_event(self, namespace: Namespace, data: Any) -> None:
        """Forward core lifecycle events while keeping root lifecycle API-owned."""
        if not _is_record(data) or not isinstance(data.get("event"), str):
            if namespace:
                self._register_namespaces(namespace)
            return

        # ``LifecycleTransformer`` runs at the mux's root scope and pushes
        # one flat lifecycle stream covering every subgraph at any depth.
        # The wire envelope's ``params.namespace`` reflects the
        # transformer's scope (``[]`` for root) while the announced
        # subgraph identity lives in ``data.namespace`` per the
        # LifecyclePayload contract. Promote the data namespace when it
        # is strictly deeper so subagent ``started`` / ``completed``
        # events are not mistaken for root-scope lifecycle (which the
        # session owns separately via ``_emit_terminal_lifecycle``).
        raw_data_ns = data.get("namespace")
        if (
            isinstance(raw_data_ns, list)
            and all(isinstance(seg, str) for seg in raw_data_ns)
            and len(raw_data_ns) > len(namespace)
        ):
            namespace = list(raw_data_ns)

        event_type = data["event"]
        # Root terminal status is owned by `_emit_terminal_lifecycle`, which
        # resolves interrupts and run failure state. Root `started`/`running`
        # is seeded by `start()`.
        if not namespace:
            return

        if event_type == "started":
            await self._emit_namespace_started(namespace, data)
            return

        if event_type in ("running", "completed", "failed", "interrupted"):
            raw_graph_name = data.get("graph_name")
            raw_error = data.get("error")
            raw_cause = data.get("cause")
            await self._emit_namespace_lifecycle(
                namespace,
                event_type,
                graph_name=raw_graph_name if isinstance(raw_graph_name, str) else None,
                error=raw_error if isinstance(raw_error, str) else None,
                cause=raw_cause if _is_record(raw_cause) else None,
            )
            return

        self._register_namespaces(namespace)

    def _register_namespaces(self, namespace: Namespace) -> None:
        """Track each prefix of *namespace* as ``started`` in the session
        registry without synthesizing a wire ``lifecycle.started`` event.

        Wire announcements are owned exclusively by upstream — the langgraph
        ``LifecycleTransformer`` emits ``lifecycle.started`` for every
        spawned subgraph, and :meth:`_emit_namespace_started` forwards
        those to the wire with all upstream metadata (``graph_name``,
        ``trigger_call_id``, ``cause``) intact. Earlier versions of this
        method also synthesized stand-in ``started`` events from
        non-lifecycle handlers, which raced with the upstream emission and
        stripped its ``cause`` because the synth ran before the cause
        arrived. Now we only mark internal state so terminal cascades
        (``_emit_terminal_lifecycle``) and child-completion logic
        (``_emit_child_node_completed``) can still reason about open
        namespaces.

        Closing rules are unchanged: a namespace stays ``started`` until
        either (a) a parent ``updates`` event closes it via
        ``_emit_child_node_completed``, or (b) the run's terminal
        lifecycle cascades the whole tree.
        """
        for length in range(1, len(namespace) + 1):
            partial = namespace[:length]
            key = to_namespace_key(partial)
            info = self._namespaces.get(key)
            if info is not None and info.status == "started":
                continue
            graph_name = (
                info.graph_name if info is not None else guess_graph_name(partial)
            )
            self._set_namespace_info(partial, "started", graph_name=graph_name)

    async def _emit_namespace_started(
        self, namespace: Namespace, data: dict[str, Any]
    ) -> None:
        """Forward an upstream ``lifecycle.started`` to the wire.

        Upstream (``langgraph.stream.transformers.LifecycleTransformer``)
        owns the payload shape — ``event``, ``graph_name``,
        ``trigger_call_id``, optional ``cause``, etc. The session attaches
        the wire envelope (``event_id`` from the upstream stream-id
        ContextVar, ``seq``, ``timestamp``) and pushes; nothing about the
        payload is reconstructed here.

        Idempotent: a single ``started`` per namespace per run reaches
        the wire even if upstream emits the event multiple times when
        transformers compose. Wire-emit state is tracked separately
        from the ``_namespaces`` registry, which
        :meth:`_register_namespaces` also marks ``started`` from
        non-lifecycle event handlers without ever emitting on the wire.
        """
        if not namespace:
            return
        key = to_namespace_key(namespace)
        if key in self._wire_started_emitted:
            return
        self._wire_started_emitted.add(key)
        upstream_graph_name = data.get("graph_name")
        self._set_namespace_info(
            namespace,
            "started",
            graph_name=upstream_graph_name
            if isinstance(upstream_graph_name, str)
            else None,
        )
        await self._push_event(self._create_event("lifecycle", namespace, data))

    async def _emit_child_node_completed(
        self, parent_namespace: Namespace, node_name: str
    ) -> None:
        """Close the child namespace that corresponds to ``node_name``.

        LangGraph emits an ``updates`` event on the PARENT namespace with
        ``{node: "<name>"}`` after a node's task completes. That is our
        cue that the matching ``[*parent, "<name>:<task_id>"]`` child
        namespace is done — otherwise child namespaces stay in
        ``"started"`` until the run terminates and the whole tree is
        cascade-completed at once.

        For parallel fan-outs of the same node name, we close the oldest
        still-open matching child first; LangGraph emits one ``updates``
        per completed task, so repeated calls drain the bucket in order.
        Mirrors ``emitChildNodeCompleted`` in the JS reference.
        """
        if not node_name or node_name.startswith("__"):
            return
        prefix = f"{node_name}:"
        parent_len = len(parent_namespace)
        # ``self._namespaces`` is a dict, iterated in insertion order;
        # the first still-``started`` match is therefore the oldest
        # pending invocation.
        for info in self._namespaces.values():
            ns = info.namespace
            if len(ns) != parent_len + 1:
                continue
            if info.status != "started":
                continue
            parent_matches = True
            for i in range(parent_len):
                if ns[i] != parent_namespace[i]:
                    parent_matches = False
                    break
            if not parent_matches:
                continue
            last = ns[-1]
            if last != node_name and not last.startswith(prefix):
                continue
            await self._emit_namespace_lifecycle(
                ns, "completed", graph_name=info.graph_name
            )
            return

    async def _complete_stale_namespaces(self, current: Namespace) -> None:
        """Emit ``lifecycle: completed`` for namespaces execution has left.

        An open namespace ``M`` is still live while the current event's
        namespace equals ``M`` or extends it (``M`` is a prefix of
        ``current``). Anything else means execution moved to a disjoint
        branch or surfaced back up to an ancestor — ``M`` is done and we
        owe the subscribing client a terminal frame for it.

        Root (``[]``) is excluded; its terminal lifecycle is owned by
        :meth:`_emit_terminal_lifecycle` which knows the final run
        status (``completed`` / ``failed`` / ``interrupted``).

        Deepest namespaces are closed first so nested subgraphs emit
        ``completed`` before their parents — matches the JS reference
        output and the logical nesting the UI expects.
        """
        stale: list[NamespaceInfo] = []
        for info in self._namespaces.values():
            if info.status != "started" or not info.namespace:
                continue
            if is_prefix_match(current, info.namespace):
                continue
            stale.append(info)
        stale.sort(key=lambda item: len(item.namespace), reverse=True)
        for info in stale:
            # Re-check status — a deeper sibling's ``_emit_namespace_lifecycle``
            # call could theoretically mutate this one's state through the
            # dedup guard (it doesn't today, but the defensive check keeps
            # this loop correct under future refactors).
            current_info = self._namespaces.get(to_namespace_key(info.namespace))
            if current_info is None or current_info.status != "started":
                continue
            await self._emit_namespace_lifecycle(
                info.namespace, "completed", graph_name=info.graph_name
            )

    def _set_namespace_info(
        self,
        namespace: Namespace,
        status: str,
        *,
        graph_name: str | None = None,
    ) -> None:
        key = to_namespace_key(namespace)
        existing = self._namespaces.get(key)
        self._namespaces[key] = NamespaceInfo(
            namespace=namespace,
            status=status,
            graph_name=(
                graph_name
                or (existing.graph_name if existing else None)
                or (
                    self._root_graph_name
                    if not namespace
                    else guess_graph_name(namespace)
                )
            ),
        )

    async def _emit_namespace_lifecycle(
        self,
        namespace: Namespace,
        status: str,
        *,
        graph_name: str | None = None,
        error: str | None = None,
        cause: dict[str, Any] | None = None,
    ) -> None:
        key = to_namespace_key(namespace)
        if namespace and key not in self._namespaces:
            # Terminal arrived for a namespace whose upstream
            # ``lifecycle.started`` we never saw. Register it
            # internally so terminal-cascade bookkeeping stays
            # consistent; we deliberately do not synthesize a
            # stand-in wire ``started`` (clients would see a
            # ``completed`` without a matching ``started``, which is
            # the correct shape for a real upstream bug — better to
            # surface it than paper over it).
            self._register_namespaces(namespace)

        current = self._namespaces.get(key)
        resolved_graph_name = (
            graph_name
            or (current.graph_name if current else None)
            or (self._root_graph_name if not namespace else guess_graph_name(namespace))
        )

        if (
            current is not None
            and current.status == status
            and current.graph_name == resolved_graph_name
            and error is None
            and cause is None
        ):
            return

        self._set_namespace_info(namespace, status, graph_name=resolved_graph_name)
        lifecycle_data: dict[str, Any] = {
            "event": status,
            "graph_name": resolved_graph_name,
        }
        if error is not None:
            lifecycle_data["error"] = error
        if cause is not None:
            lifecycle_data["cause"] = cause
        await self._push_event(
            self._create_event(
                "lifecycle",
                namespace,
                lifecycle_data,
                event_id=_synth_event_id(self._run_id, "lc", key, status),
            )
        )

    # ------------------------------------------------------------------
    # Input / interrupt handling
    # ------------------------------------------------------------------

    async def _emit_input_requested_events(
        self, namespace: Namespace, requests: list[dict[str, Any]]
    ) -> None:
        emitted_any = False
        ns_key = to_namespace_key(namespace)
        for request in requests:
            interrupt_id = request.get("interrupt_id", "")
            if not interrupt_id or interrupt_id in self._pending_interrupts:
                continue
            self._pending_interrupts[interrupt_id] = list(namespace)
            await self._push_event(
                self._create_event(
                    "input",
                    namespace,
                    request,
                    event_id=_synth_event_id(
                        self._run_id, "input", ns_key, interrupt_id
                    ),
                )
            )
            emitted_any = True

        # An interrupt is a terminal lifecycle transition for the root
        # namespace: the run has stopped and is waiting for ``input.respond``.
        # Emit the lifecycle update eagerly so SDK clients can set
        # ``thread.interrupted`` without waiting for ``_emit_terminal_lifecycle``
        # at source-close (which on the in-memory runtime can race with
        # the run's success status write and otherwise arrive never).
        if emitted_any:
            await self._emit_namespace_lifecycle(
                [], "interrupted", graph_name=self._root_graph_name
            )
            # Latch so the eventual ``_emit_terminal_lifecycle`` doesn't
            # re-emit the same event — dedupe on namespace status would
            # catch it anyway, but this also saves a DB round-trip.
            self._terminal_lifecycle_emitted = True

    def lookup_pending_interrupt(self, interrupt_id: str) -> Namespace | None:
        """Return the namespace that emitted *interrupt_id*, if pending.

        Used by ``input.respond`` to validate the client is responding to
        an interrupt the server actually surfaced, and to reject
        namespace mismatches with ``no_such_interrupt``.
        """
        return self._pending_interrupts.get(interrupt_id)

    def clear_pending_interrupt(self, interrupt_id: str) -> None:
        """Remove *interrupt_id* from the pending set after a successful resume."""
        self._pending_interrupts.pop(interrupt_id, None)

    def _has_state_payload(self, value: Any) -> bool:
        return not _is_record(value) or bool(value)

    # ------------------------------------------------------------------
    # Subscription commands
    # ------------------------------------------------------------------

    async def _handle_subscribe_for_response(
        self, command: dict[str, Any], meta: dict[str, Any] | None
    ) -> dict[str, Any]:
        params = command.get("params", {}) if _is_record(command.get("params")) else {}
        raw_channels = params.get("channels")
        if not isinstance(raw_channels, list) or not raw_channels:
            return self._error(
                command["id"],
                "invalid_argument",
                "subscription.subscribe requires a non-empty channels array.",
                meta,
            )

        channels = [
            c for c in raw_channels if isinstance(c, str) and _is_supported_channel(c)
        ]
        if len(channels) != len(raw_channels):
            return self._error(
                command["id"],
                "invalid_argument",
                "subscription.subscribe received an unsupported channel.",
                meta,
            )

        namespaces = None
        raw_ns = params.get("namespaces")
        if isinstance(raw_ns, list) and all(
            isinstance(ns, list) and all(isinstance(s, str) for s in ns)
            for ns in raw_ns
        ):
            namespaces = raw_ns

        depth = None
        raw_depth = params.get("depth")
        # Reject bool explicitly — ``isinstance(False, int)`` is ``True``
        # and would silently coerce to ``depth=0`` (root-only).
        if (
            isinstance(raw_depth, int)
            and not isinstance(raw_depth, bool)
            and raw_depth >= 0
        ):
            depth = raw_depth

        sub = Subscription(
            id=str(uuid4()),
            channels=set(channels),
            namespaces=namespaces,
            depth=depth,
            active=False,
        )
        replayed = await self.install_subscription_with_replay(sub)
        return self._success(
            command["id"],
            {"subscription_id": sub.id, "replayed_events": replayed},
            meta,
        )

    async def _handle_reconnect_for_response(
        self,
        command: dict[str, Any],
        meta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Handle ``subscription.reconnect`` — subscribe + replay from ``since``.

        Takes the same filter shape as ``subscription.subscribe`` plus a
        mandatory non-negative integer ``since``. Surfaces
        :class:`ResumeGap` as a ``resume_gap`` error response carrying
        ``since`` and ``min_available_seq`` so clients can decide
        whether to refetch state or restart from the available range.
        Accepts an optional client-supplied ``subscription_id`` so the
        reconnecting client can keep its pre-disconnect identifier.
        """
        params = command.get("params", {}) if _is_record(command.get("params")) else {}
        raw_channels = params.get("channels")
        if not isinstance(raw_channels, list) or not raw_channels:
            return self._error(
                command["id"],
                "invalid_argument",
                "subscription.reconnect requires a non-empty channels array.",
                meta,
            )
        channels = [
            c for c in raw_channels if isinstance(c, str) and _is_supported_channel(c)
        ]
        if len(channels) != len(raw_channels):
            return self._error(
                command["id"],
                "invalid_argument",
                "subscription.reconnect received an unsupported channel.",
                meta,
            )

        raw_since = params.get("since")
        # Reject ``bool`` explicitly — ``isinstance(True, int)`` is
        # ``True`` so a JSON ``"since": true`` would otherwise pass as
        # ``since=1`` and silently skip the first buffered event.
        if (
            not isinstance(raw_since, int)
            or isinstance(raw_since, bool)
            or raw_since < 0
        ):
            return self._error(
                command["id"],
                "invalid_argument",
                "subscription.reconnect requires a non-negative integer since.",
                meta,
            )

        namespaces = None
        raw_ns = params.get("namespaces")
        if isinstance(raw_ns, list) and all(
            isinstance(ns, list) and all(isinstance(s, str) for s in ns)
            for ns in raw_ns
        ):
            namespaces = raw_ns

        depth = None
        raw_depth = params.get("depth")
        # Reject bool explicitly — ``isinstance(False, int)`` is ``True``
        # and would silently coerce to ``depth=0`` (root-only).
        if (
            isinstance(raw_depth, int)
            and not isinstance(raw_depth, bool)
            and raw_depth >= 0
        ):
            depth = raw_depth

        raw_sub_id = params.get("subscription_id")
        sub_id = (
            raw_sub_id if isinstance(raw_sub_id, str) and raw_sub_id else str(uuid4())
        )

        sub = Subscription(
            id=sub_id,
            channels=set(channels),
            namespaces=namespaces,
            depth=depth,
            active=False,
        )

        try:
            replayed = await self.install_subscription_with_replay(sub, since=raw_since)
        except ResumeGap as gap:
            resp: dict[str, Any] = {
                "type": "error",
                "id": command["id"],
                "error": "resume_gap",
                "message": str(gap),
                "since": gap.since,
                "min_available_seq": gap.min_available_seq,
            }
            if meta is not None:
                resp["meta"] = meta
            return resp

        return self._success(
            command["id"],
            {"subscription_id": sub.id, "replayed_events": replayed},
            meta,
        )

    async def _handle_unsubscribe_for_response(
        self, command: dict[str, Any], meta: dict[str, Any] | None
    ) -> dict[str, Any]:
        params = command.get("params", {}) if _is_record(command.get("params")) else {}
        sub_id = params.get("subscription_id")
        if not isinstance(sub_id, str):
            return self._error(
                command["id"],
                "invalid_argument",
                "subscription.unsubscribe requires a subscription_id.",
                meta,
            )
        if sub_id not in self.subscriptions:
            return self._error(
                command["id"],
                "no_such_subscription",
                f"Unknown subscription: {sub_id}",
                meta,
            )
        del self.subscriptions[sub_id]
        return self._success(command["id"], {}, meta)

    # ------------------------------------------------------------------
    # Agent tree
    # ------------------------------------------------------------------

    def _build_tree(self, namespace: Namespace) -> dict[str, Any]:
        key = to_namespace_key(namespace)
        current = self._namespaces.get(key) or NamespaceInfo(
            namespace=namespace,
            status="started",
            graph_name=(
                self._root_graph_name if not namespace else guess_graph_name(namespace)
            ),
        )
        children = sorted(
            [
                info
                for info in self._namespaces.values()
                if len(info.namespace) == len(namespace) + 1
                and is_prefix_match(info.namespace, namespace)
            ],
            key=lambda info: info.namespace,
        )
        result: dict[str, Any] = {
            "namespace": current.namespace,
            "status": current.status,
            "graph_name": current.graph_name,
        }
        if children:
            result["children"] = [self._build_tree(c.namespace) for c in children]
        return result

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _success(
        self,
        cmd_id: int,
        result: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp: dict[str, Any] = {"type": "success", "id": cmd_id, "result": result}
        if meta is not None:
            resp["meta"] = meta
        return resp

    def _error(
        self,
        cmd_id: int | None,
        error: str,
        message: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp: dict[str, Any] = {
            "type": "error",
            "id": cmd_id,
            "error": error,
            "message": message,
        }
        if meta is not None:
            resp["meta"] = meta
        return resp
