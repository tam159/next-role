"""Shared proto <-> python/DB conversion helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import orjson
from google.protobuf.timestamp_pb2 import Timestamp
from langgraph_grpc_common.conversion.config import config_to_proto
from langgraph_grpc_common.proto import core_api_pb2 as pb
from langgraph_grpc_common.proto import enum_thread_status_pb2 as _ets

THREAD_STATUS_TO_PB = {
    "idle": _ets.idle,
    "busy": _ets.busy,
    "interrupted": _ets.interrupted,
    "error": _ets.error,
}
THREAD_STATUS_FROM_PB = {v: k for k, v in THREAD_STATUS_TO_PB.items()}


def ts(dt: datetime | None) -> Timestamp | None:
    if dt is None:
        return None
    t = Timestamp()
    t.FromDatetime(dt)
    return t


def json_bytes(obj: Any) -> bytes:
    # OPT_SORT_KEYS matches Go's json.Marshal (sorted map keys), giving
    # byte-identical Fragment payloads to the Go server.
    return orjson.dumps(obj if obj is not None else {}, option=orjson.OPT_SORT_KEYS)


def loads(b: bytes | None) -> Any:
    if not b:
        return {}
    return orjson.loads(b)


def assistant_to_proto(row: dict) -> pb.Assistant:
    a = pb.Assistant(
        assistant_id=str(row["assistant_id"]),
        graph_id=row["graph_id"],
        version=row["version"],
        name=row.get("name") or "",
        metadata_json=json_bytes(row.get("metadata")),
    )
    cfg = config_to_proto(row.get("config") or {})
    if cfg is not None:
        a.config.CopyFrom(cfg)
    if row.get("context") is not None:
        a.context_json = json_bytes(row.get("context"))
    if row.get("created_at"):
        a.created_at.CopyFrom(ts(row["created_at"]))
    if row.get("updated_at"):
        a.updated_at.CopyFrom(ts(row["updated_at"]))
    if row.get("description") is not None:
        a.description = row["description"]
    return a


def assistant_version_to_proto(row: dict) -> pb.AssistantVersion:
    v = pb.AssistantVersion(
        assistant_id=str(row["assistant_id"]),
        graph_id=row["graph_id"],
        version=row["version"],
        name=row.get("name") or "",
        metadata_json=json_bytes(row.get("metadata")),
    )
    cfg = config_to_proto(row.get("config") or {})
    if cfg is not None:
        v.config.CopyFrom(cfg)
    if row.get("context") is not None:
        v.context_json = json_bytes(row.get("context"))
    if row.get("created_at"):
        v.created_at.CopyFrom(ts(row["created_at"]))
    if row.get("description") is not None:
        v.description = row["description"]
    return v


def thread_to_proto(row: dict) -> pb.Thread:
    t = pb.Thread(
        thread_id=pb.UUID(value=str(row["thread_id"])),
        status=THREAD_STATUS_TO_PB.get(row.get("status") or "idle", _ets.idle),
        metadata=pb.Fragment(value=json_bytes(row.get("metadata"))),
        config=pb.Fragment(value=json_bytes(row.get("config"))),
    )
    if row.get("created_at"):
        t.created_at.CopyFrom(ts(row["created_at"]))
    if row.get("updated_at"):
        t.updated_at.CopyFrom(ts(row["updated_at"]))
    if row.get("state_updated_at"):
        t.state_updated_at.CopyFrom(ts(row["state_updated_at"]))
    if row.get("values") is not None:
        t.values.CopyFrom(pb.Fragment(value=json_bytes(row["values"])))
    err = row.get("error")
    if err is not None:
        t.error.CopyFrom(pb.Fragment(value=bytes(err)))
    for task_id, items in (row.get("interrupts") or {}).items():
        entry = t.interrupts[task_id]
        for it in items or []:
            ip = entry.interrupts.add()
            if it.get("id") is not None:
                ip.id = it["id"]
            ip.value = json_bytes(it.get("value"))
            if it.get("when") is not None:
                ip.when = it["when"]
            if it.get("resumable") is not None:
                ip.resumable = bool(it["resumable"])
            if it.get("ns"):
                ip.ns.extend(it["ns"])
    return t
