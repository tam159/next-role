# MONKEY PATCH: Patch Starlette to fix an error in the library
# WARNING: Keep the import above before other code runs as it
# patches an error in the Starlette library.
import langgraph_api.patch  # noqa: F401,I001
import langgraph_api.timing as timing
import logging
import os
import sys
import typing

if not (
    (disable_truststore := os.getenv("DISABLE_TRUSTSTORE"))
    and disable_truststore.lower() == "true"
):
    import truststore

    truststore.inject_into_ssl()


import jsonschema_rs
import structlog
from langgraph.errors import EmptyInputError, InvalidUpdateError
from langgraph_sdk.client import configure_loopback_transports
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import BaseRoute, Mount
from starlette.types import Receive, Scope, Send

import langgraph_api.config as config
from langgraph_api.api import (
    middleware_for_protected_routes,
    protected_routes,
    shadowable_meta_routes,
    unshadowable_meta_routes,
    user_router,
)
from langgraph_api.api.openapi import set_custom_spec
from langgraph_api.errors import (
    http_exception_handler,
    overloaded_error_handler,
    remote_exception_handler,
    validation_error_handler,
    value_error_handler,
)
from langgraph_api.js.base import is_js_path
from langgraph_api.js.errors import RemoteException
from langgraph_api.middleware.ensure_store import EnsureStoreAccessible
from langgraph_api.middleware.http_logger import AccessLoggerMiddleware
from langgraph_api.middleware.private_network import PrivateNetworkMiddleware
from langgraph_api.middleware.request_id import RequestIdMiddleware
from langgraph_api.utils import SchemaGenerator
from langgraph_runtime.lifespan import lifespan
from langgraph_runtime.retry import OVERLOADED_EXCEPTIONS

logging.captureWarnings(True)
logger = structlog.stdlib.get_logger(__name__)

global_middleware = []

if config.ALLOW_PRIVATE_NETWORK:
    global_middleware.append(Middleware(PrivateNetworkMiddleware))

JS_PROXY_MIDDLEWARE_ENABLED = (
    config.HTTP_CONFIG
    and (app := config.HTTP_CONFIG.get("app"))
    and is_js_path(app.split(":")[0])
)

if JS_PROXY_MIDDLEWARE_ENABLED:
    from langgraph_api.js.remote import JSCustomHTTPProxyMiddleware

    global_middleware.append(Middleware(JSCustomHTTPProxyMiddleware))

global_middleware.extend(
    [
        (
            Middleware(
                CORSMiddleware,
                allow_origins=config.CORS_ALLOW_ORIGINS,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=[
                    "x-pagination-total",
                    "x-pagination-next",
                    "content-location",
                ],
            )
            if config.CORS_CONFIG is None
            else Middleware(
                CORSMiddleware,
                **config.CORS_CONFIG,
            )
        ),
        Middleware(AccessLoggerMiddleware, logger=logger),
        Middleware(RequestIdMiddleware, mount_prefix=config.MOUNT_PREFIX),
    ]
)
exception_handlers = {
    HTTPException: http_exception_handler,
    ValueError: value_error_handler,
    InvalidUpdateError: value_error_handler,
    EmptyInputError: value_error_handler,
    jsonschema_rs.ValidationError: validation_error_handler,
    RemoteException: remote_exception_handler,
} | {exc: overloaded_error_handler for exc in OVERLOADED_EXCEPTIONS}


def update_openapi_spec(app):
    spec = None
    if "fastapi" in sys.modules:
        # It's maybe a fastapi app
        from fastapi import FastAPI  # noqa: PLC0415

        if isinstance(user_router, FastAPI):
            spec = app.openapi()

    if spec is None:
        # How do we add
        schemas = SchemaGenerator(
            {
                "openapi": "3.1.0",
                "info": {"title": "LangSmith Deployment", "version": "0.1.0"},
            }
        )
        spec = schemas.get_schema(routes=app.routes)

    if spec:
        set_custom_spec(spec)


def apply_middleware(
    routes: list[BaseRoute], middleware: list[Middleware]
) -> list[BaseRoute]:
    """Applies middleware to a list of routes.

    Routes are modified in place (only the `app` attribute is modified);
    the modified routes are returned for convenience.
    """
    middleware_routes = []
    for route in routes:
        for cls, args, kwargs in reversed(middleware):
            if hasattr(route, "app"):
                route.app = cls(route.app, *args, **kwargs)
            else:
                raise ValueError(f"Cannot apply middleware: route {route} has no app")
        middleware_routes.append(route)
    return middleware_routes


def validate_router_lifespan_hooks(router: typing.Any) -> None:
    on_startup = getattr(router, "on_startup", None)
    on_shutdown = getattr(router, "on_shutdown", None)
    if on_startup or on_shutdown:
        raise ValueError(
            "Cannot merge lifespans with on_startup or on_shutdown: "
            f"{on_startup} {on_shutdown}"
        )


custom_middleware = (
    user_router.user_middleware if user_router and user_router.user_middleware else []
)
auth_before_custom_middleware = (
    config.HTTP_CONFIG and config.HTTP_CONFIG.get("middleware_order") == "auth_first"
)
enable_auth_on_custom_routes = config.HTTP_CONFIG and config.HTTP_CONFIG.get(
    "enable_custom_route_auth"
)
# Custom middleware to be applied at the route/mount level, not globally (app level).
route_level_custom_middleware = (
    custom_middleware if auth_before_custom_middleware else []
)

protected_mount = Mount(
    "",
    routes=protected_routes,
    middleware=(
        middleware_for_protected_routes + route_level_custom_middleware
        if auth_before_custom_middleware
        else route_level_custom_middleware + middleware_for_protected_routes
    ),
)

if user_router:
    _store_access_middleware = [Middleware(EnsureStoreAccessible)]
    # Merge routes
    app = user_router
    if auth_before_custom_middleware:
        # Authentication middleware is only applied to protected routes--
        # it is *not* global middleware. This means that by default,
        # authentication middleware is necessarily applied *after* any global middleware.
        # including custom middleware that the user might have supplied.
        #
        # To apply authentication middleware before custom middleware,
        # we must rearrange things a bit:
        #   1. Extract user-supplied routes and bundle them into a `Mount`
        #      so that we can easily apply custom middleware to all of them at once.
        #   2. Extract custom middleware from the user-supplied app.
        #      Remove it globally, but apply it to each bundle of routes at the mount level.
        #      This gives us more flexibility in ordering: we can now apply this
        #      custom middleware before *or* after authentication middleware,
        #      depending on the `middleware_order` config.
        user_app = apply_middleware(
            routes=app.routes,
            middleware=(
                middleware_for_protected_routes if enable_auth_on_custom_routes else []
            )
            + route_level_custom_middleware,
        )
        app.user_middleware = global_middleware + _store_access_middleware
    else:
        user_app = (
            apply_middleware(
                routes=app.routes,
                middleware=middleware_for_protected_routes,
            )
            if enable_auth_on_custom_routes
            else app.routes
        )
        app.user_middleware = (
            custom_middleware + global_middleware + _store_access_middleware
        )

    app.router.routes = (
        apply_middleware(unshadowable_meta_routes, route_level_custom_middleware)
        + user_app
        + apply_middleware(shadowable_meta_routes, route_level_custom_middleware)
        + [protected_mount]
    )

    update_openapi_spec(app)

    # Merge lifespans (base + user)
    user_lifespan = app.router.lifespan_context
    validate_router_lifespan_hooks(app.router)
    app.router.lifespan_context = timing.combine_lifespans(lifespan, user_lifespan)

    # Merge exception handlers (base + user)
    for k, v in exception_handlers.items():
        if k not in app.exception_handlers:
            app.exception_handlers[k] = v
        else:
            logger.debug(f"Overriding exception handler for {k}")
else:
    # It's a regular starlette app
    app = Starlette(
        routes=[
            *apply_middleware(
                unshadowable_meta_routes + shadowable_meta_routes,
                route_level_custom_middleware,
            ),
            protected_mount,
        ],
        lifespan=timing.combine_lifespans(lifespan),
        middleware=global_middleware,
        exception_handlers=exception_handlers,
    )

# If the user creates a loopback client with `get_client() (no url)
# this will update the http transport to connect to the right app
configure_loopback_transports(app)

if config.MOUNT_PREFIX:
    from starlette.routing import Route

    from langgraph_api.api import meta_metrics, ok

    prefix = config.MOUNT_PREFIX
    if not prefix.startswith("/") or prefix.endswith("/"):
        raise ValueError(
            f"Invalid mount_prefix '{prefix}': Must start with '/' and must not end with '/'. "
            f"Valid examples: '/my-api', '/v1', '/api/v1'.\nInvalid examples: 'api/', '/api/'"
        )
    logger.info(f"Mounting routes at prefix: {prefix}")

    class ASGIBypassMiddleware:
        def __init__(self, app: typing.Any, **kwargs):
            self.app = app

        async def __call__(
            self, scope: Scope, receive: Receive, send: Send
        ) -> typing.Any:
            if (root_path := scope.get("root_path")) and root_path == "/noauth":
                # The SDK initialized with None is trying to connect via
                # ASGITransport. Ensure that it has the correct subpath prefixes
                # so the regular router can handle it.
                scope["path"] = f"/noauth{prefix}{scope['path']}"
                scope["raw_path"] = scope["path"].encode("utf-8")

            return await self.app(scope, receive, send)

    # Store reference to the original app before creating the wrapper
    original_app = app

    # Add health checks at root still to avoid having to override health checks.
    app = Starlette(
        routes=[
            Route("/", ok, methods=["GET"]),
            Route("/ok", ok, methods=["GET"]),
            Route("/metrics", meta_metrics, methods=["GET"]),
            Mount(prefix, app=original_app),
        ],
        lifespan=original_app.router.lifespan_context,
        middleware=[Middleware(ASGIBypassMiddleware)],
        exception_handlers=original_app.exception_handlers,
    )

    # Share the original app's state with the wrapper app.
    # This ensures that when MOUNT_PREFIX is set and request.app points to the wrapper app,
    # request.app.state still provides access to the state set on the original app.
    app.state = original_app.state
