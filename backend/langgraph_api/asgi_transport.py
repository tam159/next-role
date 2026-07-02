"""ASGI transport that lets you schedule to the main loop.

Adapted from: https://github.com/encode/httpx/blob/6c7af967734bafd011164f2a1653abc87905a62b/httpx/_transports/asgi.py#L1
"""

from __future__ import annotations

import asyncio
import typing

from httpx import ASGITransport as ASGITransportBase
from httpx import AsyncByteStream, Request, Response

if typing.TYPE_CHECKING:  # pragma: no cover
    import trio  # type: ignore[unresolved-import]

    Event = asyncio.Event | trio.Event

__all__ = ["ASGITransport"]

_STREAM_END = object()


def is_running_trio() -> bool:
    try:
        # sniffio is a dependency of trio.

        # See https://github.com/python-trio/trio/issues/2802
        import sniffio  # type: ignore[unresolved-import]  # noqa: PLC0415

        if sniffio.current_async_library() == "trio":
            return True
    except ImportError:  # pragma: nocover
        pass

    return False


def create_event() -> Event:
    if is_running_trio():
        import trio  # type: ignore[unresolved-import]  # noqa: PLC0415

        return trio.Event()

    return asyncio.Event()


class ASGIResponseStream(AsyncByteStream):
    def __init__(
        self,
        body_queue: asyncio.Queue[bytes | object],
        app_complete: asyncio.Future[None],
        app_future: asyncio.Future[None],
        close_stream: typing.Callable[[], None],
    ) -> None:
        self._body_queue = body_queue
        self._app_complete = app_complete
        self._app_future = app_future
        self._close_stream = close_stream
        self._closed = False

    async def __aiter__(self) -> typing.AsyncIterator[bytes]:
        try:
            while True:
                chunk = await self._body_queue.get()
                if chunk is _STREAM_END:
                    break
                yield typing.cast("bytes", chunk)
            await self._app_complete
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_stream()
        if not self._app_future.done():
            self._app_future.cancel()


class ASGITransport(ASGITransportBase):
    """
    A custom AsyncTransport that handles sending requests directly to an ASGI app.

    ```python
    transport = httpx.ASGITransport(
        app=app,
        root_path="/submount",
        client=("1.2.3.4", 123)
    )
    client = httpx.AsyncClient(transport=transport)
    ```

    Arguments:

    * `app` - The ASGI application.
    * `raise_app_exceptions` - Boolean indicating if exceptions in the application
       should be raised. Default to `True`. Can be set to `False` for use cases
       such as testing the content of a client 500 response.
    * `root_path` - The root path on which the ASGI application should be mounted.
    * `client` - A two-tuple indicating the client IP and port of incoming requests.
    ```
    """

    async def handle_async_request(
        self,
        request: Request,
    ) -> Response:
        from langgraph_api.asyncio import call_soon_in_main_loop  # noqa: PLC0415

        if not isinstance(request.stream, AsyncByteStream):
            raise ValueError("Request stream must be an AsyncByteStream")

        # ASGI scope.
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": self.client,
            "root_path": self.root_path,
        }

        # Request.
        request_body_chunks = request.stream.__aiter__()
        request_complete = False

        # Response.
        current_loop = asyncio.get_running_loop()
        status_code = None
        response_headers = None
        response_started = False
        response_started_event = current_loop.create_future()
        app_complete = current_loop.create_future()
        response_complete = create_event()
        body_queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        stream_closed = False

        def close_stream() -> None:
            nonlocal stream_closed
            if stream_closed:
                return
            stream_closed = True
            body_queue.put_nowait(_STREAM_END)

        def close_stream_threadsafe() -> None:
            current_loop.call_soon_threadsafe(close_stream)

        def set_response_started() -> None:
            # Runs on current_loop, scheduled from send() on the app loop.
            # send() enqueues this before the app can finish, so by the
            # time finish_app fires we can read response_started_event
            # alone to know whether http.response.start was sent.
            if not response_started_event.done():
                response_started_event.set_result(None)

        def response_was_started() -> bool:
            # True iff set_response_started() ran (result=None).
            # Exception/cancel states on the future mean we got here via
            # an error path, not via send().
            return (
                response_started_event.done()
                and not response_started_event.cancelled()
                and response_started_event.exception() is None
            )

        # ASGI callables.

        async def receive() -> dict[str, typing.Any]:
            nonlocal request_complete

            if request_complete:
                await response_complete.wait()
                return {"type": "http.disconnect"}

            try:
                body = await request_body_chunks.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message: typing.MutableMapping[str, typing.Any]) -> None:
            nonlocal status_code, response_headers, response_started

            if message["type"] == "http.response.start":
                if response_started:
                    raise RuntimeError("Response already started")
                status_code = message["status"]
                response_headers = message.get("headers", [])
                response_started = True
                current_loop.call_soon_threadsafe(set_response_started)

            elif message["type"] == "http.response.body":
                if response_complete.is_set():
                    raise RuntimeError("Response already complete")
                body = message.get("body", b"")
                more_body = message.get("more_body", False)

                if body and request.method != "HEAD":
                    current_loop.call_soon_threadsafe(body_queue.put_nowait, body)

                if not more_body:
                    response_complete.set()
                    close_stream_threadsafe()

        app_future = call_soon_in_main_loop(self.app(scope, receive, send))

        def finish_app(fut: asyncio.Future[None]) -> None:
            try:
                fut.result()
            except asyncio.CancelledError:
                if response_was_started():
                    # Cancellation mid-stream: surface to the consumer
                    # after they finish reading what's already in the
                    # body queue.
                    if not app_complete.done():
                        app_complete.cancel()
                else:
                    # Cancellation before headers: cancel the awaiter.
                    # CancelledError inherits from BaseException (not
                    # Exception) since Python 3.8, so the `except
                    # Exception` in handle_async_request does NOT catch
                    # it — it escapes out of the transport, bypassing
                    # the raise_app_exceptions=False fallback. That's
                    # intentional: cancellation is control flow, not an
                    # application error.
                    if not response_started_event.done():
                        response_started_event.cancel()
                    if not app_complete.done():
                        app_complete.set_result(None)
            except Exception as exc:
                if response_was_started():
                    # Error after headers: the awaiter has already moved
                    # past response_started_event, so app_complete is
                    # the only remaining catch point. raise_app_exceptions
                    # must be consulted here at bind time — there's no
                    # later place to drop the exception.
                    if not app_complete.done():
                        if self.raise_app_exceptions:
                            app_complete.set_exception(exc)
                        else:
                            app_complete.set_result(None)
                else:
                    # Error before headers: bind unconditionally. The
                    # gating on raise_app_exceptions happens in the
                    # `await response_started_event` block below, which
                    # either re-raises or falls back to a synthetic 500.
                    if not response_started_event.done():
                        response_started_event.set_exception(exc)
                    if not app_complete.done():
                        app_complete.set_result(None)
            else:
                # Normal return. Three sub-cases:
                if not response_started_event.done():
                    # 1. App exited without ever sending response.start.
                    response_started_event.set_exception(
                        RuntimeError("Response not complete")
                    )
                    if not app_complete.done():
                        app_complete.set_result(None)
                elif not response_complete.is_set():
                    # 2. App started the response (and possibly sent
                    # partial body) but never signaled more_body=False.
                    # The consumer drains queued chunks then sees the
                    # error. Bound unconditionally — this is an ASGI
                    # protocol error, not an app error, so it's not
                    # gated on raise_app_exceptions.
                    if not app_complete.done():
                        app_complete.set_exception(
                            RuntimeError("Response not complete")
                        )
                else:
                    # 3. Clean completion (send saw more_body=False).
                    if not app_complete.done():
                        app_complete.set_result(None)
            finally:
                close_stream()

        app_future.add_done_callback(finish_app)

        try:
            await response_started_event
        except Exception:
            if self.raise_app_exceptions:
                raise
            response_complete.set()
            if status_code is None:
                status_code = 500
            if response_headers is None:
                response_headers = {}
            if not app_complete.done():
                app_complete.set_result(None)
            close_stream()

        if status_code is None:
            raise RuntimeError("Status code not set")
        if response_headers is None:
            raise RuntimeError("Response headers not set")

        stream = ASGIResponseStream(body_queue, app_complete, app_future, close_stream)

        return Response(status_code, headers=response_headers, stream=stream)
