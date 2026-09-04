"""NextRole Superset config (mounted read-only; SUPERSET_CONFIG_PATH points here).

Everything environment-driven: metadata DB on the shared Postgres (database
`superset`, created by analytics-db-init), Celery broker/results and caches on
the shared Redis (DB numbers /2 /3 /4 — the app itself uses the unnumbered
default). Local-dev posture: HTTP, no Talisman.
"""

import os

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = os.environ["SUPERSET_META_DB_URI"]

_REDIS_BROKER = os.environ.get("SUPERSET_REDIS_BROKER", "redis://redis:6379/2")
_REDIS_RESULTS = os.environ.get("SUPERSET_REDIS_RESULTS", "redis://redis:6379/3")
_REDIS_CACHE = os.environ.get("SUPERSET_REDIS_CACHE", "redis://redis:6379/4")


class CeleryConfig:  # noqa: D101
    broker_url = _REDIS_BROKER
    result_backend = _REDIS_RESULTS
    imports = ("superset.sql_lab", "superset.tasks.scheduler", "superset.tasks.thumbnails")
    worker_prefetch_multiplier = 1
    task_acks_late = False


CELERY_CONFIG = CeleryConfig


def _redis_cache(key_prefix: str) -> dict:
    return {
        "CACHE_TYPE": "RedisCache",
        "CACHE_DEFAULT_TIMEOUT": 300,
        "CACHE_KEY_PREFIX": key_prefix,
        "CACHE_REDIS_URL": _REDIS_CACHE,
    }


CACHE_CONFIG = _redis_cache("superset_cache_")
DATA_CACHE_CONFIG = _redis_cache("superset_data_")
FILTER_STATE_CACHE_CONFIG = _redis_cache("superset_filter_")
EXPLORE_FORM_DATA_CACHE_CONFIG = _redis_cache("superset_form_")

# Local dev runs plain HTTP behind no proxy.
TALISMAN_ENABLED = False
