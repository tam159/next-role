"""Async Redis client (redis-py asyncio) + run queue/channel key helpers.

Key formats mirror runtime_postgres/redis.py (default: no cluster).
LIST_RUN_QUEUE is shared with the worker (it lpushes a wakeup sentinel); the
stream/control channels are internal to this server (Python streams via gRPC).
"""

from __future__ import annotations

import os

import redis.asyncio as aioredis

from server.core_server import settings

_redis: aioredis.Redis | None = None

_p = os.environ.get("REDIS_KEY_PREFIX", "")
_PREFIX = f"{_p.rstrip(':')}:" if _p else ""

LIST_RUN_QUEUE = f"{_PREFIX}run:queue"


def channel_run_stream(thread_id, run_id) -> str:
    return f"{_PREFIX}thread:{thread_id}:run:{run_id}:stream"


def channel_run_control(thread_id, run_id) -> str:
    return f"{_PREFIX}thread:{thread_id}:run:{run_id}:control"


def string_run_attempt(run_id) -> str:
    return f"{_PREFIX}run:{run_id}:attempt"


def string_run_running(run_id) -> str:
    return f"{_PREFIX}run:{run_id}:running"


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URI)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
