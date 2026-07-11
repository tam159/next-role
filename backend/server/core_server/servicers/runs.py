"""Native Runs service — Postgres + Redis backed.

Implemented incrementally:
  step 1 (this file): Run conversion + CRUD/status — Get, Search, Count, Delete,
          SetStatus, Stats, PoolStats.
  step 2: Create (config/kwargs merge + thread upsert + enqueue).
  step 3: Next (SKIP LOCKED dequeue + attempt + BLPOP wait).
  step 4: streaming — Publish, Stream (bidi), Enter, MarkDone, Cancel, Sweep.
Un-overridden methods forward to Go.
"""

from __future__ import annotations

import contextlib
import uuid

import grpc
from google.protobuf.empty_pb2 import Empty
from psycopg.types.json import Jsonb

from server.core_server import db
from server.core_server._convert import json_bytes, loads, ts
from server.core_server._filters import filters_clause, thread_owner_clause
from server.core_server.redis_db import (
    LIST_RUN_QUEUE,
    THREAD_EVENTS_MAXLEN,
    THREAD_EVENTS_TTL_SECS,
    channel_run_control,
    channel_run_stream,
    get_redis,
    stream_thread_events,
    string_run_attempt,
)
from server.grpc_common.conversion.config import config_to_proto
from server.grpc_common.proto import core_api_pb2 as pb
from server.grpc_common.proto import enum_cancel_run_action_pb2 as eca
from server.grpc_common.proto import enum_control_signal_pb2 as ecs
from server.grpc_common.proto import enum_multitask_strategy_pb2 as ms
from server.grpc_common.proto import enum_run_status_pb2 as rs
from server.grpc_common.proto import enum_stream_mode_pb2 as sm
from server.grpc_common.proto.core_api_pb2_grpc import RunsServicer

try:
    from langgraph.checkpoint.base.id import uuid6
except Exception:  # pragma: no cover

    def uuid6() -> uuid.UUID:
        return uuid.uuid4()


RUN_STATUS_TO_PB = {
    "pending": rs.pending,
    "running": rs.running,
    "error": rs.error,
    "success": rs.success,
    "timeout": rs.timeout,
    "interrupted": rs.interrupted,
    "rollback": rs.rollback,
}
RUN_STATUS_FROM_PB = {v: k for k, v in RUN_STATUS_TO_PB.items()}

MULTITASK_TO_PB = {
    "reject": ms.reject,
    "interrupt": ms.interrupt,
    "rollback": ms.rollback,
    "enqueue": ms.enqueue,
}
MULTITASK_FROM_PB = {v: k for k, v in MULTITASK_TO_PB.items()}

STREAM_MODE_TO_PB = {
    "unknown": sm.unknown,
    "values": sm.values,
    "updates": sm.updates,
    "checkpoints": sm.checkpoints,
    "tasks": sm.tasks,
    "debug": sm.debug,
    "messages": sm.messages,
    "custom": sm.custom,
    "events": sm.events,
    "messages-tuple": sm.messages_tuple,
    "tools": sm.tools,
    "lifecycle": sm.lifecycle,
}


def _set_interrupt(field, val) -> None:
    if val is None:
        return
    if val == "*":
        field.all = True
    elif isinstance(val, (list, tuple)):
        field.node_names.names.extend(list(val))


def kwargs_to_proto(d: dict) -> pb.RunKwargs:
    k = pb.RunKwargs()
    if d.get("config") is not None:
        pc = config_to_proto(d["config"])
        if pc is not None:
            k.config.CopyFrom(pc)
    if d.get("input") is not None:
        k.input_json = json_bytes(d["input"])
    if d.get("context") is not None:
        k.context_json = json_bytes(d["context"])
    if d.get("command") is not None:
        k.command_json = json_bytes(d["command"])
    for mode in d.get("stream_mode") or []:
        pm = STREAM_MODE_TO_PB.get(mode)
        if pm is not None:
            k.stream_mode.append(pm)
    if d.get("interrupt_before") is not None:
        _set_interrupt(k.interrupt_before, d["interrupt_before"])
    if d.get("interrupt_after") is not None:
        _set_interrupt(k.interrupt_after, d["interrupt_after"])
    if d.get("webhook") is not None:
        k.webhook = d["webhook"]
    if d.get("feedback_keys"):
        k.feedback_keys.extend(d["feedback_keys"])
    if d.get("temporary") is not None:
        k.temporary = bool(d["temporary"])
    if d.get("subgraphs") is not None:
        k.subgraphs = bool(d["subgraphs"])
    if d.get("resumable") is not None:
        k.resumable = bool(d["resumable"])
    if d.get("checkpoint_during") is not None:
        k.checkpoint_during = bool(d["checkpoint_during"])
    if d.get("durability") is not None:
        k.durability = d["durability"]
    return k


def run_to_proto(row: dict) -> pb.Run:
    r = pb.Run(
        run_id=pb.UUID(value=str(row["run_id"])),
        thread_id=pb.UUID(value=str(row["thread_id"])),
        assistant_id=pb.UUID(value=str(row["assistant_id"])),
        status=RUN_STATUS_TO_PB.get(row.get("status") or "pending", rs.pending),
        metadata=pb.Fragment(value=json_bytes(row.get("metadata"))),
        kwargs=kwargs_to_proto(row.get("kwargs") or {}),
        multitask_strategy=MULTITASK_TO_PB.get(
            row.get("multitask_strategy") or "reject",
            ms.reject,
        ),
    )
    if row.get("created_at"):
        r.created_at.CopyFrom(ts(row["created_at"]))
    if row.get("updated_at"):
        r.updated_at.CopyFrom(ts(row["updated_at"]))
    return r


class RunsServicerImpl(RunsServicer):
    async def Get(self, request: pb.GetRunRequest, context) -> pb.Run:
        params: dict = {"rid": request.run_id.value, "tid": request.thread_id.value}
        toc = thread_owner_clause(request.filters, params, thread_id_expr="run.thread_id")
        cond = f" AND {toc}" if toc else ""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"SELECT * FROM run WHERE run_id = %(rid)s AND thread_id = %(tid)s{cond}",
                params,
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Run not found")
        return run_to_proto(row)

    async def Search(
        self,
        request: pb.SearchRunsRequest,
        context,
    ) -> pb.SearchRunsResponse:
        where, params = ["thread_id = %(tid)s"], {"tid": request.thread_id.value}
        if request.HasField("status"):
            where.append("status = %(status)s")
            params["status"] = RUN_STATUS_FROM_PB.get(request.status, "pending")
        toc = thread_owner_clause(request.filters, params, thread_id_expr="run.thread_id")
        if toc:
            where.append(toc)
        params["limit"] = request.limit if request.HasField("limit") else 10
        params["offset"] = request.offset if request.HasField("offset") else 0
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"SELECT * FROM run WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = await cur.fetchall()
        return pb.SearchRunsResponse(runs=[run_to_proto(r) for r in rows])

    async def Count(self, request: pb.CountRunsRequest, context) -> pb.CountResponse:
        where, params = ["thread_id = %(tid)s"], {"tid": request.thread_id.value}
        if request.statuses:
            where.append("status = ANY(%(statuses)s)")
            params["statuses"] = list(request.statuses)
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"SELECT count(*) AS n FROM run WHERE {' AND '.join(where)}",
                params,
            )
            n = (await cur.fetchone())["n"]
        return pb.CountResponse(count=n)

    async def Delete(self, request: pb.DeleteRunRequest, context) -> pb.UUID:
        params: dict = {"rid": request.run_id.value, "tid": request.thread_id.value}
        toc = thread_owner_clause(request.filters, params, thread_id_expr="run.thread_id")
        cond = f" AND {toc}" if toc else ""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"DELETE FROM run WHERE run_id = %(rid)s AND thread_id = %(tid)s{cond} "
                "RETURNING run_id",
                params,
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Run not found")
        return pb.UUID(value=str(row["run_id"]))

    async def SetStatus(self, request: pb.SetRunStatusRequest, context):
        from google.protobuf.empty_pb2 import Empty

        status = RUN_STATUS_FROM_PB.get(request.status)
        if status is not None:
            async with db.pool().connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE run SET status = %s, updated_at = now() WHERE run_id = %s",
                    (status, request.run_id.value),
                )
        return Empty()

    async def Stats(self, request, context) -> pb.RunStats:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    count(*) FILTER (WHERE status = 'pending') AS n_pending,
                    count(*) FILTER (WHERE status = 'running') AS n_running,
                    max(extract(epoch FROM now() - created_at)) FILTER (WHERE status = 'pending') AS wait_max,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM now() - created_at))
                        FILTER (WHERE status = 'pending') AS wait_med
                FROM run
                """,
            )
            row = await cur.fetchone()
        out = pb.RunStats(n_pending=row["n_pending"] or 0, n_running=row["n_running"] or 0)
        if row["wait_max"] is not None:
            out.pending_runs_wait_time_max_secs = float(row["wait_max"])
        if row["wait_med"] is not None:
            out.pending_runs_wait_time_med_secs = float(row["wait_med"])
        return out

    async def PoolStats(self, request, context) -> pb.ConnectionPoolStats:
        from server.core_server.redis_db import get_redis

        out = pb.ConnectionPoolStats()
        try:
            s = db.pool().get_stats()
            out.postgres.CopyFrom(
                pb.PostgresPoolStats(
                    pool_max=s.get("pool_max", 0),
                    pool_size=s.get("pool_size", 0),
                    pool_available=s.get("pool_available", 0),
                    requests_queued=s.get("requests_waiting", 0),
                    requests_errors=s.get("requests_errors", 0),
                ),
            )
        except Exception:
            pass
        try:
            cp = get_redis().connection_pool
            out.redis.CopyFrom(
                pb.RedisPoolStats(
                    idle_connections=len(getattr(cp, "_available_connections", [])),
                    in_use_connections=len(getattr(cp, "_in_use_connections", [])),
                    max_connections=getattr(cp, "max_connections", 0),
                ),
            )
        except Exception:
            pass
        return out

    async def Create(
        self,
        request: pb.CreateRunRequest,
        context,
    ) -> pb.CreateRunResponse:
        kwargs = loads(request.kwargs_json) if request.kwargs_json else {}
        metadata = loads(request.metadata_json) if request.HasField("metadata_json") else {}
        assistant_id = request.assistant_id.value
        metadata.setdefault("assistant_id", assistant_id)

        run_id = (
            request.run_id.value
            if request.HasField("run_id") and request.run_id.value
            else str(uuid6())
        )
        thread_given = request.HasField("thread_id") and bool(request.thread_id.value)
        thread_id = request.thread_id.value if thread_given else str(uuid.uuid4())
        status = (
            RUN_STATUS_FROM_PB.get(request.status, "pending")
            if request.HasField("status")
            else "pending"
        )
        mts = (
            MULTITASK_FROM_PB.get(request.multitask_strategy, "reject")
            if request.HasField("multitask_strategy")
            else "reject"
        )
        create_thread = (not thread_given) or (
            request.HasField("if_not_exists")
            and request.if_not_exists == pb.CreateRunBehavior.CREATE_THREAD_IF_THREAD_NOT_EXISTS
        )
        prevent = (
            request.HasField("prevent_insert_if_inflight") and request.prevent_insert_if_inflight
        )
        after_seconds = request.after_seconds if request.HasField("after_seconds") else 0

        params = {
            "run_id": uuid.UUID(run_id),
            "thread_id": uuid.UUID(thread_id),
            "assistant_id": uuid.UUID(assistant_id),
            "metadata": Jsonb(metadata),
            "kwargs": Jsonb(kwargs),
            "config": Jsonb(kwargs.get("config") or {}),
            "status": status,
            "multitask_strategy": mts,
            "user_id": request.user_id if request.HasField("user_id") else None,
            "after_seconds": f"{int(after_seconds)} seconds",
        }

        # Auth filters (empty in single-user mode → conditions collapse to ""):
        # thread_filters gate attaching runs to an existing thread the caller
        # doesn't own; assistant_filters gate the assistant join. A filtered-out
        # row means the CTEs produce nothing → no run row → NOT_FOUND upstream.
        tf = filters_clause(request.thread_filters, params, column="thread.metadata")
        tf_cond = f" AND {tf}" if tf else ""
        af = filters_clause(request.assistant_filters, params, column="assistant.metadata")
        af_cond = f" AND {af}" if af else ""

        if create_thread:
            thread_cte = f"""WITH inserted_thread AS (
                INSERT INTO thread (thread_id, status, metadata, config, state_updated_at)
                SELECT %(thread_id)s, 'busy',
                    jsonb_build_object('graph_id', assistant.graph_id, 'assistant_id', assistant.assistant_id) || %(metadata)s::jsonb,
                    assistant.config || %(config)s::jsonb || jsonb_build_object('configurable',
                        coalesce((assistant.config -> 'configurable'), '{{}}'::jsonb) || coalesce(%(config)s::jsonb -> 'configurable', '{{}}'::jsonb)),
                    now()
                FROM assistant WHERE assistant_id = %(assistant_id)s{af_cond}
                ON CONFLICT (thread_id) DO NOTHING
                RETURNING *
            ),
            run_thread AS (
                SELECT * FROM thread WHERE thread_id = %(thread_id)s{tf_cond}
                UNION ALL
                SELECT * FROM inserted_thread
            ),"""
        else:
            thread_cte = (
                "WITH run_thread AS "
                f"(SELECT * FROM thread WHERE thread_id = %(thread_id)s{tf_cond}),"
            )

        query = (
            thread_cte
            + """
            inflight_runs AS (
                SELECT run.* FROM run WHERE thread_id = %(thread_id)s AND run.status = 'pending'
            ),
            inserted_run AS (
                INSERT INTO run (run_id, thread_id, assistant_id, metadata, status, kwargs, multitask_strategy, created_at)
                SELECT
                    %(run_id)s, run_thread.thread_id, assistant.assistant_id, %(metadata)s, %(status)s,
                    %(kwargs)s::jsonb || jsonb_build_object(
                        'config', assistant.config || run_thread.config || %(config)s::jsonb || jsonb_build_object(
                            'configurable',
                                coalesce((assistant.config -> 'configurable'), '{}'::jsonb) ||
                                coalesce((run_thread.config -> 'configurable'), '{}'::jsonb) ||
                                coalesce(%(config)s::jsonb -> 'configurable', '{}'::jsonb) ||
                                jsonb_build_object(
                                    'run_id', %(run_id)s::text,
                                    'thread_id', run_thread.thread_id,
                                    'graph_id', assistant.graph_id,
                                    'assistant_id', assistant.assistant_id,
                                    'user_id', coalesce(
                                        %(config)s::jsonb -> 'configurable' ->> 'user_id',
                                        run_thread.config -> 'configurable' ->> 'user_id',
                                        assistant.config -> 'configurable' ->> 'user_id',
                                        %(user_id)s::text)
                                ),
                            'metadata', assistant.metadata || run_thread.metadata || %(metadata)s
                        )
                    ),
                    %(multitask_strategy)s, now() + %(after_seconds)s::interval
                FROM run_thread CROSS JOIN assistant
                WHERE run_thread.thread_id = %(thread_id)s AND assistant.assistant_id = %(assistant_id)s
            """
            + af_cond
            + (" AND NOT EXISTS (SELECT 1 FROM inflight_runs)" if prevent else "")
            + """
                RETURNING run.*
            ),
            updated_thread AS (
                UPDATE thread SET
                    metadata = jsonb_set(jsonb_set(thread.metadata, '{graph_id}', to_jsonb(assistant.graph_id)), '{assistant_id}', to_jsonb(assistant.assistant_id)),
                    config = assistant.config || thread.config || %(config)s::jsonb || jsonb_build_object('configurable',
                        coalesce((assistant.config -> 'configurable'), '{}'::jsonb) ||
                        coalesce((thread.config -> 'configurable'), '{}'::jsonb) ||
                        coalesce(%(config)s::jsonb -> 'configurable', '{}'::jsonb)),
                    status = 'busy'
                FROM inserted_run INNER JOIN assistant ON assistant.assistant_id = inserted_run.assistant_id
                WHERE thread.thread_id = inserted_run.thread_id AND thread.status != 'busy'
            )
            SELECT * FROM inserted_run
            UNION ALL
            SELECT * FROM inflight_runs
            """
        )

        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()

        if any(r["status"] == "pending" and str(r["run_id"]) == run_id for r in rows):
            try:
                await get_redis().lpush(LIST_RUN_QUEUE, 1)
            except Exception:
                pass

        return pb.CreateRunResponse(runs=[run_to_proto(r) for r in rows])

    async def _claim(self, limit: int) -> list[dict]:
        """Atomically claim up to `limit` pending runs: lock with SKIP LOCKED and
        flip them to 'running' so other workers skip them."""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                WITH claimed AS (
                    SELECT run.run_id
                    FROM run
                    WHERE run.status = 'pending' AND run.created_at < now()
                    ORDER BY run.created_at
                    LIMIT %s
                    FOR NO KEY UPDATE SKIP LOCKED
                )
                UPDATE run SET status = 'running', updated_at = now()
                FROM claimed WHERE run.run_id = claimed.run_id
                RETURNING run.*
                """,
                (limit,),
            )
            return await cur.fetchall()

    async def Next(self, request: pb.NextRunRequest, context) -> pb.NextRunResponse:
        limit = request.limit or 1
        rows = await self._claim(limit)
        if not rows and request.wait:
            # Long-poll: wait for a create wakeup, then try once more.
            try:
                await get_redis().blpop([LIST_RUN_QUEUE], timeout=5)
            except Exception:
                pass
            rows = await self._claim(limit)

        resp = pb.NextRunResponse()
        r = get_redis()
        for row in rows:
            rid = str(row["run_id"])
            try:
                attempt = await r.incrby(string_run_attempt(rid), 1)
                await r.expire(string_run_attempt(rid), 60)
            except Exception:
                attempt = 1
            rwa = resp.runs.add()
            rwa.run.CopyFrom(run_to_proto(row))
            rwa.attempt = int(attempt)
        return resp

    # --- streaming / control (Redis pub/sub) ---------------------------------

    async def _run_finished(self, run_id: str) -> bool:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT status FROM run WHERE run_id = %s", (run_id,))
            row = await cur.fetchone()
        return row is None or row["status"] not in ("pending", "running")

    @staticmethod
    async def _fanout_event(tid: str, rid: str | None, ev: pb.StreamEvent) -> None:
        """Deliver one run event: live pub/sub channel + the durable thread log.

        The channel serves v1 run-scoped subscribers; the XADD backs
        Threads.Stream replay (`last_event_id`) so fresh v2 subscribers —
        including the JS SDK's mid-run stream rotations and history views —
        can catch up instead of seeing live-only events. Best-effort by
        design: a Redis hiccup must not fail the worker's publish path.

        Only **structural** events are logged. Chunked message streams
        (`messages`, `messages|<namespace>`) are live-only: measured on a
        real multi-subagent run they were ~8.1k of 8.2k entries and 95% of
        66 MB, flooding the capped log until the earliest subagents' tool
        events were trimmed — history panes then showed missing/misordered
        activity. Panes hydrate from `tools`/`values`/`lifecycle` events;
        main-chat prose hydrates from thread state over REST.
        """
        r = get_redis()
        # Base type: strip the namespace suffix ("|tools:<id>") and the kind
        # suffix ("/partial", "/complete", ...) — chunked message events are
        # "messages/partial|<ns>" on the wire, not bare "messages".
        base_type = ev.event_type.split("|", 1)[0].split("/", 1)[0]
        if base_type != "messages":
            # Log structural events FIRST and stamp the entry id into the
            # published copy: Threads.Stream subscribes to pub/sub before it
            # replays the log, and drops live structural events whose id is
            # already covered by replay — exact seam dedup instead of
            # duplicates. Chunked message events are live-only and carry no
            # stream_id.
            with contextlib.suppress(Exception):
                key = stream_thread_events(tid)
                entry_id = await r.xadd(
                    key,
                    {"d": ev.SerializeToString()},
                    maxlen=THREAD_EVENTS_MAXLEN,
                    approximate=True,
                )
                await r.expire(key, THREAD_EVENTS_TTL_SECS)
                ev.stream_id = (
                    entry_id.decode() if isinstance(entry_id, (bytes, bytearray)) else entry_id
                )
        with contextlib.suppress(Exception):
            await r.publish(channel_run_stream(tid, rid if rid else "*"), ev.SerializeToString())

    async def Publish(self, request: pb.PublishStreamEventRequest, context) -> Empty:
        tid = request.thread_id.value
        rid = request.run_id.value if request.HasField("run_id") and request.run_id.value else None
        ev = pb.StreamEvent(event_type=request.event_type, message=request.message)
        if rid:
            ev.run_id = rid
        await self._fanout_event(tid, rid, ev)
        return Empty()

    async def MarkDone(self, request: pb.MarkRunDoneRequest, context) -> Empty:
        tid, rid = request.thread_id.value, request.run_id.value
        # run_id travels on the event itself: log readers have no channel name
        # to recover it from (v1 channel readers derive it either way).
        done = pb.StreamEvent(event_type="control", message=b"done", run_id=rid)
        await self._fanout_event(tid, rid, done)
        return Empty()

    async def Stream(self, request_iterator, context):
        r = get_redis()
        pubsub = r.pubsub()
        tid = rid = None
        try:
            async for msg in request_iterator:
                if msg.HasField("subscribe"):
                    tid = msg.subscribe.thread_id.value
                    rid = msg.subscribe.run_id.value
                    await pubsub.subscribe(channel_run_stream(tid, rid))
                    yield pb.StreamEvent(event_type="control", message=b"subscribed")
                elif msg.HasField("join"):
                    break
            if rid is None:
                return
            idle = 0
            while True:
                m = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if m is None:
                    idle += 1
                    if idle >= 3 and await self._run_finished(rid):
                        break  # safety net if the 'done' marker was missed
                    continue
                idle = 0
                data = m.get("data")
                if not isinstance(data, (bytes, bytearray)):
                    continue
                ev = pb.StreamEvent()
                ev.ParseFromString(bytes(data))
                if ev.event_type == "control" and ev.message == b"done":
                    break
                yield ev
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    async def Enter(self, request: pb.EnterRunRequest, context):
        tid, rid = request.thread_id.value, request.run_id.value
        r = get_redis()
        ctrl = channel_run_control(tid, rid)
        pubsub = r.pubsub()
        await pubsub.subscribe(ctrl)
        try:
            existing = None
            with contextlib.suppress(Exception):
                existing = await r.get(ctrl)
            if existing in (b"interrupt", b"rollback"):
                yield pb.ControlEvent(
                    action=ecs.interrupt if existing == b"interrupt" else ecs.rollback,
                )
                return
            while True:
                m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if m is None:
                    continue
                data = m.get("data")
                if data == b"interrupt":
                    yield pb.ControlEvent(action=ecs.interrupt)
                    return
                if data == b"rollback":
                    yield pb.ControlEvent(action=ecs.rollback)
                    return
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    async def Cancel(self, request: pb.CancelRunRequest, context) -> Empty:
        action = (
            "rollback"
            if request.HasField("action") and request.action == eca.rollback
            else "interrupt"
        )
        targets: list[tuple[str, str]] = []  # (thread_id, run_id)
        async with db.pool().connection() as conn, conn.cursor() as cur:
            if request.HasField("run_ids"):
                tid = request.run_ids.thread_id.value
                for u in request.run_ids.run_ids:
                    targets.append((tid, u.value))
            elif request.HasField("status"):
                st = request.status.status
                statuses = {
                    pb.CancelRunStatus.CANCEL_RUN_STATUS_PENDING: ["pending"],
                    pb.CancelRunStatus.CANCEL_RUN_STATUS_RUNNING: ["running"],
                    pb.CancelRunStatus.CANCEL_RUN_STATUS_ALL: ["pending", "running"],
                }.get(st, ["pending", "running"])
                await cur.execute(
                    "SELECT run_id, thread_id FROM run WHERE status = ANY(%s)",
                    (statuses,),
                )
                targets = [(str(x["thread_id"]), str(x["run_id"])) for x in await cur.fetchall()]

            # Ownership gate: drop targets on threads the caller can't see —
            # otherwise any authenticated user could interrupt/rollback
            # another user's runs by id.
            fparams: dict = {}
            fc = filters_clause(request.filters, fparams)
            if fc and targets:
                fparams["tids"] = list({tid for tid, _rid in targets})
                await cur.execute(
                    f"SELECT thread_id FROM thread WHERE thread_id = ANY(%(tids)s) AND {fc}",
                    fparams,
                )
                owned = {str(x["thread_id"]) for x in await cur.fetchall()}
                targets = [(tid, rid) for tid, rid in targets if tid in owned]

            r = get_redis()
            run_ids = [rid for _tid, rid in targets]
            for tid, rid in targets:
                ctrl = channel_run_control(tid, rid)
                with contextlib.suppress(Exception):
                    await r.set(ctrl, action, ex=60)
                    await r.publish(ctrl, action)
            if run_ids:
                if action == "rollback":
                    await cur.execute(
                        "DELETE FROM run WHERE run_id = ANY(%s::uuid[]) AND status = 'pending'",
                        (run_ids,),
                    )
                else:
                    await cur.execute(
                        "UPDATE run SET status = 'interrupted', updated_at = now() "
                        "WHERE run_id = ANY(%s::uuid[]) AND status = 'pending'",
                        (run_ids,),
                    )
        return Empty()

    async def Sweep(self, request, context) -> pb.SweepRunsResponse:
        # No heartbeat-based abandonment tracking in this deployment, and no
        # caller invokes Sweep — nothing to reclaim.
        return pb.SweepRunsResponse()
