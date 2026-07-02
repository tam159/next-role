import langgraph.version
import structlog
from starlette.responses import JSONResponse, PlainTextResponse

from langgraph_api import __version__, config, metadata
from langgraph_api.feature_flags import IS_POSTGRES_OR_GRPC_BACKEND
from langgraph_api.http_metrics import HTTP_METRICS_COLLECTOR
from langgraph_api.route import ApiRequest
from langgraph_api.schema import PoolStats, PostgresPoolStats, RedisPoolStats
from langgraph_runtime.database import connect, pool_stats
from langgraph_runtime.metrics import get_metrics

if IS_POSTGRES_OR_GRPC_BACKEND:
    from langgraph_api.grpc.ops import Runs
else:
    from langgraph_runtime.ops import Runs

METRICS_FORMATS = {"prometheus", "json"}

logger = structlog.stdlib.get_logger(__name__)


def _merge_pool_stats(local: PoolStats, remote: PoolStats) -> PoolStats:
    """Merge local and remote pool stats by summing numeric values. Used to aggregate Python + Go pool metrics."""
    merged: PoolStats = {}
    if "postgres" in local or "postgres" in remote:
        lp = local.get("postgres") or {}
        rp = remote.get("postgres") or {}
        merged["postgres"] = PostgresPoolStats(
            pool_max=lp.get("pool_max", 0) + rp.get("pool_max", 0),
            pool_size=lp.get("pool_size", 0) + rp.get("pool_size", 0),
            pool_available=lp.get("pool_available", 0) + rp.get("pool_available", 0),
            requests_queued=lp.get("requests_queued", 0) + rp.get("requests_queued", 0),
            requests_errors=lp.get("requests_errors", 0) + rp.get("requests_errors", 0),
        )
    if "redis" in local or "redis" in remote:
        lr = local.get("redis") or {}
        rr = remote.get("redis") or {}
        merged["redis"] = RedisPoolStats(
            idle_connections=lr.get("idle_connections", 0)
            + rr.get("idle_connections", 0),
            in_use_connections=lr.get("in_use_connections", 0)
            + rr.get("in_use_connections", 0),
            max_connections=lr.get("max_connections", 0) + rr.get("max_connections", 0),
        )
    return merged


def _pool_stats_to_prometheus_lines(
    stats: PoolStats,
    project_id: str | None,
    revision_id: str | None,
    deployment_type: str = "",
) -> list[str]:
    """Format merged pool stats as Prometheus text lines (same format as langgraph_runtime.database.pool_stats)."""
    labels = f'project_id="{project_id}", revision_id="{revision_id}", deployment_type="{deployment_type}"'
    lines = []
    if "postgres" in stats:
        pg = stats["postgres"]
        lines.extend(
            [
                "# HELP lg_api_pg_pool_max The maximum size of the postgres connection pool.",
                "# TYPE lg_api_pg_pool_max gauge",
                f"lg_api_pg_pool_max{{{labels}}} {pg.get('pool_max', 0)}",
                "# HELP lg_api_pg_pool_size Number of connections currently managed by the postgres connection pool (in the pool, given to clients, being prepared)",
                "# TYPE lg_api_pg_pool_size gauge",
                f"lg_api_pg_pool_size{{{labels}}} {pg.get('pool_size', 0)}",
                "# HELP lg_api_pg_pool_available Number of connections currently idle in the postgres connection pool",
                "# TYPE lg_api_pg_pool_available gauge",
                f"lg_api_pg_pool_available{{{labels}}} {pg.get('pool_available', 0)}",
                "# HELP lg_api_pg_pool_requests_queued Number of postgres connection requests queued because a postgres connection wasn't immediately available in the pool",
                "# TYPE lg_api_pg_pool_requests_queued counter",
                f"lg_api_pg_pool_requests_queued{{{labels}}} {pg.get('requests_queued', 0)}",
                "# HELP lg_api_pg_pool_requests_errors Number of postgres connection requests resulting in an error (timeouts, queue full...)",
                "# TYPE lg_api_pg_pool_requests_errors counter",
                f"lg_api_pg_pool_requests_errors{{{labels}}} {pg.get('requests_errors', 0)}",
            ]
        )
    if "redis" in stats:
        rd = stats["redis"]
        lines.extend(
            [
                "# HELP lg_api_redis_pool_available Number of connections currently idle in the redis connection pool",
                "# TYPE lg_api_redis_pool_available gauge",
                f"lg_api_redis_pool_available{{{labels}}} {rd.get('idle_connections', 0)}",
                "# HELP lg_api_redis_pool_size Number of connections currently in use in the redis connection pool",
                "# TYPE lg_api_redis_pool_size gauge",
                f"lg_api_redis_pool_size{{{labels}}} {rd.get('in_use_connections', 0)}",
                "# HELP lg_api_redis_pool_max The maximum size of the redis connection pool.",
                "# TYPE lg_api_redis_pool_max gauge",
                f"lg_api_redis_pool_max{{{labels}}} {rd.get('max_connections', 0)}",
            ]
        )
    return lines


async def _grpc_pool_stats() -> PoolStats:
    """Fetch connection pool stats from the Core API (Go) via gRPC for metrics aggregation. Returns {} on error."""
    if not IS_POSTGRES_OR_GRPC_BACKEND:
        return {}
    try:
        return await Runs.pool_stats()
    except Exception as e:
        await logger.awarning(
            "Failed to fetch Core API pool stats for aggregation", exc_info=e
        )
        return {}


async def meta_pool_stats(metrics_format: str) -> PoolStats | list[str]:
    local_pool_stats: PoolStats = pool_stats()

    # Aggregate with Core API (Go) pool stats when using gRPC backend
    grpc_pool_stats = await _grpc_pool_stats()
    merged_pool_stats = _merge_pool_stats(local_pool_stats, grpc_pool_stats)
    if metrics_format == "prometheus":
        return _pool_stats_to_prometheus_lines(
            merged_pool_stats,
            metadata.PROJECT_ID,
            metadata.HOST_REVISION_ID,
            metadata.DEPLOYMENT_TYPE,
        )
    else:
        return merged_pool_stats


async def meta_info(request: ApiRequest):
    return JSONResponse(
        {
            "version": __version__,
            "langgraph_py_version": langgraph.version.__version__,
            "flags": {
                "assistants": True,
                "crons": True,
                "langsmith": bool(config.LANGSMITH_CONTROL_PLANE_API_KEY)
                and bool(config.TRACING),
                "langsmith_tracing_replicas": True,
            },
            "host": {
                "kind": metadata.HOST,
                "project_id": metadata.PROJECT_ID,
                "host_revision_id": metadata.HOST_REVISION_ID,
                "revision_id": metadata.REVISION,
                "tenant_id": metadata.TENANT_ID,
            },
        }
    )


async def meta_metrics(request: ApiRequest):
    # determine output format
    metrics_format = request.query_params.get("format", "prometheus")
    if metrics_format not in METRICS_FORMATS:
        metrics_format = "prometheus"

    # collect stats
    metrics = get_metrics()
    worker_metrics = metrics["workers"]
    workers_max = worker_metrics["max"]
    workers_active = worker_metrics["active"]
    workers_available = worker_metrics["available"]

    http_metrics = HTTP_METRICS_COLLECTOR.get_metrics(
        metadata.PROJECT_ID,
        metadata.HOST_REVISION_ID,
        metrics_format,
        metadata.DEPLOYMENT_TYPE,
    )

    merged_pool_stats = await meta_pool_stats(metrics_format)

    if metrics_format == "json":
        async with connect() as conn:
            resp = {
                **merged_pool_stats,
                "queue": await Runs.stats(conn),
                **http_metrics,
            }
            if config.N_JOBS_PER_WORKER > 0:
                resp["workers"] = worker_metrics
            return JSONResponse(resp)
    elif metrics_format == "prometheus":
        metrics = []
        try:
            async with connect() as conn:
                queue_stats = await Runs.stats(conn)

                labels = f'project_id="{metadata.PROJECT_ID}", revision_id="{metadata.HOST_REVISION_ID}", deployment_type="{metadata.DEPLOYMENT_TYPE}"'
                metrics.extend(
                    [
                        "# HELP lg_api_num_pending_runs The number of runs currently pending.",
                        "# TYPE lg_api_num_pending_runs gauge",
                        f"lg_api_num_pending_runs{{{labels}}} {queue_stats['n_pending']}",
                        "# HELP lg_api_num_running_runs The number of runs currently running.",
                        "# TYPE lg_api_num_running_runs gauge",
                        f"lg_api_num_running_runs{{{labels}}} {queue_stats['n_running']}",
                        "# HELP lg_api_pending_runs_wait_time_max The maximum time a run has been pending, in seconds.",
                        "# TYPE lg_api_pending_runs_wait_time_max gauge",
                        f"lg_api_pending_runs_wait_time_max{{{labels}}} {queue_stats.get('pending_runs_wait_time_max_secs') or 0}",
                        "# HELP lg_api_pending_runs_wait_time_med The median pending wait time across runs, in seconds.",
                        "# TYPE lg_api_pending_runs_wait_time_med gauge",
                        f"lg_api_pending_runs_wait_time_med{{{labels}}} {queue_stats.get('pending_runs_wait_time_med_secs') or 0}",
                        "# HELP lg_api_pending_unblocked_runs_wait_time_max The maximum time a run has been pending excluding runs blocked by another run on the same thread, in seconds.",
                        "# TYPE lg_api_pending_unblocked_runs_wait_time_max gauge",
                        f"lg_api_pending_unblocked_runs_wait_time_max{{{labels}}} {queue_stats.get('pending_unblocked_runs_wait_time_max_secs') or 0}",
                    ]
                )
        except Exception as e:
            await logger.awarning(
                "Ignoring error while getting run stats for /metrics", exc_info=e
            )

        if config.N_JOBS_PER_WORKER > 0:
            worker_labels = f'project_id="{metadata.PROJECT_ID}", revision_id="{metadata.HOST_REVISION_ID}", deployment_type="{metadata.DEPLOYMENT_TYPE}"'
            metrics.extend(
                [
                    "# HELP lg_api_workers_max The maximum number of workers available.",
                    "# TYPE lg_api_workers_max gauge",
                    f"lg_api_workers_max{{{worker_labels}}} {workers_max}",
                    "# HELP lg_api_workers_active The number of currently active workers.",
                    "# TYPE lg_api_workers_active gauge",
                    f"lg_api_workers_active{{{worker_labels}}} {workers_active}",
                    "# HELP lg_api_workers_available The number of available (idle) workers.",
                    "# TYPE lg_api_workers_available gauge",
                    f"lg_api_workers_available{{{worker_labels}}} {workers_available}",
                ]
            )

        metrics.extend(http_metrics)
        metrics.extend(merged_pool_stats)

        metrics_response = "\n".join(metrics)
        return PlainTextResponse(metrics_response)
