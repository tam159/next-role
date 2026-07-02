"""Async Postgres connection pool (psycopg3)."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from core_server import settings

_pool: AsyncConnectionPool | None = None


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            settings.POSTGRES_URI,
            min_size=settings.POOL_MIN,
            max_size=settings.POOL_MAX,
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=False,
        )
        await _pool.open(wait=True)
    return _pool


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("DB pool not opened")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
