"""Native Threads service — Postgres-backed.

Implements the CRUD/read methods (Create, Get, Patch, Delete, Search, Count,
GetGraphID). The run-lifecycle methods (SetStatus, SetJointStatus) and the
streaming method (Stream), plus Copy (checkpoint-table copy), are left to the
fallback for now — they belong with the Runs/Checkpointer phase.
"""

from __future__ import annotations

import contextlib
import uuid

import grpc
import orjson
from google.protobuf.empty_pb2 import Empty
from psycopg.types.json import Jsonb

from core_server import db
from core_server._convert import (
    THREAD_STATUS_FROM_PB,
    loads,
    thread_to_proto,
)
from core_server.redis_db import channel_run_stream, get_redis
from langgraph_grpc_common.proto import core_api_pb2 as pb
from langgraph_grpc_common.proto import enum_thread_stream_mode_pb2 as etsm
from langgraph_grpc_common.proto.core_api_pb2_grpc import ThreadsServicer

_SORT = {
    pb.ThreadsSortBy.THREADS_SORT_BY_THREAD_ID: "thread_id",
    pb.ThreadsSortBy.THREADS_SORT_BY_CREATED_AT: "created_at",
    pb.ThreadsSortBy.THREADS_SORT_BY_UPDATED_AT: "updated_at",
    pb.ThreadsSortBy.THREADS_SORT_BY_STATUS: "status",
    pb.ThreadsSortBy.THREADS_SORT_BY_STATE_UPDATED_AT: "state_updated_at",
}


def _if_exists_do_nothing(value: int) -> bool:
    return "nothing" in pb.OnConflictBehavior.Name(value).lower()


def _sort_dir(value: int) -> str:
    return "ASC" if "asc" in pb.SortOrder.Name(value).lower() else "DESC"


class ThreadsServicerImpl(ThreadsServicer):
    async def Get(self, request: pb.GetThreadRequest, context) -> pb.Thread:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM thread WHERE thread_id = %s",
                (request.thread_id.value,),
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Thread {request.thread_id.value} not found",
            )
        return thread_to_proto(row)

    async def Create(self, request: pb.CreateThreadRequest, context) -> pb.Thread:
        tid = (
            request.thread_id.value
            if request.HasField("thread_id") and request.thread_id.value
            else str(uuid.uuid4())
        )
        meta = loads(request.metadata_json) if request.HasField("metadata_json") else {}
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO thread (thread_id, metadata, status, config, state_updated_at)
                VALUES (%s, %s, 'idle', '{}'::jsonb, now())
                ON CONFLICT (thread_id) DO NOTHING
                RETURNING *
                """,
                (tid, Jsonb(meta)),
            )
            row = await cur.fetchone()
            if row is None:
                if not _if_exists_do_nothing(request.if_exists):
                    await context.abort(
                        grpc.StatusCode.ALREADY_EXISTS,
                        f"Thread {tid} already exists",
                    )
                await cur.execute("SELECT * FROM thread WHERE thread_id = %s", (tid,))
                row = await cur.fetchone()
        return thread_to_proto(row)

    async def Patch(self, request: pb.PatchThreadRequest, context) -> pb.Thread:
        meta = loads(request.metadata_json) if request.HasField("metadata_json") else {}
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE thread SET metadata = metadata || %s, updated_at = now()
                WHERE thread_id = %s RETURNING *
                """,
                (Jsonb(meta), request.thread_id.value),
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Thread {request.thread_id.value} not found",
            )
        return thread_to_proto(row)

    async def Delete(self, request: pb.DeleteThreadRequest, context) -> pb.UUID:
        tid = request.thread_id.value
        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            for table in (
                "checkpoint_writes",
                "checkpoint_blobs",
                "checkpoints",
                "run",
                "cron",
                "thread_ttl",
            ):
                await cur.execute(f"DELETE FROM {table} WHERE thread_id = %s", (tid,))
            await cur.execute(
                "DELETE FROM thread WHERE thread_id = %s RETURNING thread_id",
                (tid,),
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Thread {tid} not found",
            )
        return pb.UUID(value=str(row["thread_id"]))

    async def Search(
        self,
        request: pb.SearchThreadsRequest,
        context,
    ) -> pb.SearchThreadsResponse:
        where, params = [], {}
        if request.HasField("metadata_json"):
            meta = loads(request.metadata_json)
            if meta:
                where.append("metadata @> %(meta)s")
                params["meta"] = Jsonb(meta)
        if request.HasField("values_json"):
            vals = loads(request.values_json)
            if vals:
                where.append("values @> %(vals)s")
                params["vals"] = Jsonb(vals)
        if request.HasField("status"):
            where.append("status = %(status)s")
            params["status"] = THREAD_STATUS_FROM_PB.get(request.status, "idle")
        if request.ids:
            where.append("thread_id = ANY(%(ids)s)")
            params["ids"] = [u.value for u in request.ids]
        params["limit"] = request.limit if request.HasField("limit") else 1000
        params["offset"] = request.offset if request.HasField("offset") else 0
        col = _SORT.get(request.sort_by, "created_at")
        direction = _sort_dir(request.sort_order) if request.HasField("sort_order") else "DESC"
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
            f"SELECT * FROM thread{clause} "
            f"ORDER BY {col} {direction} LIMIT %(limit)s OFFSET %(offset)s"
        )
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return pb.SearchThreadsResponse(threads=[thread_to_proto(r) for r in rows])

    async def Count(self, request: pb.CountThreadsRequest, context) -> pb.CountResponse:
        where, params = [], {}
        if request.HasField("metadata_json"):
            meta = loads(request.metadata_json)
            if meta:
                where.append("metadata @> %(meta)s")
                params["meta"] = Jsonb(meta)
        if request.HasField("values_json"):
            vals = loads(request.values_json)
            if vals:
                where.append("values @> %(vals)s")
                params["vals"] = Jsonb(vals)
        if request.HasField("status"):
            where.append("status = %(status)s")
            params["status"] = THREAD_STATUS_FROM_PB.get(request.status, "idle")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(f"SELECT count(*) AS n FROM thread{clause}", params)
            n = (await cur.fetchone())["n"]
        return pb.CountResponse(count=n)

    async def GetGraphID(
        self,
        request: pb.GetGraphIDRequest,
        context,
    ) -> pb.GetGraphIDResponse:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                SELECT kwargs->'config'->'configurable'->>'graph_id' AS gid
                FROM run
                WHERE thread_id = %s
                  AND kwargs->'config'->'configurable'->>'graph_id' IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (request.thread_id.value,),
            )
            row = await cur.fetchone()
        resp = pb.GetGraphIDResponse()
        if row and row["gid"]:
            resp.graph_id = row["gid"]
        return resp

    async def SetStatus(self, request: pb.SetThreadStatusRequest, context) -> Empty:
        tid = request.thread_id.value
        cp = request.checkpoint if request.HasField("checkpoint") else None
        has_next = bool(cp.next) if cp is not None else False
        exc = (
            request.exception_json
            if request.HasField("exception_json") and request.exception_json
            else None
        )
        base = "error" if exc else ("interrupted" if has_next else "idle")
        interrupts = loads(cp.interrupts_json) if cp is not None and cp.interrupts_json else {}
        params = {"tid": tid, "base": base, "interrupts": Jsonb(interrupts), "error": exc}
        sets = [
            "updated_at = now()",
            "state_updated_at = now()",
            "interrupts = %(interrupts)s",
            "error = %(error)s",
            "status = CASE WHEN EXISTS (SELECT 1 FROM run WHERE thread_id = %(tid)s "
            "AND status IN ('pending','running')) THEN 'busy' ELSE %(base)s END",
        ]
        if cp is not None:
            sets.append("values = %(values)s")
            params["values"] = Jsonb(loads(cp.values_json)) if cp.values_json else None
        where = "thread_id = %(tid)s"
        if request.expected_status:
            expected = [THREAD_STATUS_FROM_PB.get(s) for s in request.expected_status]
            expected = [s for s in expected if s]
            if expected:
                where += " AND status = ANY(%(expected)s)"
                params["expected"] = expected
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"UPDATE thread SET {', '.join(sets)} WHERE {where}",
                params,
            )
        return Empty()

    async def SetJointStatus(
        self,
        request: pb.SetThreadJointStatusRequest,
        context,
    ) -> Empty:
        tid, rid = request.thread_id.value, request.run_id.value
        run_status = request.run_status
        cp = request.checkpoint if request.HasField("checkpoint") else None
        has_next = bool(cp.next) if cp is not None else False
        exc = (
            request.exception_json
            if request.HasField("exception_json") and request.exception_json
            else None
        )
        if exc and run_status not in ("interrupted", "rollback"):
            base = "error"
        elif has_next:
            base = "interrupted"
        else:
            base = "idle"
        interrupts = loads(cp.interrupts_json) if cp is not None and cp.interrupts_json else {}
        params = {
            "tid": tid,
            "rid": rid,
            "graph_id": request.graph_id,
            "base": base,
            "active": run_status in ("pending", "running"),
            "interrupts": Jsonb(interrupts),
            "error": exc,
            "run_status": run_status,
        }
        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            if run_status == "rollback":
                await cur.execute(
                    "DELETE FROM run WHERE run_id = %(rid)s AND thread_id = %(tid)s",
                    params,
                )
            else:
                await cur.execute(
                    "UPDATE run SET status = %(run_status)s, updated_at = now() WHERE run_id = %(rid)s",
                    params,
                )
            sets = [
                "updated_at = now()",
                "state_updated_at = now()",
                "metadata = jsonb_set(metadata, '{graph_id}', to_jsonb(%(graph_id)s::text))",
                "interrupts = %(interrupts)s",
                "error = %(error)s",
                "status = CASE WHEN %(active)s OR EXISTS (SELECT 1 FROM run WHERE "
                "thread_id = %(tid)s AND status IN ('pending','running')) THEN 'busy' ELSE %(base)s END",
            ]
            if cp is not None:
                sets.append("values = %(values)s")
                params["values"] = Jsonb(loads(cp.values_json)) if cp.values_json else None
            await cur.execute(
                f"UPDATE thread SET {', '.join(sets)} WHERE thread_id = %(tid)s",
                params,
            )
        return Empty()

    async def Copy(self, request: pb.CopyThreadRequest, context) -> pb.Thread:
        tid = request.thread_id.value
        new_tid = str(uuid.uuid4())
        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM thread WHERE thread_id = %s", (tid,))
            if await cur.fetchone() is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, f"Thread {tid} not found")
            await cur.execute(
                "INSERT INTO thread (thread_id, metadata, status, config, state_updated_at) "
                "SELECT %s, metadata, 'idle', config, now() FROM thread WHERE thread_id = %s RETURNING *",
                (new_tid, tid),
            )
            new_row = await cur.fetchone()
            await cur.execute(
                """
                INSERT INTO checkpoints (thread_id, checkpoint_id, run_id, parent_checkpoint_id, checkpoint, metadata, checkpoint_ns)
                SELECT %s::uuid, checkpoint_id, run_id, parent_checkpoint_id, checkpoint,
                       jsonb_set(metadata, '{thread_id}', to_jsonb(%s::text)), checkpoint_ns
                FROM checkpoints WHERE thread_id = %s::uuid ON CONFLICT DO NOTHING
                """,
                (new_tid, new_tid, tid),
            )
            await cur.execute(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) "
                "SELECT %s::uuid, checkpoint_ns, channel, version, type, blob FROM checkpoint_blobs "
                "WHERE thread_id = %s::uuid ON CONFLICT DO NOTHING",
                (new_tid, tid),
            )
            await cur.execute(
                "INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob) "
                "SELECT %s::uuid, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob FROM checkpoint_writes "
                "WHERE thread_id = %s::uuid ON CONFLICT DO NOTHING",
                (new_tid, tid),
            )
        return thread_to_proto(new_row)

    async def _thread_has_active_runs(self, thread_id: str) -> bool:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM run WHERE thread_id = %s AND status IN ('pending','running') LIMIT 1",
                (thread_id,),
            )
            return (await cur.fetchone()) is not None

    async def Stream(self, request: pb.StreamThreadRequest, context):
        """Follow a thread's events across its runs (server-streaming).

        Subscribes to every run-stream channel for the thread, converts each
        run's 'done' control marker into a metadata/run_done event (matching the
        reference runtime), applies stream_modes filtering, and ends once the
        thread has no active runs.
        """
        tid = request.thread_id.value
        modes = {etsm.ThreadStreamMode.Name(m) for m in request.stream_modes}

        def should_filter(event_name: str, message: bytes) -> bool:
            if not modes:
                return False
            if "run_modes" in modes and event_name != "state_update":
                return False
            if "state_update" in modes and event_name == "state_update":
                return False
            if "lifecycle" in modes and event_name == "metadata":
                try:
                    d = orjson.loads(message)
                    if d.get("status") == "run_done":
                        return False
                    if "attempt" in d and "run_id" in d:
                        return False
                except (orjson.JSONDecodeError, TypeError):
                    pass
            return True

        pubsub = get_redis().pubsub()
        await pubsub.psubscribe(channel_run_stream(tid, "*"))
        try:
            idle = 0
            while True:
                m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if m is None:
                    if not await self._thread_has_active_runs(tid):
                        idle += 1
                        if idle >= 2:
                            break
                    else:
                        idle = 0
                    continue
                idle = 0
                data = m.get("data")
                if not isinstance(data, (bytes, bytearray)):
                    continue
                ev = pb.StreamEvent()
                ev.ParseFromString(bytes(data))
                event_name = ev.event_type
                payload = ev.message
                run_id = ev.run_id if ev.HasField("run_id") else None
                if event_name == "control" and payload == b"done":
                    ch = m.get("channel")
                    ch = ch.decode() if isinstance(ch, (bytes, bytearray)) else (ch or "")
                    done_run = ch.split("run:")[1].split(":")[0] if "run:" in ch else (run_id or "")
                    event_name = "metadata"
                    payload = orjson.dumps({"status": "run_done", "run_id": done_run})
                    run_id = done_run
                if should_filter(event_name, payload):
                    continue
                out = pb.StreamEvent(event_type=event_name, message=payload)
                if run_id:
                    out.run_id = run_id
                yield out
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
