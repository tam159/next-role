"""Middleware to handle setting request IDs for logging."""

import re
import time
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

PATHS_INCLUDE = ("/runs", "/threads")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp, mount_prefix: str = ""):
        self.app = app
        paths = (
            (mount_prefix + p for p in ("/runs", "/threads"))
            if mount_prefix
            else ("/runs", "/threads")
        )
        self.pattern = re.compile(r"^(" + "|".join(paths) + r")(/.*)?$")

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and self.pattern.match(scope["path"]):
            from langgraph_api.logging import set_logging_context  # noqa: PLC0415

            request_id = next(
                (h[1] for h in scope["headers"] if h[0] == b"x-request-id"),
                None,
            )
            if request_id is None:
                request_id = str(uuid.uuid4()).encode()
                scope["headers"].append((b"x-request-id", request_id))
            scope["request_start_time_ms"] = int(time.time() * 1000)
            set_logging_context({"request_id": request_id.decode()})
        await self.app(scope, receive, send)
