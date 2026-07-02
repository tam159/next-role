import asyncio
import os
import sys
import threading
from contextlib import asynccontextmanager

import structlog
from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config
from langgraph.constants import CONF
from starlette.applications import Starlette

from langgraph_api.config import BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS
from langgraph_runtime_postgres import (
    checkpoint,
    database,
    long_query_monitor,
    queue,
)

logger = structlog.stdlib.get_logger(__name__)


_LAST_LIFESPAN_ERROR: BaseException | None = None


def get_last_error() -> BaseException | None:
    return _LAST_LIFESPAN_ERROR


@asynccontextmanager
async def lifespan(
    app: Starlette | None = None,
    *,
    cancel_event: asyncio.Event | None = None,
    with_cron_scheduler: bool = True,
    taskset: set[asyncio.Task] | None = None,
):
    from langgraph_api import (
        __version__,
        config,
        cron_scheduler,
        feature_flags,
        graph,
        http,
        metadata,
    )
    from langgraph_api import (
        _checkpointer as api_checkpointer,
    )
    from langgraph_api import (
        asyncio as langgraph_asyncio,
    )
    from langgraph_api import store as api_store
    from langgraph_api.js import (
        ui,
    )

    global _LAST_LIFESPAN_ERROR
    _LAST_LIFESPAN_ERROR = None

    await logger.ainfo(
        f"Starting Postgres runtime with langgraph-api={__version__}",
        version=__version__,
    )
    try:
        current_loop = asyncio.get_running_loop()
        langgraph_asyncio.set_event_loop(current_loop)
    except RuntimeError:
        await logger.aerror("Failed to set loop")

    await database.start_pool()
    await api_checkpointer.start_checkpointer()
    await checkpoint.start_checkpoint_ingestion_loop()

    # Upstream verified a LangGraph Cloud license here; NextRole is open source
    # and ships no license machinery (the stub always returned True anyway).

    if config.LANGGRAPH_LOGS_ENABLED:
        from langgraph_api import self_hosted_logs

        self_hosted_logs.initialize_self_hosted_logs()

    await http.start_http_client()
    await ui.start_ui_bundler()

    grpc_waits = []

    if config.PYTHON_GRPC_SERVER_ENABLED:
        from langgraph_api.grpc.server import (
            run_python_grpc_server,
            wait_until_python_grpc_ready,
        )

        langgraph_asyncio.create_task(run_python_grpc_server(port=config.PYTHON_GRPC_SERVER_PORT))
        grpc_waits.append(wait_until_python_grpc_ready())

    from langgraph_api.grpc.client import wait_until_grpc_ready

    grpc_waits.append(wait_until_grpc_ready())
    await asyncio.gather(*grpc_waits)

    if config.LANGGRAPH_METRICS_ENABLED:
        from langgraph_api import self_hosted_metrics

        self_hosted_metrics.initialize_self_hosted_metrics()
    if config.DATADOG_METRICS_ENABLED:
        from langgraph_api.metrics_datadog import (
            COUNTER_SERVER_STARTED,
            get_datadog_metrics_reporter,
        )

        reporter = get_datadog_metrics_reporter()
        reporter.initialize()
        reporter.inc_counter(COUNTER_SERVER_STARTED)

    async def _log_graph_load_failure(err: graph.GraphLoadError) -> None:
        cause = err.__cause__ or err.cause
        log_fields = err.log_fields()
        log_fields["action"] = "fix_user_graph"
        await logger.aerror(
            f"Graph '{err.spec.id}' failed to load: {err.cause_message}",
            **log_fields,
        )
        await logger.adebug(
            "Full graph load failure traceback (internal)",
            **{k: v for k, v in log_fields.items() if k != "user_traceback"},
            exc_info=cause,
        )

    try:
        async with langgraph_asyncio.SimpleTaskGroup(
            cancel=True,
            cancel_event=cancel_event,
            taskgroup_name="Lifespan",
        ) as tg:
            tg.create_task(metadata.metadata_loop())
            await api_store.collect_store_from_env()
            store_instance = await api_store.get_store()
            if not api_store.CUSTOM_STORE:
                tg.create_task(store_instance.start_ttl_sweeper())
            else:
                await logger.ainfo("Using custom store. Skipping store TTL sweeper.")

            tg.create_task(long_query_monitor.long_query_monitor_loop())

            if feature_flags.USE_RUNTIME_CONTEXT_API:
                from langgraph._internal._constants import (
                    CONFIG_KEY_RUNTIME,
                )
                from langgraph.runtime import Runtime

                langgraph_config: RunnableConfig = {
                    CONF: {CONFIG_KEY_RUNTIME: Runtime(store=store_instance)},
                }
            else:
                from langgraph.constants import CONFIG_KEY_STORE

                langgraph_config: RunnableConfig = {CONF: {CONFIG_KEY_STORE: store_instance}}

            var_child_runnable_config.set(langgraph_config)

            graph.patch_packages_distributions()
            try:
                await graph.collect_graphs_from_env(True)
            except graph.GraphLoadError as exc:
                _LAST_LIFESPAN_ERROR = exc
                await _log_graph_load_failure(exc)
                raise
            if config.N_JOBS_PER_WORKER > 0:
                tg.create_task(queue_with_signal())
            else:
                await logger.ainfo("N_JOBS_PER_WORKER is 0. Skipping queue.")

            if with_cron_scheduler:
                tg.create_task(cron_scheduler.cron_scheduler())

            yield
    except graph.GraphLoadError as exc:
        _LAST_LIFESPAN_ERROR = exc
        raise
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        _LAST_LIFESPAN_ERROR = exc
        logger.exception("Lifespan failed", exc_info=True)
    finally:
        await api_checkpointer.exit_checkpointer()
        await api_store.exit_store()
        await ui.stop_ui_bundler()
        if config.LANGGRAPH_METRICS_ENABLED:
            from langgraph_api import self_hosted_metrics

            self_hosted_metrics.shutdown_self_hosted_metrics()
        if config.DATADOG_METRICS_ENABLED:
            from langgraph_api.metrics_datadog import (
                COUNTER_SERVER_REQUESTED_TO_STOP,
                COUNTER_SERVER_STOPPED,
                get_datadog_metrics_reporter,
            )

            reporter = get_datadog_metrics_reporter()
            reporter.inc_counter(COUNTER_SERVER_REQUESTED_TO_STOP)
            reporter.inc_counter(COUNTER_SERVER_STOPPED)
            reporter.shutdown()

        await graph.stop_remote_graphs()
        await http.stop_http_client()
        await http.stop_webhook_http_client()

        from langgraph_api.grpc.client import close_shared_client

        await close_shared_client()

        await checkpoint.stop_checkpoint_ingestion_loop()
        await database.stop_pool()

        if config.PYTHON_GRPC_SERVER_ENABLED:
            from langgraph_api.grpc.server import (
                stop_python_grpc_server,
            )

            await stop_python_grpc_server()

        if config.LANGGRAPH_LOGS_ENABLED:
            from langgraph_api import self_hosted_logs

            self_hosted_logs.shutdown_self_hosted_logs()


async def queue_with_signal():
    try:
        await queue.queue()
    except asyncio.CancelledError:
        return
    except TimeoutError as e:
        await logger.aerror(
            f"The queue surpassed the grace period while shutting down. Some runs may still be running and will be canceled and retried on a healthy instance. The grace period is currently set to {BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS} seconds. If that is too low, adjust the BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS environment variable to an appropriate value. Signaling shutdown",
            exc_info=e,
            BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS=BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS,
        )
        _exit_after_flush()
    except Exception as exc:
        await logger.aexception("Queue failed. Signaling shutdown", exc_info=exc)
        _exit_after_flush()


def _exit_after_flush(timeout: float = 5.0) -> None:
    """Trigger Python's normal shutdown sequence, then hard-exit if needed.

    sys.exit(1) raises SystemExit which lets asyncio clean up, runs atexit
    handlers (ddtrace and LangSmith background flush threads), and joins
    non-daemon threads — including stuck BgLoopRunner workers when
    BG_JOB_ISOLATED_LOOPS is enabled.

    The daemon timer ensures os._exit fires after `timeout` seconds if any
    thread blocks exit (e.g. a BgLoopRunner worker stuck on an in-flight
    request). If the process exits cleanly before the timer fires, the daemon
    timer is discarded harmlessly.
    """

    def _hard_exit() -> None:
        sys.stderr.write(
            f"Queue shutdown timer expired: threads did not exit within {timeout}s grace period. Force-exiting via os._exit.\n",
        )
        sys.stderr.flush()
        os._exit(1)

    t = threading.Timer(timeout, _hard_exit)
    t.daemon = True
    t.start()
    sys.exit(1)


lifespan.get_last_error = get_last_error  # type: ignore[attr-defined]
