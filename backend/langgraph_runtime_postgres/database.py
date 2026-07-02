import asyncio
import os
import re
import threading
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, TypeAlias

import structlog
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import set_json_dumps, set_json_loads
from psycopg_pool import AsyncConnectionPool
from redis.exceptions import LockError, LockNotOwnedError

import langgraph_api.config as config
from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.schema import PoolStats
from langgraph_api.serde import fragment_loads, json_dumpb
from langgraph_runtime_postgres import redis
from langgraph_runtime_postgres.redis import LOCK_MIGRATION

Row: TypeAlias = dict[str, Any]


_CREATE_OR_DROP_INDEX_RE = re.compile(
    r"(?i)(create\s+index\s+concurrently\s*|drop\s+index\s+(?:concurrently\s+)?(?:if\s+exists\s+)?\s*)",
)


def _split_sql_statements(sql: str) -> list[str]:
    "Split SQL so each CREATE INDEX CONCURRENTLY and each DROP INDEX is its own statement; rest stays grouped by delimiter."
    parts = _CREATE_OR_DROP_INDEX_RE.split(sql)
    statements = []
    for i in range(0, len(parts), 2):
        segment = parts[i].strip()
        prefix = parts[i - 1] if i > 0 else ""
        stmt = (prefix + segment).strip()
        if not stmt:
            continue
        statements.append(stmt if stmt.endswith(";") else stmt + ";")
    return statements


logger = structlog.stdlib.get_logger(__name__)

_pg_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
_stats_task: asyncio.Task | None = None


_thread_local = threading.local()


async def healthcheck(*, check_db: bool = True) -> None:
    if not check_db:
        return
    async with connect(supports_core_api=False) as conn, conn.cursor() as cur:
        await cur.execute("SELECT 1")
    await redis.get_redis().ping()


@asynccontextmanager
async def connect(
    *,
    supports_core_api: bool = True,
    __test__: bool = False,
) -> AsyncIterator[AsyncConnection[DictRow]]:
    if supports_core_api and IS_POSTGRES_OR_GRPC_BACKEND:
        yield None
        return
    if __test__:
        async with await create_conn(__test__) as conn:
            yield conn
        return
    if threading.current_thread() is not threading.main_thread():
        if not hasattr(_thread_local, "pg_pool"):
            _thread_local.pg_pool = create_pool(__test__=__test__, thread_local=True)
            await _thread_local.pg_pool.open(wait=True)
            logger.info(
                "Created new thread-local Postgres connection pool",
                thread_name=threading.current_thread().name,
            )
        async with _thread_local.pg_pool.connection() as conn:
            yield conn
        return
    if _pg_pool is None:
        raise RuntimeError("Postgres pool not initialized")
    async with _pg_pool.connection() as conn:
        yield conn


async def _configure_connection(conn: AsyncConnection[DictRow]):
    set_json_dumps(json_dumpb, conn)
    set_json_loads(fragment_loads, conn)


async def _reset_connection(conn: AsyncConnection[DictRow]) -> None:
    with suppress(Exception):
        await conn.rollback()


def create_pool(
    *,
    __test__: bool = False,
    thread_local: bool = False,
) -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    params = conninfo_to_dict(config.DATABASE_URI)
    params.setdefault("options", "")
    if not __test__:
        params["options"] += " -c lock_timeout=1000"
        params["options"] += " -c statement_timeout=900s"
        params["options"] += " -c idle_in_transaction_session_timeout=900s"

    if thread_local:
        pool_min_size = config.CHECKPOINTER_POSTGRES_POOL_MIN_SIZE

        pool_max_size = config.POSTGRES_POOL_MAX_SIZE // config.N_JOBS_PER_WORKER

        pool_max_idle = 30
    else:
        pool_min_size = config.CHECKPOINTER_POSTGRES_POOL_MIN_SIZE

        pool_max_size = config.POSTGRES_POOL_MAX_SIZE

        pool_max_idle = 60

    return AsyncConnectionPool(
        connection_class=AsyncConnection[DictRow],
        min_size=pool_min_size,
        max_size=pool_max_size,
        max_idle=pool_max_idle,
        timeout=config.CHECKPOINTER_POSTGRES_POOL_TIMEOUT_SECONDS,
        kwargs={
            **params,
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        configure=_configure_connection,
        reset=_reset_connection,
        open=False,
    )


async def create_conn(__test__: bool = False) -> AsyncConnection[DictRow]:
    params = conninfo_to_dict(config.DATABASE_URI)
    params.setdefault("options", "")
    if not __test__:
        params["options"] += " -c lock_timeout=1000"
        params["options"] += " -c statement_timeout=900s"
        params["options"] += " -c idle_in_transaction_session_timeout=900s"

    conn = await AsyncConnection.connect(
        config.DATABASE_URI,
        options=params["options"],
        row_factory=dict_row,
        autocommit=True,
        prepare_threshold=0,
    )
    await _configure_connection(conn)
    return conn


async def migrate() -> None:
    async with connect(supports_core_api=False) as conn, conn.cursor() as cur:
        await cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version BIGINT PRIMARY KEY,
                dirty   BOOLEAN NOT NULL
            )
        """,
        )

        await cur.execute(
            "SELECT COALESCE(MAX(version), -1) AS v FROM schema_migrations",
        )

        current_version = (await cur.fetchone())["v"]

        migration_paths = defaultdict(dict)
        for migration_path in sorted(os.listdir(config.MIGRATIONS_PATH)):
            version = int(migration_path.split("_")[0])
            which = migration_path.split(".")[-2]
            if which == "up":
                key = "standard"
            elif which == "lite":
                key = "lite"
            else:
                raise ValueError(f"Unknown migration file: {migration_path}")
            if key in migration_paths[version]:
                raise ValueError(
                    f"Duplicate migration version {version} variant {key!r}: {migration_paths[version][key]} and {migration_path}",
                )
            migration_paths[version][key] = migration_path

        postgres_extensions = config.LANGGRAPH_POSTGRES_EXTENSIONS
        for version, step_options in migration_paths.items():
            if postgres_extensions not in step_options:
                migration = step_options["standard"]
            else:
                migration = step_options[postgres_extensions]
            if version <= current_version:
                continue
            with open(os.path.join(config.MIGRATIONS_PATH, migration)) as f:
                sql = f.read().strip()
            for one in _split_sql_statements(sql):
                try:
                    await cur.execute(one, prepare=False)
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to apply database migration {version}\n\nStatement: {one}",
                    ) from e

            await cur.execute(
                "INSERT INTO schema_migrations (version, dirty) VALUES (%s, %s)",
                (version, False),
            )

            logger.info("Applied database migration", version=version)


def _apply_store_config() -> None:
    """Apply store config (TTL, index, etc.) to the store module.

    This must be called before any Store() instance is created so that
    the thread-local singleton picks up default_ttl and other settings.
    Safe to call outside the migration lock since it only sets module-level state.
    """
    from langgraph_runtime_postgres import store as lg_store

    if not config.STORE_CONFIG:
        return
    lg_store.set_store_config(config.STORE_CONFIG)


async def migrate_vector_index():
    from langgraph_runtime_postgres import store as lg_store

    if not config.STORE_CONFIG:
        return
    logger.info("Setting up vector index", store_config=str(config.STORE_CONFIG))
    await lg_store.setup_vector_index(lg_store.Store())


async def start_pool() -> None:
    global _pg_pool, _stats_task
    await redis.start_redis()

    _pg_pool = create_pool()

    await _pg_pool.open(wait=True)

    _apply_store_config()

    logger.info("Attempting to acquire migration lock")
    try:
        async with redis.get_redis().lock(
            name=LOCK_MIGRATION,
            timeout=60.0,
            blocking_timeout=30.0,
        ):
            await logger.ainfo("Migration lock acquired")

            await migrate()
            await migrate_vector_index()
    except LockError:
        await logger.awarning(
            "Failed to acquire migration lock - another server is already running migrations. Continuing.",
        )
    except LockNotOwnedError as e:
        await logger.awarning("Error releasing migration lock. %s Continuing.", e)
    except Exception as e:
        await logger.aexception("Migration failed", exc_info=e)
        raise
    finally:
        await logger.ainfo("Migration lock released")

    _stats_task = asyncio.create_task(stats_loop())


async def stats_loop() -> None:
    if config.IS_EXECUTOR_ENTRYPOINT:
        return
    _pool = _pg_pool
    if _pool is None:
        raise RuntimeError("Postgres pool not initialized")
    while True:
        logger.info("Postgres pool stats", **_pool.pop_stats())
        await asyncio.sleep(config.STATS_INTERVAL_SECS)


async def stop_pool() -> None:
    global _pg_pool, _stats_task
    if threading.current_thread() is not threading.main_thread():
        if hasattr(_thread_local, "pg_pool"):
            await _thread_local.pg_pool.close()
            del _thread_local.pg_pool
            logger.info(
                "Closed thread-local Postgres connection pool",
                thread_name=threading.current_thread().name,
            )
        return

    if _stats_task is not None:
        _stats_task.cancel("Stopping pool")
        try:
            await _stats_task
        except asyncio.CancelledError:
            pass
        finally:
            _stats_task = None

    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None

    await redis.stop_redis()


def pool_stats() -> PoolStats:
    "Get stats for the main Postgres and Redis pool"
    try:
        return {
            "postgres": _get_pool().get_stats(),
            "redis": redis.redis_stats(),
        }
    except Exception:
        return {}


def _get_pool() -> AsyncConnectionPool[AsyncConnection[DictRow]]:
    if threading.current_thread() is not threading.main_thread():
        return _thread_local.pg_pool
    if _pg_pool is None:
        raise RuntimeError("Postgres pool not initialized")
    return _pg_pool


__all__ = ["connect", "pool_stats", "start_pool", "stop_pool"]
