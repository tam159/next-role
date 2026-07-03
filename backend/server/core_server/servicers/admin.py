"""Native Admin service — Truncate (test/reset helper)."""

from __future__ import annotations

from google.protobuf.empty_pb2 import Empty

from server.core_server import db
from server.grpc_common.proto import core_api_pb2 as pb
from server.grpc_common.proto.core_api_pb2_grpc import AdminServicer

# Map each TruncateRequest flag to the tables it clears (children first).
_GROUPS = {
    "assistants": ["assistant_versions", "assistant"],
    "threads": ["thread_ttl", "thread"],
    "runs": ["run"],
    "checkpointer": ["checkpoint_writes", "checkpoint_blobs", "checkpoints"],
    "store": ["store"],
}


class AdminServicerImpl(AdminServicer):
    async def Truncate(self, request: pb.TruncateRequest, context) -> Empty:
        tables: list[str] = []
        for flag, group in _GROUPS.items():
            if getattr(request, flag):
                tables.extend(group)
        if tables:
            async with db.pool().connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE",
                )
        return Empty()
