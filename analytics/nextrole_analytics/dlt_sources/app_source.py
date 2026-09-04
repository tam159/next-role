"""dlt source over the LangGraph runtime tables → bronze dataset ``raw_app``.

Extraction rules follow the analytics blueprint's sync plan: incremental merge
on ``updated_at`` cursors, and *structure and metrics only* — the shaping in
:mod:`nextrole_analytics.dlt_sources.transforms` drops every payload field
(``run.kwargs`` input, ``thread.values`` bodies, checkpoint channel state,
``store.value`` content) before rows leave the process.

Connections are lazy: nothing touches Postgres at import time. The read-only
DSN comes from ``ANALYTICS_SOURCE_PG_URI`` (the ``analytics_ro`` role created
by the ``analytics-db-init`` compose one-shot).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import dlt
import sqlalchemy as sa
from dlt.sources.sql_database import sql_table

from nextrole_analytics.dlt_sources.transforms import (
    explode_messages,
    shape_checkpoint_meta,
    shape_run,
    shape_thread,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from dlt.common.schema.typing import TColumnSchema
    from dlt.sources import DltResource

_RUN_COLUMNS = (
    "run_id",
    "thread_id",
    "assistant_id",
    "created_at",
    "updated_at",
    "metadata",
    "status",
    "kwargs",
    "multitask_strategy",
)
_THREAD_COLUMNS = (
    "thread_id",
    "created_at",
    "updated_at",
    "metadata",
    "status",
    "values",
    "interrupts",
    "state_updated_at",
)
_ASSISTANT_COLUMNS = (
    "assistant_id",
    "graph_id",
    "name",
    "description",
    "version",
    "created_at",
    "updated_at",
)


def _text_columns(*names: str) -> dict[str, TColumnSchema]:
    """Explicit nullable-text column hints.

    dlt only materializes columns it has *seen values for* — a shaped field
    that happens to be all-NULL in the source (e.g. no run ever set a model
    override, or no session carried an IPv4 address) would otherwise never
    become a warehouse column and downstream dbt models would fail to compile.
    """
    return {name: {"data_type": "text", "nullable": True} for name in names}


def _bigint_columns(*names: str) -> dict[str, TColumnSchema]:
    """Explicit nullable-bigint column hints (same rationale as _text_columns)."""
    return {name: {"data_type": "bigint", "nullable": True} for name in names}


# Hand-written SQL keeps payload columns out of the SELECT entirely (and, for
# store, computes length server-side so the value never crosses the wire).
# `"values"` is a reserved word and must stay quoted.
_MESSAGES_SQL_FULL = (
    "SELECT thread_id, updated_at, \"values\"->'messages' AS messages "
    "FROM public.thread ORDER BY updated_at"
)
_MESSAGES_SQL_INCREMENTAL = (
    "SELECT thread_id, updated_at, \"values\"->'messages' AS messages "
    "FROM public.thread WHERE updated_at >= :cursor ORDER BY updated_at"
)
_CHECKPOINTS_SQL_FULL = (
    "SELECT thread_id, checkpoint_ns, checkpoint_id, run_id, "
    "checkpoint->>'ts' AS ts, metadata FROM public.checkpoints"
)
_CHECKPOINTS_SQL_INCREMENTAL = (
    "SELECT thread_id, checkpoint_ns, checkpoint_id, run_id, "
    "checkpoint->>'ts' AS ts, metadata FROM public.checkpoints "
    "WHERE checkpoint->>'ts' >= :cursor"
)
_STORE_SQL_FULL = (
    "SELECT prefix, key, created_at, updated_at, length(value::text) AS value_length "
    "FROM public.store"
)
_STORE_SQL_INCREMENTAL = (
    "SELECT prefix, key, created_at, updated_at, length(value::text) AS value_length "
    "FROM public.store WHERE updated_at >= :cursor"
)


def pg_uri() -> str:
    """Return the read-only extraction DSN (SQLAlchemy form, ``postgresql+psycopg2://…``)."""
    uri = os.environ.get("ANALYTICS_SOURCE_PG_URI", "")
    if not uri:
        msg = (
            "ANALYTICS_SOURCE_PG_URI is not set — the analytics extraction needs the "
            "read-only Postgres DSN (see docker-compose.yml, analytics-db-init)."
        )
        raise RuntimeError(msg)
    return uri


def _rows(uri: str, sql_full: str, sql_incremental: str, cursor: Any) -> Iterator[dict[str, Any]]:  # noqa: ANN401
    """Stream mapping rows for a full or cursor-bounded query on a short-lived engine."""
    engine = sa.create_engine(uri)
    try:
        stmt = sa.text(sql_incremental if cursor is not None else sql_full)
        params = {"cursor": cursor} if cursor is not None else {}
        with engine.connect() as conn:
            for row in conn.execute(stmt, params).mappings():
                yield dict(row)
    finally:
        engine.dispose()


@dlt.resource(
    name="message",
    primary_key=("thread_id", "message_index"),
    write_disposition="merge",
    columns={
        **_text_columns(
            "message_id",
            "type",
            "name",
            "model_name",
            "model_provider",
            "finish_reason",
            "tool_call_names",
            "tool_call_id",
            "tool_status",
        ),
        **_bigint_columns(
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_creation_tokens",
            "cache_read_tokens",
            "reasoning_tokens",
        ),
    },
)
def _message_resource(
    uri: str,
    thread_updated_at: dlt.sources.incremental[Any] = dlt.sources.incremental("thread_updated_at"),
) -> Iterator[dict[str, Any]]:
    """Explode ``thread.values.messages`` into per-message metric rows (no content)."""
    cursor = thread_updated_at.last_value
    for row in _rows(uri, _MESSAGES_SQL_FULL, _MESSAGES_SQL_INCREMENTAL, cursor):
        for message_row in explode_messages(str(row["thread_id"]), row["messages"]):
            message_row["thread_updated_at"] = row["updated_at"]
            yield message_row


@dlt.resource(
    name="checkpoint_meta",
    primary_key=("thread_id", "checkpoint_ns", "checkpoint_id"),
    write_disposition="merge",
    columns={
        **_text_columns("source", "graph_id", "assistant_id", "owner", "user_id"),
        **_bigint_columns("step"),
        "ts": {"data_type": "timestamp", "nullable": True},
    },
)
def _checkpoint_meta_resource(
    uri: str,
    ts: dlt.sources.incremental[Any] = dlt.sources.incremental("ts"),
) -> Iterator[dict[str, Any]]:
    """Checkpoint step telemetry: whitelisted metadata keys + the checkpoint timestamp."""
    for row in _rows(uri, _CHECKPOINTS_SQL_FULL, _CHECKPOINTS_SQL_INCREMENTAL, ts.last_value):
        yield shape_checkpoint_meta(row)


@dlt.resource(name="store_item", primary_key=("prefix", "key"), write_disposition="merge")
def _store_item_resource(
    uri: str,
    updated_at: dlt.sources.incremental[Any] = dlt.sources.incremental("updated_at"),
) -> Iterator[dict[str, Any]]:
    """Store (long-term memory) inventory: prefix/key/timestamps + value length only."""
    yield from _rows(uri, _STORE_SQL_FULL, _STORE_SQL_INCREMENTAL, updated_at.last_value)


def _table_resource(
    uri: str,
    table: str,
    columns: Sequence[str],
    primary_key: str,
    shaped_columns: dict[str, TColumnSchema] | None = None,
) -> DltResource:
    """Create an incremental-merge ``sql_table`` resource with lazy reflection."""
    resource = sql_table(
        credentials=uri,
        table=table,
        schema="public",
        included_columns=list(columns),
        defer_table_reflect=True,
        incremental=dlt.sources.incremental("updated_at"),
    )
    resource.apply_hints(
        write_disposition="merge",
        primary_key=primary_key,
        columns=shaped_columns,
    )
    return resource


@dlt.source(name="nextrole_app")
def nextrole_app_source() -> Sequence[DltResource]:
    """Build the six runtime resources (incremental merge loads — dedup by primary key)."""
    uri = pg_uri()

    run = _table_resource(
        uri,
        "run",
        _RUN_COLUMNS,
        "run_id",
        shaped_columns=_text_columns("owner", "main_model_override", "subagent_model_override"),
    )
    run.add_map(shape_run)

    thread = _table_resource(
        uri,
        "thread",
        _THREAD_COLUMNS,
        "thread_id",
        shaped_columns=_text_columns("owner", "graph_id", "assistant_id"),
    )
    thread.add_map(shape_thread)

    assistant = _table_resource(
        uri,
        "assistant",
        _ASSISTANT_COLUMNS,
        "assistant_id",
        shaped_columns=_text_columns("name", "description"),
    )

    return [
        run,
        thread,
        assistant,
        _message_resource(uri),
        _checkpoint_meta_resource(uri),
        _store_item_resource(uri),
    ]
