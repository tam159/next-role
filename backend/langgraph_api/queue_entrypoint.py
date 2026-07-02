import os

if not (
    (disable_truststore := os.getenv("DISABLE_TRUSTSTORE"))
    and disable_truststore.lower() == "true"
):
    import truststore

    truststore.inject_into_ssl()

import asyncio
import functools
import json
import logging.config
import pathlib
import signal
import socket
from contextlib import suppress

import structlog

from langgraph_api.api.meta import meta_pool_stats
from langgraph_api.utils.errors import GraphLoadError, HealthServerStartupError
from langgraph_api.utils.network import format_hostport, normalize_host
from langgraph_runtime import lifespan
from langgraph_runtime.database import healthcheck
from langgraph_runtime.metrics import get_metrics

logger = structlog.stdlib.get_logger(__name__)

health_server_task: asyncio.Task | None = None
shutdown_reason: str | None = None


def _ensure_port_available(host: str, port: int) -> None:
    host = normalize_host(host)
    # Pin AF_INET6 for IPv6 literals: glibc's getaddrinfo can return EAI_ADDRFAMILY
    # on IPv6-only hosts when the family is unspecified.
    family = socket.AF_INET6 if ":" in host else socket.AF_UNSPEC
    last_error: OSError | None = None
    try:
        addrinfos = socket.getaddrinfo(
            host,
            port,
            family=family,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
    except OSError as exc:
        raise HealthServerStartupError(host, port, exc) from exc

    for family, socktype, proto, _, sockaddr in addrinfos:
        try:
            with socket.socket(family, socktype, proto) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(sockaddr)
                return
        except OSError as exc:
            last_error = exc

    if last_error is None:
        last_error = OSError(f"Could not resolve bind address for host {host!r}")
    raise HealthServerStartupError(host, port, last_error)


async def health_and_metrics_server():
    import uvicorn  # noqa: PLC0415
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import JSONResponse, PlainTextResponse  # noqa: PLC0415
    from starlette.routing import Mount, Route  # noqa: PLC0415

    from langgraph_api import config as lc_config  # noqa: PLC0415
    from langgraph_api.api.meta import METRICS_FORMATS  # noqa: PLC0415

    port = int(os.getenv("PORT", "8080"))
    # Not in public docs: LANGGRAPH_SERVER_HOST is internal
    host = normalize_host(os.getenv("LANGGRAPH_SERVER_HOST", "0.0.0.0"))

    async def health_endpoint(request: Request):
        check_db = int(request.query_params.get("check_db", "1"))

        await healthcheck(check_db=bool(check_db))
        return JSONResponse({"status": "ok"})

    async def metrics_endpoint(request: Request):
        metrics_format = request.query_params.get("format", "prometheus")
        if metrics_format not in METRICS_FORMATS:
            await logger.awarning(
                f"metrics format {metrics_format} not supported, falling back to prometheus"
            )
            metrics_format = "prometheus"

        metrics = get_metrics()
        worker_metrics = metrics["workers"]
        workers_max = worker_metrics["max"]
        workers_active = worker_metrics["active"]
        workers_available = worker_metrics["available"]

        project_id = os.getenv("LANGSMITH_HOST_PROJECT_ID")
        revision_id = os.getenv("LANGSMITH_HOST_REVISION_ID")

        pg_redis_stats = await meta_pool_stats(metrics_format)

        if metrics_format == "json":
            resp = {
                **pg_redis_stats,
                "workers": worker_metrics,
            }
            return JSONResponse(resp)
        elif metrics_format == "prometheus":
            metrics_lines = [
                "# HELP lg_api_workers_max The maximum number of workers available.",
                "# TYPE lg_api_workers_max gauge",
                f'lg_api_workers_max{{project_id="{project_id}", revision_id="{revision_id}"}} {workers_max}',
                "# HELP lg_api_workers_active The number of currently active workers.",
                "# TYPE lg_api_workers_active gauge",
                f'lg_api_workers_active{{project_id="{project_id}", revision_id="{revision_id}"}} {workers_active}',
                "# HELP lg_api_workers_available The number of available (idle) workers.",
                "# TYPE lg_api_workers_available gauge",
                f'lg_api_workers_available{{project_id="{project_id}", revision_id="{revision_id}"}} {workers_available}',
            ]

            metrics_lines.extend(pg_redis_stats)

            return PlainTextResponse(
                "\n".join(metrics_lines),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

    routes = [
        Route("/ok", health_endpoint),
        Route("/metrics", metrics_endpoint),
    ]
    app = Starlette(routes=routes)
    if lc_config.MOUNT_PREFIX:
        app = Starlette(
            routes=[*routes, Mount(lc_config.MOUNT_PREFIX, app=app)],
            lifespan=app.router.lifespan_context,
            exception_handlers=app.exception_handlers,
        )

    try:
        _ensure_port_available(host, port)
    except HealthServerStartupError as exc:
        await logger.aerror(
            str(exc),
            host=exc.host,
            port=exc.port,
            cause=str(exc.cause),
        )
        raise

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="error",
        access_log=False,
    )
    # Server will run indefinitely until the process is terminated
    server = uvicorn.Server(config)

    logger.info(
        f"Health and metrics server started at http://{format_hostport(host, port)}"
    )
    try:
        # Use the internal serve to skip capturing signals, otherwise there's a race condition
        # where uvicorn captures the signal and will exit early before the queue can gracefully shutdown
        await server._serve()
    except SystemExit as exc:
        if exc.code == 0:
            return
        try:
            _ensure_port_available(host, port)
        except HealthServerStartupError as port_exc:
            await logger.aerror(
                str(port_exc),
                host=port_exc.host,
                port=port_exc.port,
                cause=str(port_exc.cause),
            )
            raise port_exc from None
        error = HealthServerStartupError(host, port, exc)
        await logger.aerror(
            str(error), host=error.host, port=error.port, cause=str(error.cause)
        )
        raise error from None
    except OSError as exc:
        error = HealthServerStartupError(host, port, exc)
        await logger.aerror(
            str(error), host=error.host, port=error.port, cause=str(error.cause)
        )
        raise error from exc
    except asyncio.CancelledError:
        # Close the health server cleanly once we've exited the lifespan to make sure we respect graceful shutdown
        logger.info("Shutting down health and metrics server")
        await server.shutdown()


async def entrypoint(
    entrypoint_name: str = "python-queue",
    cancel_event: asyncio.Event | None = None,
):
    from langgraph_api import logging as lg_logging  # noqa: PLC0415
    from langgraph_api import timing  # noqa: PLC0415
    from langgraph_api.api import user_router  # noqa: PLC0415
    from langgraph_api.server import app  # noqa: PLC0415

    lg_logging.set_logging_context({"entrypoint": entrypoint_name})
    tasks: set[asyncio.Task] = set()
    user_lifespan = None if user_router is None else user_router.router.lifespan_context
    wrapped_lifespan = timing.combine_lifespans(
        functools.partial(
            lifespan.lifespan,
            with_cron_scheduler=False,
            taskset=tasks,
            cancel_event=cancel_event,
        ),
        user_lifespan,
    )

    async with wrapped_lifespan(app):
        # Create the health server task once all start up operations are complete and the API is ready to serve traffic
        global health_server_task
        health_server_task = asyncio.create_task(health_and_metrics_server())

        # If the health server fails unexpectedly, trigger shutdown
        def _on_health_server_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None and cancel_event is not None:
                logger.error(
                    "Health server failed unexpectedly, triggering shutdown",
                    error=str(exc),
                )
                global shutdown_reason
                shutdown_reason = f"health server failed: {exc}"
                cancel_event.set()

        health_server_task.add_done_callback(_on_health_server_done)

        # Keep everything running until it's time to shutdown
        # The only way we want to be able to exit this loop is through a cancelled error
        while True:
            await asyncio.sleep(3600)


async def main(entrypoint_name: str = "python-queue"):
    """Run the queue entrypoint and shut down gracefully on SIGTERM/SIGINT."""

    loop = asyncio.get_running_loop()
    cancel_event = asyncio.Event()

    # Attach signal handler for SIGTERM
    def _handle_signal() -> None:
        global shutdown_reason
        if not cancel_event.is_set():
            shutdown_reason = "sigterm signal received"
            cancel_event.set()

    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_signal)
    except (NotImplementedError, RuntimeError):
        signal.signal(signal.SIGTERM, lambda *_: _handle_signal())

    # Start the queue entrypoint
    entry_task = asyncio.create_task(
        entrypoint(
            entrypoint_name=entrypoint_name,
            cancel_event=cancel_event,
        )
    )

    # Handle the case where the entrypoint errors out
    def _on_entry_task_done(task: asyncio.Task) -> None:
        global shutdown_reason
        logger.info("Entrypoint task finished")
        if not cancel_event.is_set():
            shutdown_reason = "entrypoint task finished"
            cancel_event.set()

    entry_task.add_done_callback(_on_entry_task_done)

    # Wait for something to trigger shutdown
    await cancel_event.wait()
    logger.info("Shutting down queue...", shutdown_reason=shutdown_reason)

    # Wait for the queue entrypoint to finish
    entry_task.cancel()
    try:
        await entry_task
    except asyncio.CancelledError:
        pass
    except (GraphLoadError, HealthServerStartupError) as exc:
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        if str(exc) == "generator didn't yield":
            last_error = lifespan.get_last_error()
            if last_error is not None:
                logger.exception(
                    "Application startup failed",
                    error_type=type(last_error).__name__,
                    error_message=str(last_error),
                )
                raise SystemExit(1) from None
        raise
    except Exception as exc:
        logger.exception("Queue entrypoint task failed", exc_info=exc)
        raise SystemExit(1) from exc

    # Shutdown the health and metrics server
    global health_server_task
    if health_server_task is not None:
        health_server_task.cancel()
        # We suppress everything here as errors with the health server before cancellation have already been handled
        with suppress(asyncio.CancelledError, Exception):
            await health_server_task
        logger.info("Health and metrics server finished")


if __name__ == "__main__":
    from langgraph_api import config

    config.IS_QUEUE_ENTRYPOINT = True
    with open(pathlib.Path(__file__).parent.parent / "logging.json") as file:
        loaded_config = json.load(file)
        logging.config.dictConfig(loaded_config)
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass
    # run the entrypoint
    asyncio.run(main())
