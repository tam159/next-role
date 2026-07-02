"""gRPC-based cache operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta

from google.protobuf.duration_pb2 import Duration  # type: ignore[import]
from langgraph_grpc_common.proto import core_api_pb2 as pb

from langgraph_api.grpc.client import get_shared_client


async def cache_get(key: str) -> bytes | None:
    """Get a value from the cache.

    Args:
        key: The cache key (must be a valid Redis key suffix).

    Returns:
        The cached value as bytes, or None if not found.
    """
    client = await get_shared_client()
    resp = await client.cache.Get(pb.CacheGetRequest(key=key))
    if resp.HasField("value"):
        return resp.value
    return None


async def cache_set(key: str, value: bytes, ttl: timedelta | None = None) -> None:
    """Set a value in the cache.

    Args:
        key: The cache key.
        value: The value to cache (must be valid serialized JSON).
        ttl: Optional time-to-live.
          (Zero/none => implementation-defined maximum)
    """
    client = await get_shared_client()
    req = pb.CacheSetRequest(key=key, value=value)
    if ttl is not None:
        seconds = int(ttl.total_seconds())
        nanos = int((ttl.total_seconds() - seconds) * 1e9)
        req.ttl.CopyFrom(Duration(seconds=seconds, nanos=nanos))
    await client.cache.Set(req)
