"""Native Crons service — Postgres-backed.

Replicates the Go server's behavior observed on the wire:
- payload is stored as a flat jsonb dict (assistant_id/input/context/config/...),
  with config.configurable.cron_id injected on create;
- next_run_date is computed from the schedule via croniter on create;
- on_run_completed defaults to 'delete'.
The payload<->proto converters mirror langgraph_api/grpc/ops/crons.py exactly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import croniter
import grpc
import orjson
from google.protobuf.empty_pb2 import Empty
from psycopg.types.json import Jsonb

from core_server import db
from core_server._convert import json_bytes, loads, ts
from langgraph_grpc_common.conversion.config import config_from_proto, config_to_proto
from langgraph_grpc_common.proto import core_api_pb2 as pb
from langgraph_grpc_common.proto import enum_cron_on_run_completed_pb2 as cron_orc_pb2
from langgraph_grpc_common.proto import enum_multitask_strategy_pb2 as ms_pb2
from langgraph_grpc_common.proto.core_api_pb2_grpc import CronsServicer

_SORT = {
    pb.CronsSortBy.CRONS_SORT_BY_CRON_ID: "cron_id",
    pb.CronsSortBy.CRONS_SORT_BY_ASSISTANT_ID: "assistant_id",
    pb.CronsSortBy.CRONS_SORT_BY_THREAD_ID: "thread_id",
    pb.CronsSortBy.CRONS_SORT_BY_NEXT_RUN_DATE: "next_run_date",
    pb.CronsSortBy.CRONS_SORT_BY_END_TIME: "end_time",
    pb.CronsSortBy.CRONS_SORT_BY_CREATED_AT: "created_at",
    pb.CronsSortBy.CRONS_SORT_BY_UPDATED_AT: "updated_at",
}


def _interrupt_from_proto(config) -> str | list[str] | None:
    if not config:
        return None
    which = config.WhichOneof("config")
    if which == "all":
        return "*"
    if which == "node_names":
        return list(config.node_names.names)
    return None


def _payload_proto_to_dict(p: pb.CronPayload) -> dict:
    result: dict = {}
    if p.assistant_id:
        result["assistant_id"] = p.assistant_id
    if p.input_json:
        result["input"] = orjson.loads(p.input_json)
    if p.context_json:
        result["context"] = orjson.loads(p.context_json)
    if p.metadata_json:
        result["metadata"] = orjson.loads(p.metadata_json)
    if p.HasField("webhook"):
        result["webhook"] = p.webhook
    if p.HasField("config"):
        result["config"] = dict(config_from_proto(p.config))
    if p.HasField("interrupt_before"):
        result["interrupt_before"] = _interrupt_from_proto(p.interrupt_before)
    if p.HasField("interrupt_after"):
        result["interrupt_after"] = _interrupt_from_proto(p.interrupt_after)
    if p.HasField("multitask_strategy"):
        result["multitask_strategy"] = ms_pb2.MultitaskStrategy.Name(
            p.multitask_strategy,
        )
    for key, val in p.extra_json.items():
        result[key] = orjson.loads(val)
    return result


def _payload_dict_to_proto(payload: dict) -> pb.CronPayload:
    p = pb.CronPayload()
    if "assistant_id" in payload:
        p.assistant_id = str(payload["assistant_id"])
    if payload.get("input") is not None:
        p.input_json = orjson.dumps(payload["input"])
    if payload.get("context") is not None:
        p.context_json = orjson.dumps(payload["context"])
    if payload.get("metadata") is not None:
        p.metadata_json = orjson.dumps(payload["metadata"])
    if payload.get("webhook") is not None:
        p.webhook = payload["webhook"]
    if payload.get("config") is not None:
        pc = config_to_proto(payload["config"])
        if pc is not None:
            p.config.CopyFrom(pc)
    simple = {"assistant_id", "input", "context", "metadata", "webhook", "config"}
    for key, val in payload.items():
        if key not in simple and val is not None:
            p.extra_json[key] = orjson.dumps(val)
    return p


def cron_to_proto(row: dict) -> pb.Cron:
    c = pb.Cron(
        cron_id=pb.UUID(value=str(row["cron_id"])),
        assistant_id=str(row["assistant_id"]) if row.get("assistant_id") else "",
        schedule=row["schedule"],
        enabled=bool(row.get("enabled")),
        metadata_json=json_bytes(row.get("metadata")),
        payload=_payload_dict_to_proto(row.get("payload") or {}),
    )
    if row.get("thread_id"):
        c.thread_id.CopyFrom(pb.UUID(value=str(row["thread_id"])))
    if row.get("on_run_completed"):
        c.on_run_completed = cron_orc_pb2.CronOnRunCompleted.Value(
            row["on_run_completed"],
        )
    if row.get("end_time"):
        c.end_time.CopyFrom(ts(row["end_time"]))
    if row.get("created_at"):
        c.created_at.CopyFrom(ts(row["created_at"]))
    if row.get("updated_at"):
        c.updated_at.CopyFrom(ts(row["updated_at"]))
    if row.get("user_id"):
        c.user_id = row["user_id"]
    if row.get("next_run_date"):
        c.next_run_date.CopyFrom(ts(row["next_run_date"]))
    if row.get("timezone"):
        c.timezone = row["timezone"]
    return c


def _compute_next_run_date(schedule: str, base: datetime) -> datetime:
    return croniter.croniter(schedule, base).get_next(datetime)


class CronsServicerImpl(CronsServicer):
    async def Create(self, request: pb.CreateCronRequest, context) -> pb.Cron:
        cron_id = (
            request.cron_id.value
            if request.HasField("cron_id") and request.cron_id.value
            else str(uuid.uuid4())
        )
        payload = _payload_proto_to_dict(request.payload)
        cfg = payload.get("config") or {}
        cfg.setdefault("configurable", {})
        cfg["configurable"]["cron_id"] = cron_id
        payload["config"] = cfg

        schedule = request.schedule
        now = datetime.now(UTC)
        try:
            next_run_date = _compute_next_run_date(schedule, now)
        except Exception:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Invalid cron schedule: {schedule}",
            )

        orc = (
            cron_orc_pb2.CronOnRunCompleted.Name(request.on_run_completed)
            if request.HasField("on_run_completed")
            else "delete"
        )
        params = {
            "cron_id": cron_id,
            "assistant_id": payload.get("assistant_id"),
            "thread_id": request.thread_id.value
            if request.HasField("thread_id") and request.thread_id.value
            else None,
            "user_id": request.user_id if request.HasField("user_id") else None,
            "payload": Jsonb(payload),
            "schedule": schedule,
            "next_run_date": next_run_date,
            "end_time": request.end_time.ToDatetime(tzinfo=UTC)
            if request.HasField("end_time")
            else None,
            "metadata": Jsonb(loads(request.metadata_json) if request.metadata_json else {}),
            "orc": orc,
            "enabled": request.enabled,
            "timezone": request.timezone if request.HasField("timezone") else None,
        }
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO cron (cron_id, assistant_id, thread_id, user_id, payload,
                                  schedule, next_run_date, end_time, metadata,
                                  on_run_completed, enabled, timezone)
                VALUES (%(cron_id)s, %(assistant_id)s, %(thread_id)s, %(user_id)s, %(payload)s,
                        %(schedule)s, %(next_run_date)s, %(end_time)s, %(metadata)s,
                        %(orc)s, %(enabled)s, %(timezone)s)
                RETURNING *
                """,
                params,
            )
            row = await cur.fetchone()
        return cron_to_proto(row)

    async def Get(self, request: pb.GetCronRequest, context) -> pb.Cron:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM cron WHERE cron_id = %s",
                (request.cron_id.value,),
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Cron {request.cron_id.value} not found",
            )
        return cron_to_proto(row)

    async def Patch(self, request: pb.PatchCronRequest, context) -> pb.Cron:
        sets, params = [], {"cron_id": request.cron_id.value}
        if request.HasField("schedule"):
            sets.append("schedule = %(schedule)s")
            params["schedule"] = request.schedule
        if request.HasField("end_time"):
            sets.append("end_time = %(end_time)s")
            params["end_time"] = request.end_time.ToDatetime(tzinfo=UTC)
        if request.HasField("enabled"):
            sets.append("enabled = %(enabled)s")
            params["enabled"] = request.enabled
        if request.HasField("on_run_completed"):
            sets.append("on_run_completed = %(orc)s")
            params["orc"] = cron_orc_pb2.CronOnRunCompleted.Name(
                request.on_run_completed,
            )
        if request.HasField("timezone"):
            sets.append("timezone = %(timezone)s")
            params["timezone"] = request.timezone
        if request.HasField("payload"):
            pd = _payload_proto_to_dict(request.payload)
            cfg = pd.get("config") or {}
            cfg.setdefault("configurable", {})
            cfg["configurable"]["cron_id"] = request.cron_id.value
            pd["config"] = cfg
            sets.append("payload = %(payload)s")
            params["payload"] = Jsonb(pd)
        if request.HasField("metadata_json"):
            sets.append("metadata = %(metadata)s")
            params["metadata"] = Jsonb(loads(request.metadata_json))
        set_clause = (", " + ", ".join(sets)) if sets else ""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"UPDATE cron SET updated_at = now(){set_clause} "
                "WHERE cron_id = %(cron_id)s RETURNING *",
                params,
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Cron {request.cron_id.value} not found",
            )
        return cron_to_proto(row)

    async def Delete(self, request: pb.DeleteCronRequest, context) -> Empty:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM cron WHERE cron_id = %s",
                (request.cron_id.value,),
            )
        return Empty()

    async def Search(
        self,
        request: pb.SearchCronsRequest,
        context,
    ) -> pb.SearchCronsResponse:
        where, params = [], {}
        if request.HasField("assistant_id") and request.assistant_id.value:
            where.append("assistant_id = %(aid)s")
            params["aid"] = request.assistant_id.value
        if request.HasField("thread_id") and request.thread_id.value:
            where.append("thread_id = %(tid)s")
            params["tid"] = request.thread_id.value
        if request.HasField("enabled"):
            where.append("enabled = %(enabled)s")
            params["enabled"] = request.enabled
        if request.HasField("metadata_json"):
            meta = loads(request.metadata_json)
            if meta:
                where.append("metadata @> %(meta)s")
                params["meta"] = Jsonb(meta)
        params["limit"] = request.limit if request.HasField("limit") else 1000
        params["offset"] = request.offset if request.HasField("offset") else 0
        col = _SORT.get(request.sort_by, "created_at")
        direction = "ASC" if "asc" in pb.SortOrder.Name(request.sort_order).lower() else "DESC"
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"SELECT * FROM cron{clause} ORDER BY {col} {direction} "
                "LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = await cur.fetchall()
        return pb.SearchCronsResponse(crons=[cron_to_proto(r) for r in rows])

    async def Count(self, request: pb.CountCronsRequest, context) -> pb.CountResponse:
        where, params = [], {}
        if request.HasField("assistant_id") and request.assistant_id.value:
            where.append("assistant_id = %(aid)s")
            params["aid"] = request.assistant_id.value
        if request.HasField("thread_id") and request.thread_id.value:
            where.append("thread_id = %(tid)s")
            params["tid"] = request.thread_id.value
        if request.HasField("metadata_json"):
            meta = loads(request.metadata_json)
            if meta:
                where.append("metadata @> %(meta)s")
                params["meta"] = Jsonb(meta)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(f"SELECT count(*) AS n FROM cron{clause}", params)
            n = (await cur.fetchone())["n"]
        return pb.CountResponse(count=n)

    async def Next(self, request: Empty, context) -> pb.NextCronsResponse:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                SELECT *, now() AS _now FROM cron
                WHERE enabled = true
                  AND next_run_date IS NOT NULL
                  AND next_run_date <= now()
                  AND (end_time IS NULL OR end_time > now())
                ORDER BY next_run_date
                """,
            )
            rows = await cur.fetchall()
        resp = pb.NextCronsResponse()
        for row in rows:
            cwn = resp.crons.add()
            cwn.cron.CopyFrom(cron_to_proto(row))
            cwn.now.CopyFrom(ts(row["_now"]))
        return resp

    async def SetNextRunDate(
        self,
        request: pb.SetNextRunDateRequest,
        context,
    ) -> Empty:
        next_run_date = (
            request.next_run_date.ToDatetime(tzinfo=UTC)
            if request.HasField("next_run_date")
            else None
        )
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "UPDATE cron SET next_run_date = %s, updated_at = now() WHERE cron_id = %s",
                (next_run_date, request.cron_id.value),
            )
        return Empty()
