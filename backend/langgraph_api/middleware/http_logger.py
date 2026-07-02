import asyncio
import logging

import structlog
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope, Send

from langgraph_api.config import MOUNT_PREFIX
from langgraph_api.http_metrics import HTTP_METRICS_COLLECTOR
from langgraph_api.utils.headers import should_include_header_in_logs

asgi = structlog.stdlib.get_logger("asgi")


PATHS_IGNORE = {"/ok", "/metrics"}


def _get_level(status: int | None) -> int:
    if status is None or status < 400:
        return logging.INFO
    if status < 500:
        return logging.WARNING
    return logging.ERROR


# Both libraries are optional dependencies. Import eagerly at module load
# so any blocking work in their __init__ (e.g. ddtrace's os.getcwd()) runs
# before the event loop starts, not lazily on the request hot path.
try:
    from ddtrace import tracer as _dd_tracer  # type: ignore[unresolved-import]
except ImportError:
    _dd_tracer = None

try:
    from opentelemetry import trace as _otel_trace  # type: ignore[unresolved-import]
except ImportError:
    _otel_trace = None


def _capture_trace_ids() -> dict[str, str]:
    """Snapshot the active trace/span IDs.

    The access log fires from the outermost middleware's ``finally`` block, by
    which point ddtrace's and OTel's request spans have already closed. Capture
    IDs while the span is still active (e.g. on ``http.response.start``) so the
    log line can be correlated to its trace.

    Returns ``{}`` on any tracing-library error — this runs from response
    callbacks and exception handlers, so it must never raise (a failure here
    would mask the real application exception or turn a 200 into a 500).
    """
    ids: dict[str, str] = {}
    try:
        dd_span = _dd_tracer.current_span() if _dd_tracer is not None else None
        if dd_span is not None:
            ids["dd.trace_id"] = str(dd_span.trace_id)
            ids["dd.span_id"] = str(dd_span.span_id)
        if _otel_trace is not None:
            ctx = _otel_trace.get_current_span().get_span_context()
            if ctx.is_valid:
                ids["otel.trace_id"] = format(ctx.trace_id, "032x")
                ids["otel.span_id"] = format(ctx.span_id, "016x")
    except Exception:
        return {}
    return ids


class AccessLoggerMiddleware:
    def __init__(
        self,
        app,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        self.app = app
        self.logger = logger
        if hasattr(logger, "isEnabledFor"):
            self.debug_enabled = self.logger.isEnabledFor(logging.DEBUG)
        elif hasattr(logger, "is_enabled_for"):
            self.debug_enabled = self.logger.is_enabled_for(logging.DEBUG)
        else:
            self.debug_enabled = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("path", "").replace(MOUNT_PREFIX or "", "") in PATHS_IGNORE
        ):
            return await self.app(scope, receive, send)  # pragma: no cover

        loop = asyncio.get_event_loop()
        info = {
            "response": {},
            "response_bytes": 0,
            "first_byte_time": None,
            "trace_ids": {},
        }

        if self.debug_enabled:

            async def inner_receive() -> Message:
                message = await receive()
                asgi.debug(f"ASGI receive {message['type']}", **message)
                return message

            async def inner_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    info["response"] = message
                    info["trace_ids"] = _capture_trace_ids()
                elif message["type"] == "http.response.body":
                    if info["first_byte_time"] is None:
                        info["first_byte_time"] = loop.time()
                    info["response_bytes"] += len(message.get("body", b""))
                await send(message)
                asgi.debug(f"ASGI send {message['type']}", **message)

        else:
            inner_receive = receive

            async def inner_send(message) -> None:
                if message["type"] == "http.response.start":
                    info["response"] = message
                    info["trace_ids"] = _capture_trace_ids()
                elif message["type"] == "http.response.body":
                    if info["first_byte_time"] is None:
                        info["first_byte_time"] = loop.time()
                    info["response_bytes"] += len(message.get("body", b""))
                await send(message)

        try:
            info["start_time"] = loop.time()
            await self.app(scope, inner_receive, inner_send)
        except ClientDisconnect as exc:
            info["response"]["status"] = 499
            if not info["trace_ids"]:
                info["trace_ids"] = _capture_trace_ids()
            raise exc
        except Exception as exc:
            info["response"]["status"] = 500
            if not info["trace_ids"]:
                info["trace_ids"] = _capture_trace_ids()
            raise exc
        finally:
            info["end_time"] = loop.time()
            latency = int((info["end_time"] - info["start_time"]) * 1_000)

            status = info["response"].get("status")
            method = scope.get("method")
            path = scope.get("path")
            route = scope.get("route")

            if method and route and status:
                HTTP_METRICS_COLLECTOR.record_request(method, route, status, latency)
            qs = scope.get("query_string")
            first_byte_time = info["first_byte_time"]
            ttfb_ms = (
                round((first_byte_time - info["start_time"]) * 1000, 2)
                if first_byte_time is not None
                else None
            )
            self.logger.log(
                _get_level(status),
                f"{method} {path} {status} {latency}ms",
                method=method,
                path=path,
                status=status,
                latency_ms=latency,
                ttfb_ms=ttfb_ms,
                run_id=scope.get("run_id"),
                response_size_bytes=info["response_bytes"],
                route=str(route),
                path_params=scope.get("path_params"),
                query_string=qs.decode() if qs else "",
                error_detail=scope.get("error_detail"),
                proto=scope.get("http_version"),
                req_header=_headers_to_dict(scope.get("headers")),
                res_header=_headers_to_dict(info["response"].get("headers")),
                **info["trace_ids"],
            )


IGNORE_HEADERS = {
    b"authorization",
    b"cookie",
    b"set-cookie",
    b"x-api-key",
}


def _headers_to_dict(headers: list[tuple[bytes, bytes]] | None) -> dict[str, str]:
    if headers is None:
        return {}

    result = {}
    for k, v in headers:
        if k in IGNORE_HEADERS:
            continue
        key = k.decode()
        if should_include_header_in_logs(key):
            result[key] = v.decode()

    return result
