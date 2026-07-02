import functools
import inspect
import typing

import jsonschema_rs
import orjson
import structlog
from starlette._exception_handler import wrap_app_handling_exceptions
from starlette._utils import is_async_callable
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute, compile_path, get_name
from starlette.types import ASGIApp, Receive, Scope, Send

from langgraph_api import config
from langgraph_api.serde import json_dumpb
from langgraph_api.utils import get_auth_ctx, with_user
from langgraph_api.validation import (
    sanitize_reserved_keys,
    should_strip_reserved_metadata,
)

logger = structlog.getLogger(__name__)
SchemaType = (
    jsonschema_rs.Draft4Validator
    | jsonschema_rs.Draft6Validator
    | jsonschema_rs.Draft7Validator
    | jsonschema_rs.Draft201909Validator
    | jsonschema_rs.Draft202012Validator
    | None
)


def api_request_response(
    func: typing.Callable[[Request], typing.Awaitable[ASGIApp] | ASGIApp],
) -> ASGIApp:
    """
    Takes a function or coroutine `func(request) -> response`,
    and returns an ASGI application.
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        request = ApiRequest(scope, receive, send)

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if is_async_callable(func):
                response: ASGIApp = await func(request)
            else:
                response = await run_in_threadpool(
                    typing.cast("typing.Callable[[Request], ASGIApp]", func), request
                )
            await response(scope, receive, send)

        await wrap_app_handling_exceptions(app, request)(scope, receive, send)

    return app


class ApiResponse(JSONResponse):
    def render(self, content: typing.Any) -> bytes:
        return json_dumpb(content)


def _json_loads(content: bytearray, schema: SchemaType) -> typing.Any:
    """Parse JSON and validate schema. Used by threadpool for large payloads."""
    json_data = orjson.loads(content)
    sanitize_reserved_keys(
        json_data,
        strip_metadata=should_strip_reserved_metadata(schema),
    )
    if schema is not None:
        schema.validate(json_data)
    return json_data


class ApiRequest(Request):
    async def body(self) -> bytearray:
        if not hasattr(self, "_body"):
            chunks = bytearray()
            limit = config.HTTP_MAX_REQUEST_BODY_BYTES
            async for chunk in self.stream():
                chunks.extend(chunk)
                if len(chunks) > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Request body too large (limit: {limit} bytes)",
                    )
            self._body = chunks
        return self._body

    async def json(self, schema: SchemaType = None) -> typing.Any:
        if not hasattr(self, "_json"):
            body = await self.body()

            # Hybrid approach for optimal performance:
            # - Small payloads: parse directly (fast, no queueing/thread pool limitations)
            # - Large payloads: use dedicated thread pool (safer, doesn't block event loop)
            try:
                self._json = (
                    await run_in_threadpool(_json_loads, body, schema)
                    if len(body) > config.JSON_THREAD_POOL_MINIMUM_SIZE_BYTES
                    else _json_loads(body, schema)
                )
            except orjson.JSONDecodeError as e:
                raise HTTPException(
                    status_code=422, detail="Invalid JSON in request body"
                ) from e
        return self._json


# ApiRoute uses our custom ApiRequest class to handle requests.
class ApiRoute(Route):
    def __init__(
        self,
        path: str,
        endpoint: typing.Callable[..., typing.Any],
        *,
        methods: list[str] | None = None,
        name: str | None = None,
        include_in_schema: bool = True,
        middleware: typing.Sequence[Middleware] | None = None,
    ) -> None:
        if not path.startswith("/"):
            raise ValueError("Routed paths must start with '/'")
        self.path = path
        self.endpoint = endpoint
        self.name = get_name(endpoint) if name is None else name
        self.include_in_schema = include_in_schema

        endpoint_handler = endpoint
        while isinstance(endpoint_handler, functools.partial):
            endpoint_handler = endpoint_handler.func
        if inspect.isfunction(endpoint_handler) or inspect.ismethod(endpoint_handler):
            # Endpoint is function or method. Treat it as `func(request) -> response`.
            self.app = api_request_response(endpoint)
            if methods is None:
                methods = ["GET"]
        else:
            # Endpoint is a class. Treat it as ASGI.
            self.app = endpoint

        if middleware is not None:
            for cls, args, kwargs in reversed(middleware):
                self.app = cls(app=self.app, *args, **kwargs)  # noqa: B026

        if methods is None:
            self.methods = None
        else:
            self.methods = {method.upper() for method in methods}
            if "GET" in self.methods:
                self.methods.add("HEAD")

        self.path_regex, self.path_format, self.param_convertors = compile_path(path)

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        # https://asgi.readthedocs.io/en/latest/specs/www.html#http-connection-scope
        from langgraph_api.logging import set_logging_context  # noqa: PLC0415

        scope["route"] = self.path
        set_logging_context({"path": self.path, "method": scope.get("method")})
        route_pattern = f"{scope.get('root_path', '')}{self.path}"
        _name_otel_span(scope, route_pattern)
        ctx = get_auth_ctx()
        if ctx:
            user, auth = ctx.user, ctx.permissions
        else:
            user, auth = scope.get("user"), scope.get("auth")
        async with with_user(user, auth):
            return await super().handle(scope, receive, send)


class ApiWebSocketRoute(WebSocketRoute):
    """WebSocket route that propagates the auth context var.

    Mirrors ``ApiRoute.handle`` for WebSocket handlers: starlette's
    ``AuthenticationMiddleware`` populates ``scope["user"]`` and
    ``scope["auth"]`` on the upgrade, but code that reads auth via
    the ``AuthContext`` ContextVar (``get_auth_ctx``) still sees
    ``None`` because the default ``WebSocketRoute.handle`` does not
    enter the ``with_user`` context manager.

    Without this wrapper, ops like ``Runs.search`` / ``Threads.State.get``
    invoked from the WebSocket handler path read ``None`` for the user
    and silently return empty results or fail authorization. This is the
    WebSocket analog of ``ApiRoute`` and must be used for any WS route
    that reaches the ops layer.
    """

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        from langgraph_api.logging import set_logging_context  # noqa: PLC0415

        scope["route"] = self.path
        set_logging_context({"path": self.path, "method": "WEBSOCKET"})
        route_pattern = f"{scope.get('root_path', '')}{self.path}"
        _name_otel_span(scope, route_pattern)
        ctx = get_auth_ctx()
        if ctx:
            user, auth = ctx.user, ctx.permissions
        else:
            user, auth = scope.get("user"), scope.get("auth")
        async with with_user(user, auth):
            return await super().handle(scope, receive, send)


def _name_otel_span(scope: Scope, route_pattern: str):
    """Best-effort rename of the active OTEL server span to include the route.

    - No-ops if OTEL is disabled or OTEL libs are unavailable.
    - Sets span name to "METHOD /templated/path" and attaches http.route.
    - Never raises; safe for hot path usage.
    """
    if not config.OTEL_ENABLED:
        return
    try:
        from opentelemetry.trace import get_current_span  # noqa: PLC0415

        span = get_current_span()
        if span.is_recording():
            method = scope.get("method", "") or ""
            try:
                span.update_name(f"{method} {route_pattern}")
            except Exception:
                logger.error("Failed to update OTEL span name", exc_info=True)
                pass
            try:
                span.set_attribute("http.route", route_pattern)
            except Exception:
                logger.error("Failed to update OTEL span attributes", exc_info=True)
    except Exception:
        logger.error("Failed to update OTEL span", exc_info=True)
