"""OTEL trace context propagation utilities.

Provides helpers for extracting, storing, and restoring W3C Trace Context
across the API-to-worker boundary in distributed LangGraph deployments.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any

import structlog

from langgraph_api import __version__, config

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from opentelemetry.trace import Tracer

logger = structlog.stdlib.get_logger(__name__)

# Constants for storing trace context in configurable
OTEL_TRACEPARENT_KEY = "__otel_traceparent__"
OTEL_TRACESTATE_KEY = "__otel_tracestate__"
OTEL_TRACER_NAME = "langsmith_agent_server"
OTEL_RUN_ID_ATTR_NAME = "langsmith.run_id"
OTEL_THREAD_ID_ATTR_NAME = "langsmith.thread_id"

# Cached instances (initialized lazily, once)
_propagator: Any = None
_tracer: Any = None
_otel_available: bool | None = None


def _check_otel_available() -> bool:
    """Check if OpenTelemetry is available. Cached after first call."""
    global _otel_available
    if _otel_available is None:
        try:
            from opentelemetry import trace  # noqa: F401, PLC0415
            from opentelemetry.trace.propagation.tracecontext import (  # noqa: PLC0415
                TraceContextTextMapPropagator,  # noqa: F401
            )

            _otel_available = True
        except ImportError:
            _otel_available = False
    return _otel_available


def _get_propagator() -> Any:
    """Get cached W3C TraceContext propagator."""
    global _propagator
    if _propagator is None:
        from opentelemetry.trace.propagation.tracecontext import (  # noqa: PLC0415
            TraceContextTextMapPropagator,
        )

        _propagator = TraceContextTextMapPropagator()
    return _propagator


def _get_tracer() -> Tracer:
    """Get cached tracer for worker spans."""
    global _tracer
    if _tracer is None:
        from opentelemetry import trace  # noqa: PLC0415

        _tracer = trace.get_tracer(
            OTEL_TRACER_NAME, instrumenting_library_version=__version__
        )
    return _tracer


def extract_otel_headers_to_configurable(
    headers: Mapping[str, str],
    configurable: dict[str, Any],
) -> None:
    """Extract traceparent/tracestate from HTTP headers into configurable dict.

    Only extracts if OTEL is enabled. No-op otherwise.

    Args:
        headers: HTTP headers from the incoming request
        configurable: The configurable dict to store trace context in
    """
    if not config.OTEL_ENABLED:
        return

    if traceparent := headers.get("traceparent"):
        configurable[OTEL_TRACEPARENT_KEY] = traceparent
    if tracestate := headers.get("tracestate"):
        configurable[OTEL_TRACESTATE_KEY] = tracestate


def inject_current_trace_context(configurable: dict[str, Any]) -> None:
    """Inject current OTEL trace context into configurable for worker propagation.

    This captures the active span context (e.g., from Starlette auto-instrumentation)
    and stores it in the configurable dict so workers can restore it and create
    child spans under the API request span.

    Args:
        configurable: The configurable dict to store trace context in
    """
    if not config.OTEL_ENABLED or not _check_otel_available():
        return

    try:
        from opentelemetry import trace  # noqa: PLC0415

        span = trace.get_current_span()
        if not span.is_recording():
            return

        carrier: dict[str, str] = {}
        _get_propagator().inject(carrier)

        if traceparent := carrier.get("traceparent"):
            configurable[OTEL_TRACEPARENT_KEY] = traceparent
        if tracestate := carrier.get("tracestate"):
            configurable[OTEL_TRACESTATE_KEY] = tracestate
    except Exception:
        # Never fail - tracing issues shouldn't break functionality
        pass


@contextmanager
def restore_otel_trace_context(
    configurable: dict[str, Any],
    run_id: str | None = None,
    thread_id: str | None = None,
) -> Generator[None, None, None]:
    """Restore OTEL trace context and create child span for worker execution.

    Creates a child span under the original API request span, ensuring
    distributed traces are connected across the API-to-worker boundary.

    Yields:
        None - execution continues within the restored trace context

    Note:
        - No-ops if OTEL is disabled or unavailable
        - Tracing setup failures won't break run execution
    """
    if not config.OTEL_ENABLED or not _check_otel_available():
        yield
        return

    traceparent = configurable.get(OTEL_TRACEPARENT_KEY)
    if not traceparent:
        yield
        return

    stack: ExitStack | None = None
    try:
        from opentelemetry import trace  # noqa: PLC0415

        # Build carrier dict for W3C propagator
        carrier: dict[str, str] = {"traceparent": traceparent}
        if tracestate := configurable.get(OTEL_TRACESTATE_KEY):
            carrier["tracestate"] = tracestate

        # Extract context from carrier
        ctx = _get_propagator().extract(carrier=carrier)

        stack = ExitStack()
        span = stack.enter_context(
            _get_tracer().start_as_current_span(
                "worker.stream_run",
                context=ctx,
                kind=trace.SpanKind.CONSUMER,
            )
        )
        if run_id:
            span.set_attribute(OTEL_RUN_ID_ATTR_NAME, run_id)
        if thread_id:
            span.set_attribute(OTEL_THREAD_ID_ATTR_NAME, thread_id)
    except Exception:
        logger.debug("Failed to initialize OTEL worker span context", exc_info=True)
        if stack is not None:
            try:
                stack.close()
            except Exception:
                logger.warning("Failed to close OTEL worker span", exc_info=True)
        yield
        return

    try:
        yield
    except BaseException:
        # Preserve exception metadata when unwinding managed contexts so OTEL
        # can record body errors on the span.
        exc_type, exc, tb = sys.exc_info()
        try:
            if stack.__exit__(exc_type, exc, tb):
                return
        except Exception:
            logger.warning("Failed to close OTEL worker span", exc_info=True)
        raise
    else:
        try:
            stack.close()
        except Exception:
            logger.warning("Failed to close OTEL worker span", exc_info=True)


def inject_otel_headers() -> dict[str, str]:
    """Inject current trace context into headers for outgoing HTTP requests.

    Used to propagate trace context to webhooks.

    Returns:
        Dict with traceparent/tracestate headers if in active trace, else empty.
    """
    if not config.OTEL_ENABLED or not _check_otel_available():
        return {}

    try:
        from opentelemetry import trace  # noqa: PLC0415

        span = trace.get_current_span()
        if not span.is_recording():
            return {}

        carrier: dict[str, str] = {}
        _get_propagator().inject(carrier)
        return carrier
    except Exception:
        return {}
