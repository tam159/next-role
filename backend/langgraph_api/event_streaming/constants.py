from __future__ import annotations

EVENT_STREAMING_V2_CONFIG_KEY = "__event_streaming_v2"

# Ring-buffer size for ``EventStreamingSession``. Events beyond this point
# are dropped (oldest-first) from the replay buffer. Internal
# memory-safety cap — not tunable over the wire, but operators can
# override via the ``LSD_PROTOCOL_V2_BUFFER_SIZE`` env var (see
# ``langgraph_api.config``). Evictions are tracked via
# ``COUNTER_STREAMING_DATA_LOSS`` with ``reason=protocol_v2_buffer_overflow``
# and reconnects whose ``since`` cursor falls behind the buffer head
# receive a ``resume_gap`` protocol error rather than silent
# truncation. Bumped from the original 1,000 to 10,000 to give
# reconnecting clients a wider resume window on chattier graphs.
DEFAULT_MAX_BUFFER_SIZE = 10_000

SUPPORTED_CHANNELS: frozenset[str] = frozenset(
    {
        "values",
        "updates",
        "checkpoints",
        "messages",
        "tools",
        "custom",
        "lifecycle",
        "input",
        "tasks",
    }
)

# ``stream_mode`` set on every protocol v2 run. Doubles as the resumable
# set: ``consume()`` only tags events as resumable when their mode is in
# the run's ``stream_mode`` (see ``stream.py``).
DEFAULT_RUN_STREAM_MODES: list[str] = [
    "values",
    "updates",
    "messages",
    "tools",
    "lifecycle",
    "custom",
    "tasks",
    "checkpoints",
]
