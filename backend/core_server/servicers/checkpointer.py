"""Native Checkpointer service — management methods only.

IMPORTANT: in the Postgres runtime the graph's checkpointer writes/reads
Postgres *directly* (langgraph_runtime_postgres.checkpoint.Checkpointer +
the in-process ingestion loop). The gRPC Checkpointer service's data methods
(Put / PutWrites / GetTuple / List) are used only by the MongoDB backend
(GrpcCheckpointer), so they are left forwarded — they are never called in this
deployment.

The management methods operate on the checkpoint tables directly and ARE
reachable in postgres mode (notably Prune via thread-prune keep_latest), so we
implement them natively for full independence from the Go server.
"""

from __future__ import annotations

import grpc
from google.protobuf.empty_pb2 import Empty

from core_server import db
from langgraph_grpc_common.proto import checkpointer_pb2 as cpb
from langgraph_grpc_common.proto.checkpointer_pb2_grpc import CheckpointerServicer

_CKPT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")

_MONGO_ONLY = (
    "Checkpointer data methods are not used by the Postgres runtime "
    "(checkpoints persist in-process via the direct-PG checkpointer). "
    "This gRPC path is MongoDB-backend only."
)


class CheckpointerServicerImpl(CheckpointerServicer):
    # Data methods: unused in postgres mode (direct-PG checkpointer handles these).
    async def Put(self, request, context):
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, _MONGO_ONLY)

    async def PutWrites(self, request, context):
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, _MONGO_ONLY)

    async def GetTuple(self, request, context):
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, _MONGO_ONLY)

    async def List(self, request, context):
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, _MONGO_ONLY)

    async def GetCapabilities(self, request, context) -> cpb.Capabilities:
        return cpb.Capabilities(
            supports_delete_thread=True,
            supports_prune=True,
            supports_delete_for_runs=True,
            supports_copy_thread=True,
        )

    async def DeleteThread(
        self,
        request: cpb.DeleteThreadRequest,
        context,
    ) -> Empty:
        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            for table in _CKPT_TABLES:
                await cur.execute(
                    f"DELETE FROM {table} WHERE thread_id = %s::uuid",
                    (request.thread_id,),
                )
        return Empty()

    async def DeleteForRuns(
        self,
        request: cpb.DeleteForRunsRequest,
        context,
    ) -> Empty:
        run_ids = list(request.run_ids)
        if run_ids:
            async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM checkpoint_writes cw USING checkpoints c
                    WHERE cw.thread_id = c.thread_id
                      AND cw.checkpoint_ns = c.checkpoint_ns
                      AND cw.checkpoint_id = c.checkpoint_id
                      AND c.run_id = ANY(%s::uuid[])
                    """,
                    (run_ids,),
                )
                await cur.execute(
                    "DELETE FROM checkpoints WHERE run_id = ANY(%s::uuid[])",
                    (run_ids,),
                )
        return Empty()

    async def CopyThread(self, request: cpb.CopyThreadRequest, context) -> Empty:
        src, dst = request.from_thread_id, request.to_thread_id
        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO checkpoints (thread_id, checkpoint_id, run_id, parent_checkpoint_id, checkpoint, metadata, checkpoint_ns)
                SELECT %s::uuid, checkpoint_id, run_id, parent_checkpoint_id, checkpoint,
                       jsonb_set(metadata, '{thread_id}', to_jsonb(%s::text)), checkpoint_ns
                FROM checkpoints WHERE thread_id = %s::uuid
                ON CONFLICT DO NOTHING
                """,
                (dst, dst, src),
            )
            await cur.execute(
                """
                INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)
                SELECT %s::uuid, checkpoint_ns, channel, version, type, blob
                FROM checkpoint_blobs WHERE thread_id = %s::uuid ON CONFLICT DO NOTHING
                """,
                (dst, src),
            )
            await cur.execute(
                """
                INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob)
                SELECT %s::uuid, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob
                FROM checkpoint_writes WHERE thread_id = %s::uuid ON CONFLICT DO NOTHING
                """,
                (dst, src),
            )
        return Empty()

    async def Prune(self, request: cpb.PruneRequest, context) -> Empty:
        thread_ids = list(request.thread_ids)
        if not thread_ids:
            return Empty()
        delete_all = request.strategy == cpb.PruneRequest.PruneStrategy.DELETE_ALL
        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            if delete_all:
                for table in _CKPT_TABLES:
                    await cur.execute(
                        f"DELETE FROM {table} WHERE thread_id = ANY(%s::uuid[])",
                        (thread_ids,),
                    )
            else:  # keep_latest: drop every checkpoint but the newest per thread
                await cur.execute(
                    """
                    WITH latest AS (
                        SELECT thread_id, max(checkpoint_id) AS keep
                        FROM checkpoints WHERE thread_id = ANY(%s::uuid[]) GROUP BY thread_id
                    )
                    DELETE FROM checkpoint_writes cw
                    USING checkpoints c JOIN latest l ON c.thread_id = l.thread_id
                    WHERE cw.thread_id = c.thread_id
                      AND cw.checkpoint_ns = c.checkpoint_ns
                      AND cw.checkpoint_id = c.checkpoint_id
                      AND c.checkpoint_id <> l.keep
                    """,
                    (thread_ids,),
                )
                await cur.execute(
                    """
                    WITH latest AS (
                        SELECT thread_id, max(checkpoint_id) AS keep
                        FROM checkpoints WHERE thread_id = ANY(%s::uuid[]) GROUP BY thread_id
                    )
                    DELETE FROM checkpoints c USING latest l
                    WHERE c.thread_id = l.thread_id AND c.checkpoint_id <> l.keep
                    """,
                    (thread_ids,),
                )
        return Empty()
