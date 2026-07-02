"""Native Assistants service — Postgres-backed.

Ported from the reference SQL (risa storage/ops.py) + inmem semantics, adapted
to the live schema created by the Go server (assistant / assistant_versions,
which include `context` and `description` columns).

Assistant reads are scoped to the graphs registered for this deployment
(LANGSERVE_GRAPHS keys), matching the Go server's behavior.
"""

from __future__ import annotations

import functools
import os

import grpc
import orjson
from psycopg.types.json import Jsonb

from core_server import db
from core_server._convert import (
    assistant_to_proto,
    assistant_version_to_proto,
    loads,
)
from langgraph_grpc_common.conversion.config import config_from_proto
from langgraph_grpc_common.proto import core_api_pb2 as pb
from langgraph_grpc_common.proto.core_api_pb2_grpc import AssistantsServicer

_SORT_COLS = ("assistant_id", "graph_id", "name", "created_at", "updated_at")


@functools.cache
def _registered_graphs() -> tuple[str, ...] | None:
    """graph_ids registered for this deployment (LANGSERVE_GRAPHS keys).

    Returns None to disable filtering (env unset / empty / unparseable).
    """
    raw = os.environ.get("LANGSERVE_GRAPHS")
    if not raw:
        return None
    try:
        d = orjson.loads(raw)
    except Exception:
        return None
    return tuple(d.keys()) if d else None


def _if_exists_do_nothing(value: int) -> bool:
    return "nothing" in pb.OnConflictBehavior.Name(value).lower()


def _sort_col(value: int) -> str:
    name = pb.AssistantsSortBy.Name(value).lower()
    for col in _SORT_COLS:
        if name.endswith(col):
            return col
    return "created_at"


def _sort_dir(value: int) -> str:
    return "ASC" if "asc" in pb.SortOrder.Name(value).lower() else "DESC"


def _merge_config_context(config: dict, context: dict) -> tuple[dict, dict]:
    """Keep config['configurable'] and context in sync (inmem semantics)."""
    if config.get("configurable") and context:
        return config, context  # both set; caller validates
    if config.get("configurable"):
        context = config["configurable"]
    elif context:
        config = {**config, "configurable": context}
    return config, context


class AssistantsServicerImpl(AssistantsServicer):
    async def Get(self, request: pb.GetAssistantRequest, context) -> pb.Assistant:
        graphs = _registered_graphs()
        sql = "SELECT * FROM assistant WHERE assistant_id = %s"
        args: list = [request.assistant_id]
        if graphs is not None:
            sql += " AND graph_id = ANY(%s)"
            args.append(list(graphs))
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, args)
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Assistant {request.assistant_id} not found",
            )
        return assistant_to_proto(row)

    async def Create(self, request: pb.CreateAssistantRequest, context) -> pb.Assistant:
        graphs = _registered_graphs()
        if graphs is not None and request.graph_id not in graphs:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"Graph '{request.graph_id}' not found among registered graphs",
            )
        cfg = dict(config_from_proto(request.config)) if request.HasField("config") else {}
        ctx = loads(request.context_json) if request.context_json else {}
        if cfg.get("configurable") and ctx:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Cannot specify both configurable and context.",
            )
        cfg, ctx = _merge_config_context(cfg, ctx)
        meta = loads(request.metadata_json) if request.HasField("metadata_json") else {}
        desc = request.description if request.HasField("description") else None
        params = {
            "aid": request.assistant_id,
            "gid": request.graph_id,
            "config": Jsonb(cfg),
            "context": Jsonb(ctx),
            "metadata": Jsonb(meta),
            "name": request.name,
            "desc": desc,
        }
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                WITH ins_a AS (
                    INSERT INTO assistant (assistant_id, graph_id, config, context, metadata, name, description)
                    VALUES (%(aid)s, %(gid)s, %(config)s, %(context)s, %(metadata)s, %(name)s, %(desc)s)
                    ON CONFLICT (assistant_id) DO NOTHING
                    RETURNING *
                ),
                ins_v AS (
                    INSERT INTO assistant_versions (assistant_id, version, graph_id, config, context, metadata, name, description)
                    SELECT assistant_id, 1, graph_id, config, context, metadata, name, description FROM ins_a
                    ON CONFLICT (assistant_id, version) DO NOTHING
                )
                SELECT * FROM ins_a
                """,
                params,
            )
            row = await cur.fetchone()
            if row is None:
                if not _if_exists_do_nothing(request.if_exists):
                    await context.abort(
                        grpc.StatusCode.ALREADY_EXISTS,
                        f"Assistant {request.assistant_id} already exists",
                    )
                await cur.execute(
                    "SELECT * FROM assistant WHERE assistant_id = %s",
                    (request.assistant_id,),
                )
                row = await cur.fetchone()
        return assistant_to_proto(row)

    async def Patch(self, request: pb.PatchAssistantRequest, context) -> pb.Assistant:
        sets, params = [], {"aid": request.assistant_id}
        if request.HasField("graph_id"):
            sets.append("graph_id = %(gid)s")
            params["gid"] = request.graph_id
        if request.HasField("config"):
            sets.append("config = %(config)s")
            params["config"] = Jsonb(dict(config_from_proto(request.config)))
        if request.HasField("context_json"):
            sets.append("context = %(context)s")
            params["context"] = Jsonb(loads(request.context_json))
        if request.HasField("metadata_json"):
            sets.append("metadata = metadata || %(metadata)s")
            params["metadata"] = Jsonb(loads(request.metadata_json))
        if request.HasField("name"):
            sets.append("name = %(name)s")
            params["name"] = request.name
        if request.HasField("description"):
            sets.append("description = %(desc)s")
            params["desc"] = request.description

        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM assistant_versions WHERE assistant_id = %(aid)s",
                params,
            )
            nv = (await cur.fetchone())["v"]
            params["nv"] = nv
            set_clause = (", " + ", ".join(sets)) if sets else ""
            await cur.execute(
                f"""
                UPDATE assistant
                SET version = %(nv)s, updated_at = now(){set_clause}
                WHERE assistant_id = %(aid)s
                RETURNING *
                """,
                params,
            )
            row = await cur.fetchone()
            if row is None:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"Assistant {request.assistant_id} not found",
                )
            await cur.execute(
                """
                INSERT INTO assistant_versions (assistant_id, version, graph_id, config, context, metadata, name, description)
                VALUES (%(aid)s, %(nv)s, %(gid)s, %(config)s, %(context)s, %(metadata)s, %(name)s, %(desc)s)
                """,
                {
                    "aid": row["assistant_id"],
                    "nv": nv,
                    "gid": row["graph_id"],
                    "config": Jsonb(row["config"]),
                    "context": Jsonb(row.get("context")),
                    "metadata": Jsonb(row["metadata"]),
                    "name": row.get("name"),
                    "desc": row.get("description"),
                },
            )
        return assistant_to_proto(row)

    async def Delete(
        self,
        request: pb.DeleteAssistantRequest,
        context,
    ) -> pb.DeleteAssistantsResponse:
        async with db.pool().connection() as conn, conn.transaction(), conn.cursor() as cur:
            if request.HasField("delete_threads") and request.delete_threads:
                await cur.execute(
                    "DELETE FROM thread WHERE (metadata->>'assistant_id') = %s",
                    (request.assistant_id,),
                )
            await cur.execute(
                "DELETE FROM assistant WHERE assistant_id = %s RETURNING assistant_id",
                (request.assistant_id,),
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Assistant {request.assistant_id} not found",
            )
        return pb.DeleteAssistantsResponse(assistant_ids=[str(row["assistant_id"])])

    async def Search(
        self,
        request: pb.SearchAssistantsRequest,
        context,
    ) -> pb.SearchAssistantsResponse:
        where, params = [], {}
        graphs = _registered_graphs()
        if graphs is not None:
            where.append("graph_id = ANY(%(graphs)s)")
            params["graphs"] = list(graphs)
        if request.HasField("graph_id"):
            where.append("graph_id = %(gid)s")
            params["gid"] = request.graph_id
        if request.HasField("name"):
            where.append("name ILIKE %(name)s")
            params["name"] = f"%{request.name}%"
        if request.HasField("metadata_json"):
            meta = loads(request.metadata_json)
            if meta:
                where.append("metadata @> %(meta)s")
                params["meta"] = Jsonb(meta)
        params["limit"] = request.limit if request.HasField("limit") else 1000
        params["offset"] = request.offset if request.HasField("offset") else 0
        col = _sort_col(request.sort_by) if request.HasField("sort_by") else "created_at"
        direction = _sort_dir(request.sort_order) if request.HasField("sort_order") else "DESC"
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = (
            f"SELECT * FROM assistant{clause} "
            f"ORDER BY {col} {direction} LIMIT %(limit)s OFFSET %(offset)s"
        )
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
        return pb.SearchAssistantsResponse(
            assistants=[assistant_to_proto(r) for r in rows],
        )

    async def SetLatest(
        self,
        request: pb.SetLatestAssistantRequest,
        context,
    ) -> pb.Assistant:
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE assistant a
                SET config = v.config, context = v.context, metadata = v.metadata,
                    version = v.version, name = v.name, description = v.description,
                    updated_at = now()
                FROM assistant_versions v
                WHERE a.assistant_id = v.assistant_id
                  AND a.assistant_id = %(aid)s AND v.version = %(ver)s
                RETURNING a.*
                """,
                {"aid": request.assistant_id, "ver": request.version},
            )
            row = await cur.fetchone()
        if row is None:
            await context.abort(
                grpc.StatusCode.NOT_FOUND,
                f"Assistant {request.assistant_id} version {request.version} not found",
            )
        return assistant_to_proto(row)

    async def GetVersions(
        self,
        request: pb.GetAssistantVersionsRequest,
        context,
    ) -> pb.GetAssistantVersionsResponse:
        where = ["assistant_id = %(aid)s"]
        params = {
            "aid": request.assistant_id,
            "limit": request.limit if request.HasField("limit") else 1000,
            "offset": request.offset if request.HasField("offset") else 0,
        }
        graphs = _registered_graphs()
        if graphs is not None:
            where.append("graph_id = ANY(%(graphs)s)")
            params["graphs"] = list(graphs)
        if request.HasField("metadata_json"):
            meta = loads(request.metadata_json)
            if meta:
                where.append("metadata @> %(meta)s")
                params["meta"] = Jsonb(meta)
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(
                f"SELECT * FROM assistant_versions WHERE {' AND '.join(where)} "
                "ORDER BY version DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = await cur.fetchall()
        return pb.GetAssistantVersionsResponse(
            versions=[assistant_version_to_proto(r) for r in rows],
        )

    async def Count(self, request: pb.CountAssistantsRequest, context) -> pb.CountResponse:
        where, params = [], {}
        graphs = _registered_graphs()
        if graphs is not None:
            where.append("graph_id = ANY(%(graphs)s)")
            params["graphs"] = list(graphs)
        if request.HasField("graph_id"):
            where.append("graph_id = %(gid)s")
            params["gid"] = request.graph_id
        if request.HasField("name"):
            where.append("name ILIKE %(name)s")
            params["name"] = f"%{request.name}%"
        if request.HasField("metadata_json"):
            meta = loads(request.metadata_json)
            if meta:
                where.append("metadata @> %(meta)s")
                params["meta"] = Jsonb(meta)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        async with db.pool().connection() as conn, conn.cursor() as cur:
            await cur.execute(f"SELECT count(*) AS n FROM assistant{clause}", params)
            n = (await cur.fetchone())["n"]
        return pb.CountResponse(count=n)
