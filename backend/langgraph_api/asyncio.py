import asyncio
import concurrent.futures
from collections.abc import AsyncIterator, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from functools import partial
from typing import Any, Generic, TypeVar

import structlog

T = TypeVar("T")

logger = structlog.stdlib.get_logger(__name__)

_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def as_asynccontextmanager(value: Any) -> AsyncIterator[Any]:
    """Normalize an async CM, sync CM, or coroutine into a single async context manager.

    Handles the three factory return styles used across graph, store, and checkpointer
    loading without duplicating the dispatch logic.
    """
    if hasattr(value, "__aenter__") and hasattr(value, "__aexit__"):
        async with value as result:
            yield result
    elif hasattr(value, "__enter__") and hasattr(value, "__exit__"):
        with value as result:
            yield result
    elif asyncio.iscoroutine(value):
        yield await value
    else:
        yield value


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def get_event_loop() -> asyncio.AbstractEventLoop:
    if _MAIN_LOOP is None:
        raise RuntimeError("No event loop set")
    return _MAIN_LOOP


async def sleep_if_not_done(delay: float, done: asyncio.Event) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(done.wait(), delay)


class ValueEvent(asyncio.Event):
    def set(self, value: Any = True) -> None:
        """Set the internal flag to true. All coroutines waiting for it to
        become set are awakened. Coroutine that call wait() once the flag is
        true will not block at all.
        """
        if not self._value:
            self._value = value

            for fut in self._waiters:
                if not fut.done():
                    fut.set_result(value)

    async def wait(self):
        """Block until the internal flag is set.

        If the internal flag is set on entry, return value
        immediately.  Otherwise, block until another coroutine calls
        set() to set the flag, then return the value.
        """
        if self._value:
            return self._value

        fut = self._get_loop().create_future()
        self._waiters.append(fut)
        try:
            return await fut
        finally:
            self._waiters.remove(fut)


async def wait_if_not_done(coro: Coroutine[Any, Any, T], done: ValueEvent) -> T:
    """Wait for the coroutine to finish or the event to be set."""
    try:
        async with asyncio.TaskGroup() as tg:
            coro_task = tg.create_task(coro)
            done_task = tg.create_task(done.wait())
            coro_task.add_done_callback(
                lambda _: done_task.cancel("Coro task completed")
            )
            done_task.add_done_callback(lambda _: coro_task.cancel(done._value))
            try:
                return await coro_task
            except asyncio.CancelledError as e:
                if e.args and asyncio.isfuture(e.args[-1]):
                    fut = e.args[-1]
                    await logger.ainfo(
                        "Awaiting future upon cancellation.",
                        task=str(fut),
                    )
                    await fut
                    await logger.ainfo("Done awaiting.", task=str(fut))
                if e.args and isinstance(e.args[0], Exception):
                    raise e.args[0] from None
                raise
    except ExceptionGroup as e:
        raise e.exceptions[0] from None


PENDING_TASKS = set()


def _create_task_done_callback(
    ignore_exceptions: tuple[type[Exception], ...],
    task: asyncio.Task | asyncio.Future,
) -> None:
    PENDING_TASKS.discard(task)
    try:
        if (exc := task.exception()) and not isinstance(exc, ignore_exceptions):
            logger.exception("asyncio.task failed", exc_info=exc)
    except asyncio.CancelledError:
        pass


def create_task(
    coro: Coroutine[Any, Any, T], ignore_exceptions: tuple[Exception, ...] = ()
) -> asyncio.Task[T]:
    """Create a new task in the current task group and return it."""
    task = asyncio.create_task(coro)
    PENDING_TASKS.add(task)
    task.add_done_callback(partial(_create_task_done_callback, ignore_exceptions))
    return task


def run_coroutine_threadsafe(
    coro: Coroutine[Any, Any, T],
    ignore_exceptions: tuple[type[Exception], ...] = (),
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> concurrent.futures.Future[T] | concurrent.futures.Future[None]:
    if loop is None:
        loop = _MAIN_LOOP
    if loop is None:
        raise RuntimeError("No event loop set")
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    future.add_done_callback(partial(_create_task_done_callback, ignore_exceptions))
    return future


def call_soon_in_main_loop(coro: Coroutine[Any, Any, T]) -> asyncio.Future[T]:
    """Run a coroutine in the main event loop."""
    from langgraph_api.utils import future as lg_future  # noqa: PLC0415

    if _MAIN_LOOP is None:
        raise RuntimeError("No event loop set")
    main_loop_fut = asyncio.ensure_future(coro, loop=_MAIN_LOOP)
    this_loop_fut = asyncio.get_running_loop().create_future()
    _MAIN_LOOP.call_soon_threadsafe(
        lg_future.chain_future, main_loop_fut, this_loop_fut
    )
    return this_loop_fut


def call_soon_threadsafe(callback, *args) -> asyncio.Handle:
    """Run a coroutine in the main event loop."""
    if _MAIN_LOOP is None:
        raise RuntimeError("No event loop set")
    return _MAIN_LOOP.call_soon_threadsafe(callback, *args)


class SimpleTaskGroup(AbstractAsyncContextManager["SimpleTaskGroup"]):
    """An async task group that can be configured to wait and/or cancel tasks on exit.

    asyncio.TaskGroup and anyio.TaskGroup both expect enter and exit to be called
    in the same asyncio task, which is not true for our use case, where exit is
    shielded from cancellation."""

    tasks: set[asyncio.Task]

    def __init__(
        self,
        *coros: Coroutine[Any, Any, T],
        cancel: bool = False,
        cancel_event: asyncio.Event | None = None,
        wait: bool = True,
        taskset: set[asyncio.Task] | None = None,
        taskgroup_name: str | None = None,
    ) -> None:
        # Copy the taskset to avoid modifying the original set unintentionally (like in lifespan)
        self.tasks = taskset.copy() if taskset is not None else set()
        self.cancel = cancel
        self.cancel_event = cancel_event
        self.wait = wait
        if taskset:
            for task in tuple(taskset):
                task.add_done_callback(partial(self._create_task_done_callback, ()))
        for coro in coros:
            self.create_task(coro)
        self.taskgroup_name = f" {taskgroup_name} " if taskgroup_name else ""

    def _create_task_done_callback(
        self, ignore_exceptions: tuple[type[Exception], ...], task: asyncio.Task
    ) -> None:
        with suppress(AttributeError):
            self.tasks.remove(task)
        try:
            if (exc := task.exception()) and not isinstance(exc, ignore_exceptions):
                logger.exception("asyncio.task failed in task group", exc_info=exc)
                if self.cancel_event:
                    self.cancel_event.set()
        except asyncio.CancelledError:
            pass

    def create_task(
        self,
        coro: Coroutine[Any, Any, T],
        ignore_exceptions: tuple[Exception, ...] = (),
    ) -> asyncio.Task[T]:
        """Create a new task in the current task group and return it."""
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(
            partial(self._create_task_done_callback, ignore_exceptions)
        )
        return task

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        tasks = self.tasks
        # break reference cycles between tasks and task group
        del self.tasks
        # cancel all tasks
        if self.cancel:
            for task in tasks:
                task.cancel(f"Task group{self.taskgroup_name}cancelled.")
        # wait for all tasks
        if self.wait:
            await asyncio.gather(*tasks, return_exceptions=True)


def to_aiter(*args: T) -> AsyncIterator[T]:
    async def agen():
        for arg in args:
            yield arg

    return agen()


V = TypeVar("V")


class aclosing(Generic[V], AbstractAsyncContextManager[V]):
    """Async context manager for safely finalizing an asynchronously cleaned-up
    resource such as an async generator, calling its ``aclose()`` method.

    Code like this:

        async with aclosing(<module>.fetch(<arguments>)) as agen:
            <block>

    is equivalent to this:

        agen = <module>.fetch(<arguments>)
        try:
            <block>
        finally:
            await agen.aclose()

    """

    def __init__(self, thing: V):
        self.thing = thing

    async def __aenter__(self) -> V:
        return self.thing

    async def __aexit__(self, *exc_info):
        await self.thing.aclose()


async def aclosing_aiter(
    aiterator: AsyncIterator[T],
) -> AsyncIterator[T]:
    if hasattr(aiterator, "__aenter__"):
        async with aiterator:  # type: ignore[invalid-context-manager]
            async for item in aiterator:
                yield item
    else:
        async with aclosing(aiterator):
            async for item in aiterator:
                yield item


class AsyncQueue(Generic[T], asyncio.Queue[T]):
    """Async unbounded FIFO queue with a wait() method.

    Subclassed from asyncio.Queue, adding a wait() method."""

    async def wait(self) -> None:
        """If queue is empty, wait until an item is available.

        Copied from Queue.get(), removing the call to .get_nowait(),
        ie. this doesn't consume the item, just waits for it.
        """
        while self.empty():
            getter = self._get_loop().create_future()
            self._getters.append(getter)
            try:
                await getter
            except:
                getter.cancel()  # Just in case getter is not done yet.
                with suppress(ValueError):
                    self._getters.remove(getter)
                if not self.empty() and not getter.cancelled():
                    # We were woken up by put_nowait(), but can't take
                    # the call.  Wake up the next in line.
                    self._wakeup_next(self._getters)
                raise
