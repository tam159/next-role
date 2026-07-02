import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

import grpc
import grpc.aio
from psycopg.errors import (
    ConnectionTimeout,
    InternalError,
    OperationalError,
    UndefinedTable,
)
from psycopg_pool.errors import PoolTimeout, TooManyRequests

P = ParamSpec("P")
T = TypeVar("T")


class RetryableException(Exception):
    pass


RETRIABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    OperationalError,
    InternalError,
    RetryableException,
)
OVERLOADED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    PoolTimeout,
    ConnectionTimeout,
    TooManyRequests,
)

_RETRYABLE_STATUS_CODES = frozenset(
    {
        grpc.StatusCode.CANCELLED,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
        grpc.StatusCode.INTERNAL,
    },
)


def retry_db(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    attempts = 3

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        for i in range(attempts):
            if i == attempts - 1:
                return await func(*args, **kwargs)
            try:
                return await func(*args, **kwargs)
            except grpc.aio.AioRpcError as e:
                if e.code() in _RETRYABLE_STATUS_CODES:
                    await asyncio.sleep(0.01)
                else:
                    raise
            except UndefinedTable:
                await asyncio.sleep(5)
            except RETRIABLE_EXCEPTIONS:
                await asyncio.sleep(0.01)

    return wrapper
