"""Native Cache service — Redis-backed key/value with TTL.

The client treats the key as a Redis key suffix and the value as opaque bytes
(serialized JSON). Both Get and Set go through this server, so we own the
namespace; misses on entries written by the old Go server are harmless.
"""

from __future__ import annotations

from google.protobuf.empty_pb2 import Empty

from server.core_server.redis_db import get_redis
from server.grpc_common.proto import core_api_pb2 as pb
from server.grpc_common.proto.core_api_pb2_grpc import CacheServicer

_PREFIX = "core_server:cache:"


class CacheServicerImpl(CacheServicer):
    async def Get(self, request: pb.CacheGetRequest, context) -> pb.CacheGetResponse:
        val = await get_redis().get(_PREFIX + request.key)
        resp = pb.CacheGetResponse()
        if val is not None:
            resp.value = val
        return resp

    async def Set(self, request: pb.CacheSetRequest, context) -> Empty:
        ttl_ms = None
        if request.HasField("ttl"):
            secs = request.ttl.seconds + request.ttl.nanos / 1e9
            if secs > 0:
                ttl_ms = max(1, int(secs * 1000))
        await get_redis().set(_PREFIX + request.key, request.value, px=ttl_ms)
        return Empty()
