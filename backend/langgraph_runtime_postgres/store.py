import threading
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any, cast

import orjson
import redis.exceptions
import structlog
from langgraph.store.base import Op, Result

# We subclass the OSS AsyncPostgresStore to override setup/batch/sweep behavior
# so it integrates with our own database connection pooling and Redis coordination.
from langgraph.store.postgres.aio import AsyncPostgresStore, PostgresIndexConfig
from langgraph_api.config import StoreConfig, TTLConfig
from langgraph_api.graph import resolve_embeddings
from langgraph_api.serde import json_loads
from psycopg import AsyncCursor, AsyncPipeline
from psycopg.rows import DictRow

from langgraph_runtime_postgres import database
from langgraph_runtime_postgres.redis import (
    LOCK_STORE_SWEEP,
    STRING_STORE_LAST_SWEEP,
    get_redis,
)

logger = structlog.stdlib.get_logger(__name__)

_STORE_CONFIG: StoreConfig = {}


class PGSTore(AsyncPostgresStore):
    """The async store."""

    def __init__(
        self,
        *,
        pipe: AsyncPipeline | None = None,
        deserializer: Callable[[bytes | orjson.Fragment], dict[str, Any]] | None = None,
        index: PostgresIndexConfig | None = None,
        ttl: TTLConfig | None = None,
    ) -> None:
        if index is None:
            index = _STORE_CONFIG.get("index")
        if ttl is None:
            ttl = _STORE_CONFIG.get("ttl")
            if not ttl:
                ttl = {"sweep_interval_minutes": 5}
        super().__init__(
            None, deserializer=json_loads, index=index, ttl=ttl
        )

    @asynccontextmanager
    async def _cursor(
        self, *, pipeline: bool = False
    ) -> AsyncIterator[AsyncCursor[DictRow]]:
        """Create a cursor for the store."""
        async with database.connect(supports_core_api=False) as conn:
            async with conn.cursor(binary=True) as cur:
                yield cur

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        grouped_ops, num_ops = _group_ops(ops)
        results: list[Result] = [None] * num_ops

        async with database.connect(supports_core_api=False) as conn:
            await self._execute_batch(grouped_ops, results, conn)

        return results

    async def setup(self) -> None:
        raise NotImplementedError("Do not use the OSS's setup method.")

    async def sweep_ttl(self) -> int:
        """Sweep expired store items with Redis coordination.

        Uses Redis to coordinate between multiple replicas to prevent
        thundering herd when sweeping expired store items.

        Returns:
            Number of deleted items
        """
        if not self.ttl_config:
            return 0

        sweep_interval_minutes = self.ttl_config.get("sweep_interval_minutes", 5)
        recent_threshold_seconds = sweep_interval_minutes * 60 // 2

        try:
            last_sweep = await get_redis().get(STRING_STORE_LAST_SWEEP)
            if last_sweep:
                await logger.adebug(
                    "Store TTL sweep recently performed by another replica, skipping",
                    last_sweep=last_sweep.decode() if last_sweep else None,
                )
                return 0

            redis_client = get_redis()
            async with redis_client.lock(
                name=LOCK_STORE_SWEEP,
                timeout=120.0,
                blocking_timeout=1.0,
            ):
                last_sweep = await redis_client.get(STRING_STORE_LAST_SWEEP)
                if last_sweep:
                    await logger.adebug(
                        "Store TTL sweep recently performed by another replica, skipping",
                        last_sweep=last_sweep.decode() if last_sweep else None,
                    )
                    return 0

                deleted = await super().sweep_ttl()

                await redis_client.set(
                    STRING_STORE_LAST_SWEEP,
                    "1",
                    ex=recent_threshold_seconds,
                )

                return deleted
        except (redis.exceptions.LockError, redis.exceptions.LockNotOwnedError) as e:
            await logger.adebug("Skipping store sweep; lock not available: %s", e)
            return 0


def set_store_config(config: StoreConfig) -> None:
    global _STORE_CONFIG
    _STORE_CONFIG = config.copy()
    if "index" not in _STORE_CONFIG or not _STORE_CONFIG["index"]:
        return
    _STORE_CONFIG["index"]["embed"] = resolve_embeddings(_STORE_CONFIG["index"])


async def setup_vector_index(store: PGSTore) -> None:
    """Set up the store database asynchronously.

    This method creates the necessary tables in the Postgres database if they don't
    already exist and runs database migrations. It MUST be called directly by the user
    the first time the store is used.
    """

    async def _get_version(cur: AsyncCursor[DictRow], table: str) -> int:
        await cur.execute(
            f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    v INTEGER PRIMARY KEY
                )
            """
        )
        await cur.execute(f"SELECT v FROM {table} ORDER BY v DESC LIMIT 1")
        row = cast(dict, await cur.fetchone())
        return -1 if row is None else row["v"]

    if store.index_config:
        async with store._cursor() as cur:
            version = await _get_version(cur, table="vector_migrations")
            for v, migration in enumerate(
                store.VECTOR_MIGRATIONS[version + 1 :],
                start=version + 1,
            ):
                sql = migration.sql
                if migration.params:
                    params = {
                        k: v(store) if v is not None and callable(v) else v
                        for k, v in migration.params.items()
                    }
                    sql = sql % params
                await cur.execute(sql)
                await cur.execute(
                    "INSERT INTO vector_migrations (v) VALUES (%s)", (v,)
                )
                await logger.ainfo("Applied vector migration", v=v)
            await logger.ainfo("Done applying vector migrations", version=version)
    else:
        await logger.awarning("No vector migrations to apply")


def _group_ops(
    ops: Iterable[Op],
) -> tuple[dict[type, list[tuple[int, Op]]], int]:
    grouped_ops: dict[type, list[tuple[int, Op]]] = defaultdict(list)
    tot = 0
    for idx, op in enumerate(ops):
        grouped_ops[type(op)].append((idx, op))
        tot += 1
    return grouped_ops, tot


_STORE = threading.local()


def start_store() -> None:
    _STORE.store = PGSTore(
        index=_STORE_CONFIG.get("index"),
        ttl=_STORE_CONFIG.get("ttl"),
    )


def Store(*args: Any, **kwargs: Any) -> PGSTore:
    if not hasattr(_STORE, "store"):
        if not _STORE_CONFIG:
            import langgraph_api.config as api_config

            if api_config.STORE_CONFIG:
                set_store_config(api_config.STORE_CONFIG)
        start_store()
    return _STORE.store
