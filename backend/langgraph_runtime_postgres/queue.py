import asyncio
import asyncio.events
import asyncio.exceptions
import concurrent.futures
import functools
import time
from collections.abc import Callable, Coroutine
from contextlib import ExitStack, suppress
from datetime import UTC, datetime
from typing import cast

import structlog

from langgraph_api import config
from langgraph_api import store as api_store
from langgraph_api.grpc.client import close_shared_client
from langgraph_api.grpc.ops import Runs
from langgraph_api.metrics_datadog import (
    COUNTER_FAILED_TO_FETCH_RUNS,
    COUNTER_RUN_ABANDONED_BY_SHUTDOWN,
    GAUGE_WORKERS_ACTIVE,
    GAUGE_WORKERS_AVAILABLE,
    LATENCY_RUN_QUEUE_WAIT_TIME_1ST_ATTEMPT,
    LATENCY_RUN_QUEUE_WAIT_TIME_RETRY_ATTEMPT,
    get_datadog_metrics_reporter,
)
from langgraph_api.utils import future as lg_future
from langgraph_runtime_postgres.database import stop_pool
from langgraph_runtime_postgres.redis import LIST_RUN_QUEUE, get_redis

logger = structlog.stdlib.get_logger(__name__)

WORKERS: set[lg_future.AnyFuture] = set()
WEBHOOKS: set[concurrent.futures.Future] = set()

SHUTDOWN_GRACE_PERIOD_SECS = 5


def get_num_workers():
    return len(WORKERS)


class BgLoopRunner(asyncio.Runner):
    """
    A runner that runs a loop in a separate thread. It's very important to
    use run the loop always in the same thread, as some objects may be created
    which are bound to the loop's thread.
    """

    executor: concurrent.futures.ThreadPoolExecutor

    def __init__(self, idx: int):
        super().__init__()
        self.idx = idx

    def __enter__(self):
        self.executor = concurrent.futures.ThreadPoolExecutor(
            1,
            thread_name_prefix=f"bg-loop-{self.idx}",
        )
        self.executor.submit(self.get_loop).result()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        loop = self.get_loop()

        for task in asyncio.all_tasks(loop):
            task.cancel("Stopping background loop")

        try:
            if not loop.is_running():
                self.executor.submit(self.run, stop_pool()).result(
                    SHUTDOWN_GRACE_PERIOD_SECS / 2,
                )
            else:
                asyncio.run_coroutine_threadsafe(stop_pool(), loop).result(
                    SHUTDOWN_GRACE_PERIOD_SECS / 2,
                )
        except TimeoutError:
            pass

        try:
            if not loop.is_running():
                self.executor.submit(self.run, api_store.exit_store()).result(
                    SHUTDOWN_GRACE_PERIOD_SECS / 2,
                )
            else:
                asyncio.run_coroutine_threadsafe(api_store.exit_store(), loop).result(
                    SHUTDOWN_GRACE_PERIOD_SECS / 2,
                )
        except TimeoutError:
            pass

        try:
            if not loop.is_running():
                self.executor.submit(self.run, close_shared_client()).result(
                    SHUTDOWN_GRACE_PERIOD_SECS / 2,
                )
            else:
                asyncio.run_coroutine_threadsafe(close_shared_client(), loop).result(
                    SHUTDOWN_GRACE_PERIOD_SECS / 2,
                )
        except TimeoutError:
            pass

        self.executor.shutdown(wait=False)

    def submit(
        self,
        coro: Coroutine,
        *,
        name: str | None = None,
        callback: Callable[[lg_future.AnyFuture], None] | None = None,
    ):
        fut = self.executor.submit(
            self.run,
            coro,
            name=name,
        )
        WORKERS.add(fut)
        if callback:
            fut.add_done_callback(callback)
        return fut

    def run(self, coro: Coroutine, *, name: str | None = None):
        """Run a coroutine inside the embedded event loop.
        Modified from asyncio.Runner.run
        - Removed main thread check (we only use it on bg threads)
        - Added callback and name arguments
        - Added WORKERS set to track tasks
        """
        if asyncio.events._get_running_loop() is not None:
            raise RuntimeError(
                "Runner.run() cannot be called from a running event loop",
            )
        self._lazy_init()
        task = self._loop.create_task(coro, name=name)
        try:
            return self._loop.run_until_complete(task)
        except asyncio.exceptions.CancelledError:
            raise


async def stats_loop():
    reporter = get_datadog_metrics_reporter()
    while True:
        await asyncio.sleep(config.STATS_INTERVAL_SECS)
        active = len(WORKERS)
        available = config.N_JOBS_PER_WORKER - active
        reporter.record_gauge(GAUGE_WORKERS_ACTIVE, float(active))
        reporter.record_gauge(GAUGE_WORKERS_AVAILABLE, float(available))
        await logger.ainfo(
            "Worker stats",
            max=config.N_JOBS_PER_WORKER,
            available=available,
            active=active,
        )


async def shutdown_queue(
    loop: asyncio.AbstractEventLoop,
    timeout: float,
    futs: list[asyncio.Future] | None = None,
):
    if not futs:
        futs = []

    if config.BG_JOB_ISOLATED_LOOPS:
        futs.extend(
            [
                cast(
                    asyncio.Future,
                    lg_future.chain_future(f, loop.create_future()),
                )
                for f in WORKERS
            ],
        )
    else:
        futs.extend([cast(asyncio.Future, f) for f in WORKERS])

    futs.extend(
        [
            cast(
                asyncio.Future,
                lg_future.chain_future(w, loop.create_future()),
            )
            for w in WEBHOOKS
        ],
    )

    await asyncio.wait_for(
        asyncio.gather(*futs, return_exceptions=True),
        timeout,
    )


async def queue():
    from langgraph_api import graph, webhook, worker
    from langgraph_api.asyncio import AsyncQueue
    from langgraph_api.schema import Run

    concurrency = config.N_JOBS_PER_WORKER
    reporter = get_datadog_metrics_reporter()
    loop = asyncio.get_running_loop()

    runners = AsyncQueue[BgLoopRunner](concurrency)
    with ExitStack() as stack:
        if config.BG_JOB_ISOLATED_LOOPS:
            await logger.ainfo("Starting queue with isolated loops")
            executor = stack.enter_context(concurrent.futures.ThreadPoolExecutor())
            RUNNERS = {stack.enter_context(BgLoopRunner(idx)) for idx in range(concurrency)}
            for r in RUNNERS:
                runners.put_nowait(r)
                r.get_loop().set_default_executor(executor)
        else:
            await logger.ainfo("Starting queue with shared loop")
            for _ in range(concurrency):
                runners.put_nowait(cast(BgLoopRunner, object()))
        expired_runners: list[BgLoopRunner] = []

        async def get_graph_id(run: Run) -> str | None:
            try:
                return run["kwargs"]["config"]["configurable"]["graph_id"]
            except Exception as exc1:
                await logger.aexception(
                    "Failed to get graph id from run",
                    run_id=run["run_id"],
                    exc_info=exc1,
                )
                try:
                    await Runs.set_status(None, run["run_id"], "error")
                except Exception as exc2:
                    await logger.aexception(
                        "Failed to set run status to error",
                        run_id=run["run_id"],
                        exc_info=exc2,
                    )
                return None

        def release_runner(runner: BgLoopRunner):
            try:
                if config.BG_JOB_ISOLATED_LOOPS:
                    loop.call_soon_threadsafe(runners.put_nowait, runner)
                    return
                runners.put_nowait(runner)
            except Exception as exc:
                expired_runners.append(runner)
                logger.exception("Background worker cleanup failed", exc_info=exc)

        def cleanup(
            task: lg_future.AnyFuture,
            runner: BgLoopRunner,
        ):
            WORKERS.discard(task)
            release_runner(runner)
            try:
                if task.cancelled():
                    return
                if exc := task.exception():
                    if not isinstance(exc, asyncio.CancelledError):
                        logger.exception(
                            f"Background worker failed for task {task}",
                            exc_info=exc,
                        )
                    return
                result: worker.WorkerResult | None = task.result()
                if result and result["webhook"]:
                    hook_fut = asyncio.run_coroutine_threadsafe(
                        webhook.call_webhook(result),
                        loop,
                    )
                    WEBHOOKS.add(hook_fut)
                    hook_fut.add_done_callback(WEBHOOKS.remove)
            except Exception as exc:
                logger.exception("Background worker cleanup failed", exc_info=exc)

        await logger.ainfo(f"Starting {concurrency} background workers")
        stats_task = asyncio.create_task(stats_loop())
        try:
            while True:
                if expired_runners:
                    await logger.awarning(
                        "Background worker expired, adding to queue",
                        num=len(expired_runners),
                    )
                    for runner in expired_runners:
                        await runners.put(runner)
                    expired_runners.clear()
                await runners.wait()
                runner = None
                try:
                    run = None
                    async for run, attempt, encryption_context in Runs.next(
                        wait=True,
                        limit=runners.qsize(),
                    ):
                        wait_ms = int(
                            (datetime.now(UTC) - run["created_at"]).total_seconds() * 1000,
                        )
                        await logger.ainfo(
                            "Dequeued run for background worker",
                            run_id=str(run["run_id"]),
                            thread_id=str(run["thread_id"]),
                            attempt=attempt,
                            run_queue_wait_ms=wait_ms,
                        )
                        if attempt == 1:
                            reporter.record_latency(
                                LATENCY_RUN_QUEUE_WAIT_TIME_1ST_ATTEMPT,
                                float(wait_ms) / 1000.0,
                            )
                        else:
                            reporter.record_latency(
                                LATENCY_RUN_QUEUE_WAIT_TIME_RETRY_ATTEMPT,
                                float(wait_ms) / 1000.0,
                            )
                        runner = runners.get_nowait()
                        graph_id = await get_graph_id(run)
                        if graph_id is None:
                            release_runner(runner)
                            runner = None
                            continue
                        if not config.BG_JOB_ISOLATED_LOOPS or (
                            graph_id and graph.is_js_graph(graph_id)
                        ):
                            task = asyncio.create_task(
                                worker.worker(
                                    run,
                                    attempt,
                                    loop,
                                    encryption_context=encryption_context,
                                ),
                                name=f"run-{run['run_id']}-attempt-{attempt}",
                            )
                            task.add_done_callback(
                                functools.partial(cleanup, runner=runner),
                            )
                            runner = None
                            WORKERS.add(task)
                        else:
                            runner.submit(
                                worker.worker(
                                    run,
                                    attempt,
                                    loop,
                                    encryption_context=encryption_context,
                                ),
                                name=f"run-{run['run_id']}-attempt-{attempt}",
                                callback=functools.partial(cleanup, runner=runner),
                            )
                            runner = None
                except Exception as exc:
                    reporter.inc_counter(COUNTER_FAILED_TO_FETCH_RUNS)
                    logger.exception(
                        "Background worker scheduler failed",
                        exc_info=exc,
                    )
                    if runner is not None:
                        release_runner(runner)
                    try:
                        if not config.FF_USE_REDIS_QUEUE:
                            await get_redis().lpush(LIST_RUN_QUEUE, 1)
                    except Exception as e:
                        logger.exception("Failed to wake up worker", exc_info=e)
        except asyncio.CancelledError as e:
            log_kwargs = {}
            if e.args:
                log_kwargs["reason"] = " ".join(map(str, e.args))
            await logger.awarning(
                "Queue task cancelled. Shutting down workers. Will terminate after "
                f"{config.BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS}s",
                **log_kwargs,
            )
            reporter.inc_counter(
                COUNTER_RUN_ABANDONED_BY_SHUTDOWN,
                value=len(WORKERS),
            )
            start = time.perf_counter()
            await shutdown_queue(loop, config.BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS)
            elapsed = time.perf_counter() - start
            await logger.ainfo("Workers finished.")
        finally:
            stats_task.cancel("Shutting down background workers")
            remaining_shutdown_time = 0
            with suppress(UnboundLocalError):
                remaining_shutdown_time = config.BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS - elapsed
            cleanup_tasks = [stats_task]
            await shutdown_queue(
                loop,
                max(remaining_shutdown_time, SHUTDOWN_GRACE_PERIOD_SECS),
                cleanup_tasks,
            )
            await logger.ainfo("Successfully shutdown queue")
