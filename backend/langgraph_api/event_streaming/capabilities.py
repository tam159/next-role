"""Runtime capability probe for Protocol v2 streaming.

Deployments bundle the user's ``langgraph`` install alongside
``langgraph-api`` in a single container. ``langgraph-api`` keeps wide
version pins (legacy streaming still works on older releases), so a
user could land on the ``/v2`` endpoint with a ``langgraph`` too old to
honour the new streaming protocol. Rather than let that surface as a
cryptic ``ImportError`` mid-stream, a silent message collapse, or a
``ValueError: Invalid stream_mode: tools`` from the graph, we probe
once at call-time and reject the offending command up front with a
clear, actionable error envelope.

The probe targets the ``langgraph`` and ``langchain-core`` symbols that
``stream.py`` relies on to serve a Python-graph Protocol v2 run. Checking
them up front lets us fail ``run.start`` before persisting a run row when
the runtime cannot emit native protocol-shaped message events.

1. ``langgraph.pregel._messages.StreamMessagesHandlerV2`` — the handler
   that honours ``CONFIG_KEY_STREAM_MESSAGES_V2`` and emits content-block
   message events. Without it, the ``"messages"`` stream mode yields
   legacy ``(BaseMessage, metadata)`` tuples the v2 session can't shape.
2. ``langgraph.pregel._tools.StreamToolCallHandler`` — wiring for the
   ``"tools"`` stream mode that ``stream.py`` adds on Protocol v2 runs.
   Missing ⇒ ``graph.astream(stream_mode=["tools", ...])`` raises.
3. ``langgraph.stream._mux.StreamMux`` — the mux framework imported
   lazily by ``stream.py``'s ``use_stream_events_v3`` branch (graphs that
   register a ``stream_transformers`` factory). Missing ⇒ ``ImportError``
   surfaces as an ``error`` SSE event.
4. ``langgraph.stream.transformers.LifecycleTransformer`` — the
   root-scope transformer that emits the protocol's lifecycle events
   (``run.start`` / ``run.end`` / subagent transitions). Missing ⇒ the
   v2 session would silently drop lifecycle frames.
5. ``langchain_core.language_models.chat_model_stream.AsyncChatModelStream`` /
   ``ChatModelStream`` and ``langchain_core.language_models._compat_bridge`` —
   the Core content-block stream primitives and finalized-message bridge.
   Missing ⇒ the protocol session would have to reconstruct legacy
   ``messages/partial`` events, which v2 no longer supports.

Remote (JS) pregels bypass every code path that needs these symbols
(``stream.py`` drops ``tools`` / ``lifecycle`` and skips the
content-block dispatch for remote graphs), so a ``BaseRemotePregel``-only
deployment could in theory run on an older Python ``langgraph``. The
probe is still applied — the DX of a clear "upgrade your dependencies"
error comfortably beats the DX of a mystery crash whose trigger depends
on which graph the user happens to invoke first.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ProtocolV2Capabilities:
    """Outcome of the runtime probe for Protocol v2 streaming support."""

    ok: bool
    missing: tuple[str, ...]
    # Replaces the default "missing langgraph symbols" wording when the
    # server has disabled v2 deliberately (e.g. ``FF_V2_EVENT_STREAMING=false``).
    # Library-version mismatches and operator-disabled v2 produce the same
    # ``unsupported`` envelope, but with very different ``"upgrade your
    # deps"`` vs ``"flip the flag"`` remediation paths — keep them
    # distinguishable in the error string.
    reason_override: str | None = None

    @property
    def error_message(self) -> str:
        """Human-readable explanation suitable for a command error envelope."""
        if self.ok:
            return ""
        if self.reason_override is not None:
            return self.reason_override
        return (
            "Protocol v2 streaming is not supported by the installed "
            "langgraph/langchain-core version. Missing symbol(s): "
            + ", ".join(self.missing)
            + ". Upgrade the runtime by installing langgraph and "
            "langchain-core releases that ship the new streaming framework "
            "(StreamMux, StreamMessagesHandlerV2, StreamToolCallHandler, "
            "LifecycleTransformer, and ChatModelStream)."
        )

    @classmethod
    def disabled_by_flag(cls) -> ProtocolV2Capabilities:
        """Build a capabilities result for ``FF_V2_EVENT_STREAMING=false``."""
        return cls(
            ok=False,
            missing=(),
            reason_override=(
                "Protocol v2 event streaming is disabled on this server "
                "(FF_V2_EVENT_STREAMING=false). Set FF_V2_EVENT_STREAMING=true to enable."
            ),
        )


# Submodule paths are intentional — ``StreamMux`` et al. are not
# re-exported from ``langgraph.stream`` / ``langgraph.pregel`` top-level,
# so a naive ``hasattr(langgraph.stream, "StreamMux")`` check would
# falsely report them as missing.
_REQUIRED_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("langgraph.pregel._messages", "StreamMessagesHandlerV2"),
    ("langgraph.pregel._tools", "StreamToolCallHandler"),
    ("langgraph.stream._mux", "StreamMux"),
    ("langgraph.stream.transformers", "LifecycleTransformer"),
    ("langchain_core.language_models.chat_model_stream", "ChatModelStream"),
    ("langchain_core.language_models.chat_model_stream", "AsyncChatModelStream"),
    ("langchain_core.language_models._compat_bridge", "message_to_events"),
)


@lru_cache(maxsize=1)
def probe_event_streaming_v2_capabilities() -> ProtocolV2Capabilities:
    """Check the installed runtime for Protocol v2 streaming support.

    Result is memoised for the process lifetime — the underlying
    packages cannot change without a restart, and the probe is on the
    hot path of every ``run.start`` / ``input.respond`` command.
    """
    missing: list[str] = []
    for module_path, attr in _REQUIRED_SYMBOLS:
        try:
            module = __import__(module_path, fromlist=[attr])
        except ImportError:
            missing.append(f"{module_path}.{attr}")
            continue
        if not hasattr(module, attr):
            missing.append(f"{module_path}.{attr}")
    return ProtocolV2Capabilities(ok=not missing, missing=tuple(missing))


class ProtocolV2UnsupportedError(RuntimeError):
    """Raised when the installed runtime can't serve a Protocol v2 run.

    Command handlers catch this specifically so they can return a
    ``type: error, error: unsupported`` envelope with a clear message,
    instead of the generic ``unknown_error`` fallback used for
    unexpected failures.
    """

    def __init__(self, capabilities: ProtocolV2Capabilities) -> None:
        super().__init__(capabilities.error_message)
        self.capabilities = capabilities
