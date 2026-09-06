"""The warehouse's own data dictionary, merged from its three sources.

No single source describes a table completely:

* **dbt** (`manifest.json` from the dbt-docs service) carries the prose written
  for this agent — what a column means, what it excludes, when it misleads.
  `catalog.json` carries types. Its `database` field is a display label
  (`"clickhouse"`), so relations are keyed on `schema` + `alias`.
* **Cube** (`/v1/meta`) carries the metric layer — how a KPI is computed, its
  synonyms and units, example questions, and the caveats attached to each cube.
* **ClickHouse** (`system.tables` / `system.columns`) carries ground truth:
  types, row counts, sort keys, and whatever comments dbt's `persist_docs` has
  written. Its rows are access-filtered, so this only ever sees the two
  databases the agent may read.

Merging them is the one place a wrapper beats raw SQL: a model exploring
`system.columns` alone would see names and types with no meaning attached.

Every fetch degrades rather than fails — a source that is down becomes a notice
in the rendered output, and the remaining sources still answer.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from backend.agents.analytics_agent.settings import (
    CubeSettings,
    DbtDocsSettings,
    WarehouseSettings,
)
from backend.agents.analytics_agent.warehouse import get_client

if TYPE_CHECKING:
    from clickhouse_connect.driver.client import Client

_CONNECT_TIMEOUT = 2.0
_READ_TIMEOUT = 8.0

#: Cube exposes no link from a cube to its underlying table, so each cube YAML
#: carries `meta.table`. This is the fallback for cubes that predate that.
_CUBE_TABLE_FALLBACK = {
    "runs": "fct_run",
    "messages": "fct_message",
    "threads": "fct_thread",
    "sessions": "fct_session",
    "users": "dim_user",
    "assistants": "dim_assistant",
}


@dataclass
class ColumnMeta:
    """One column, as far as every source knows it."""

    name: str
    type: str = ""
    description: str = ""
    pii: str = ""
    comment: str = ""


@dataclass
class TableMeta:
    """One relation in the warehouse, with its columns and Cube metrics."""

    database: str
    name: str
    description: str = ""
    engine: str = ""
    row_count: int | None = None
    sorting_key: str = ""
    columns: list[ColumnMeta] = field(default_factory=list)
    cubes: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        """Fully qualified name, ready to paste into SQL."""
        return f"{self.database}.{self.name}"

    @property
    def grain(self) -> str:
        """First sentence of the description — what one row represents.

        Whitespace is normalized first: dbt doc blocks are hard-wrapped, so a
        sentence often ends at a newline rather than at ". ".
        """
        flat = " ".join(self.description.split())
        return flat.split(". ")[0].strip().rstrip(".")


@dataclass
class MemberMeta:
    """A Cube measure or dimension: the metric layer's vocabulary."""

    name: str
    kind: str
    description: str = ""
    type: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CubeMeta:
    """One cube or view from Cube's `/v1/meta`."""

    name: str
    kind: str
    description: str = ""
    table: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    measures: list[MemberMeta] = field(default_factory=list)
    dimensions: list[MemberMeta] = field(default_factory=list)


@dataclass
class Snapshot:
    """Everything the agent knows about the warehouse at one point in time."""

    tables: dict[str, TableMeta] = field(default_factory=dict)
    cubes: dict[str, CubeMeta] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    fetched_at: float = 0.0


# ---------------------------------------------------------------------------
# fetchers — each returns None (plus a notice) rather than raising
# ---------------------------------------------------------------------------


def _get_json(url: str) -> Any | None:  # noqa: ANN401 — arbitrary JSON documents
    """GET and decode JSON, or return `None` when the service is unreachable."""
    try:
        response = httpx.get(url, timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT))
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def fetch_dbt(settings: DbtDocsSettings) -> tuple[dict, dict] | None:
    """Fetch dbt's `manifest.json` and `catalog.json`, or `None` if unavailable."""
    base = settings.url.rstrip("/")
    manifest = _get_json(f"{base}/manifest.json")
    catalog = _get_json(f"{base}/catalog.json")
    if manifest is None or catalog is None:
        return None
    return manifest, catalog


def fetch_cube(settings: CubeSettings) -> dict | None:
    """Fetch Cube's metadata document, or `None` if unavailable."""
    return _get_json(f"{settings.api_url.rstrip('/')}/cubejs-api/v1/meta")


def fetch_clickhouse(
    databases: tuple[str, ...],
    client: Client | None = None,
) -> tuple[list[dict], list[dict]] | None:
    """Fetch table and column facts for `databases`, or `None` if unavailable.

    ClickHouse filters `system.*` rows by access rights, so this returns only
    what the agent's read-only user may see.
    """
    try:
        conn = client or get_client()
        tables = conn.query(
            "SELECT database, name, engine, total_rows, sorting_key, comment "
            "FROM system.tables WHERE database IN {dbs:Array(String)} ORDER BY database, name",
            parameters={"dbs": list(databases)},
        )
        columns = conn.query(
            "SELECT database, table, name, type, comment "
            "FROM system.columns WHERE database IN {dbs:Array(String)} "
            "ORDER BY database, table, position",
            parameters={"dbs": list(databases)},
        )
    except Exception:
        return None
    to_dicts = lambda result: [  # noqa: E731 — local row-to-dict adapter
        dict(zip(result.column_names, row, strict=True)) for row in result.result_rows
    ]
    return to_dicts(tables), to_dicts(columns)


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _dbt_nodes(manifest: dict, catalog: dict) -> dict[str, dict]:
    """Index dbt descriptions and catalog types by `schema.alias`."""
    catalog_nodes = {**catalog.get("nodes", {}), **catalog.get("sources", {})}
    indexed: dict[str, dict] = {}
    for unique_id, node in {**manifest.get("nodes", {}), **manifest.get("sources", {})}.items():
        schema = node.get("schema")
        alias = node.get("alias") or node.get("name")
        if not schema or not alias:
            continue
        catalog_columns = (catalog_nodes.get(unique_id) or {}).get("columns", {}) or {}
        indexed[f"{schema}.{alias}"] = {
            "description": (node.get("description") or "").strip(),
            "columns": node.get("columns") or {},
            "catalog_columns": catalog_columns,
        }
    return indexed


def _members(raw: list[dict], kind: str, cube_name: str) -> list[MemberMeta]:
    """Convert Cube's member dicts, stripping the redundant cube-name prefix."""
    return [
        MemberMeta(
            name=str(member.get("name", "")).removeprefix(f"{cube_name}."),
            kind=kind,
            description=(member.get("description") or "").strip(),
            type=str(member.get("type") or ""),
            meta=member.get("meta") or {},
        )
        for member in raw
    ]


def _apply_clickhouse(snapshot: Snapshot, clickhouse: tuple[list[dict], list[dict]]) -> None:
    """Seed the snapshot with ground truth: relations, types, counts, sort keys."""
    tables, columns = clickhouse
    for row in tables:
        table = TableMeta(
            database=str(row["database"]),
            name=str(row["name"]),
            engine=str(row.get("engine") or ""),
            row_count=row.get("total_rows"),
            sorting_key=str(row.get("sorting_key") or ""),
            description=str(row.get("comment") or ""),
        )
        snapshot.tables[table.qualified] = table
    for row in columns:
        table = snapshot.tables.get(f"{row['database']}.{row['table']}")
        if table is not None:
            table.columns.append(
                ColumnMeta(
                    name=str(row["name"]),
                    type=str(row.get("type") or ""),
                    comment=str(row.get("comment") or ""),
                ),
            )


def _apply_dbt(snapshot: Snapshot, dbt: tuple[dict, dict]) -> None:
    """Layer dbt's prose over the relations.

    dbt wins over persisted ClickHouse comments: those comments are generated
    from this prose, and this is current even before the next materialization.
    """
    for key, node in _dbt_nodes(*dbt).items():
        table = snapshot.tables.get(key)
        if table is None:
            database, _, name = key.partition(".")
            table = TableMeta(database=database, name=name)
            snapshot.tables[key] = table
        table.description = node["description"] or table.description
        typed = node["catalog_columns"]
        for name, column in node["columns"].items():
            target = next((c for c in table.columns if c.name == name), None)
            if target is None:
                target = ColumnMeta(name=name)
                table.columns.append(target)
            target.description = (column.get("description") or "").strip()
            meta = column.get("meta") or {}
            target.pii = str(meta.get("class") or "") if meta.get("pii") else ""
            if not target.type:
                target.type = str((typed.get(name) or {}).get("type") or "")


def _apply_cube(snapshot: Snapshot, cube: dict, marts_db: str) -> None:
    """Attach the metric layer, linking each cube to the mart it reads."""
    for entry in cube.get("cubes", []):
        name = str(entry.get("name", ""))
        meta = entry.get("meta") or {}
        table_name = str(meta.get("table") or _CUBE_TABLE_FALLBACK.get(name, ""))
        snapshot.cubes[name] = CubeMeta(
            name=name,
            kind=str(entry.get("type") or "cube"),
            description=(entry.get("description") or "").strip(),
            table=table_name,
            meta=meta,
            measures=_members(entry.get("measures") or [], "measure", name),
            dimensions=_members(entry.get("dimensions") or [], "dimension", name),
        )
        table = snapshot.tables.get(f"{marts_db}.{table_name}") if table_name else None
        if table is not None:
            table.cubes.append(name)


def build_snapshot(
    *,
    warehouse: WarehouseSettings | None = None,
    dbt: tuple[dict, dict] | None,
    cube: dict | None,
    clickhouse: tuple[list[dict], list[dict]] | None,
) -> Snapshot:
    """Merge the three sources into one dictionary, noting whatever is missing."""
    warehouse = warehouse or WarehouseSettings()
    snapshot = Snapshot(fetched_at=time.time())

    if clickhouse is None:
        snapshot.notices.append(
            "ClickHouse metadata unavailable — row counts and types are omitted.",
        )
    else:
        _apply_clickhouse(snapshot, clickhouse)

    if dbt is None:
        snapshot.notices.append(
            "dbt docs unreachable — column descriptions are omitted. Restart the "
            "dbt-docs service to restore them.",
        )
    else:
        _apply_dbt(snapshot, dbt)

    if cube is None:
        snapshot.notices.append("Cube unreachable — metric definitions are omitted.")
    else:
        _apply_cube(snapshot, cube, warehouse.marts_db)

    return snapshot


def fetch_snapshot(
    *,
    warehouse: WarehouseSettings | None = None,
    cube_settings: CubeSettings | None = None,
    dbt_settings: DbtDocsSettings | None = None,
) -> Snapshot:
    """Fetch all three sources and merge them."""
    warehouse = warehouse or WarehouseSettings()
    return build_snapshot(
        warehouse=warehouse,
        dbt=fetch_dbt(dbt_settings or DbtDocsSettings()),
        cube=fetch_cube(cube_settings or CubeSettings()),
        clickhouse=fetch_clickhouse((warehouse.marts_db, warehouse.staging_db)),
    )


class SnapshotCache:
    """Serves one merged snapshot, refetching only after its TTL expires.

    Metadata changes at deploy cadence while questions arrive in bursts, so a
    short TTL keeps a multi-step analysis from re-fetching a megabyte of dbt
    artifacts on every step.
    """

    def __init__(self, ttl_s: int | None = None) -> None:
        """Create an empty cache with the configured TTL."""
        self._ttl = ttl_s if ttl_s is not None else DbtDocsSettings().cache_ttl_s
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None

    def get(self, *, refresh: bool = False) -> Snapshot:
        """Return the cached snapshot, refetching when stale or forced."""
        with self._lock:
            fresh = (
                self._snapshot is not None
                and not refresh
                and time.time() - self._snapshot.fetched_at < self._ttl
            )
            if not fresh:
                self._snapshot = fetch_snapshot()
            return self._snapshot


# ---------------------------------------------------------------------------
# rendering — what the model actually reads
# ---------------------------------------------------------------------------


def _cell(text: str) -> str:
    """Flatten prose into one markdown table cell.

    dbt doc blocks are hard-wrapped, and a raw newline would end the table row
    mid-description.
    """
    return " ".join(text.split()).replace("|", "\\|")


def _fmt_rows(count: int | None) -> str:
    """Render a row count, or an em dash when the engine does not track one."""
    return f"{count:,}" if count is not None else "—"


def _bullets(values: Any, limit: int = 3) -> str:  # noqa: ANN401 — free-form meta
    """Join a `meta` list into one line, keeping the first few entries."""
    if isinstance(values, str):
        return values
    if isinstance(values, list):
        return " · ".join(str(v) for v in values[:limit])
    return ""


#: Appended to the overview because this is the last thing the agent reads
#: before writing its first query. The same rules live in the agent's memory and
#: in the `warehouse-analysis` skill; repeating the two that actually fail here
#: costs a few tokens and saves a round trip, since a model that skipped the
#: skill still sees them at the moment it matters.
SQL_REMINDER = (
    "## Before you write SQL",
    "",
    (
        "- Never alias an expression to a name that is already a column: for any "
        "`x`, `sum(x) AS x` makes every other mention of `x` resolve to the alias "
        "and the query fails. Suffix it (`AS total_x`), and check every aliased "
        "aggregate in the select list, not just one."
    ),
    (
        "- `WHERE` filters rows, `HAVING` filters aggregates. If an error says an "
        'aggregate is "found in WHERE", check first whether an alias shadowed '
        "the column — renaming it is usually the fix, not moving to HAVING."
    ),
    "- Percentiles are `quantile(0.95)(col)`. Function names are camelCase.",
    (
        "- Unsure of any other syntax? Read the `warehouse-analysis` skill's "
        "`references/clickhouse-dialect.md` rather than guessing."
    ),
    "",
    "Next: `describe_data('<table>')` for columns and caveats before querying it.",
)


def render_overview(snapshot: Snapshot, warehouse: WarehouseSettings | None = None) -> str:
    """Render the whole-warehouse orientation an agent needs before its first query.

    Deliberately shallow: what exists, what one row of it means, how big it is,
    and which metrics are already defined. Columns come from `render_table`.
    """
    warehouse = warehouse or WarehouseSettings()
    lines = [
        "# NextRole warehouse",
        "",
        (
            "Structure and metrics only — no document bodies. Chat text, CVs, job "
            "descriptions and memories are dropped at extraction, so questions about "
            "what anyone *said* or *wrote* cannot be answered from here."
        ),
        "",
        f"- `{warehouse.marts_db}` — the gold layer. Start here.",
        (
            f"- `{warehouse.staging_db}` — typed, deduplicated entities. "
            "Drill-downs the marts don't cover."
        ),
        "",
        "## Marts",
        "",
        "| table | rows | one row is |",
        "| --- | --- | --- |",
    ]
    marts = [t for _, t in sorted(snapshot.tables.items()) if t.database == warehouse.marts_db]
    lines.extend(f"| `{t.name}` | {_fmt_rows(t.row_count)} | {t.grain or '—'} |" for t in marts)

    staging = [t for t in snapshot.tables.values() if t.database == warehouse.staging_db]
    if staging:
        lines += [
            "",
            "## Staging",
            "",
            ", ".join(f"`{t.name}`" for t in sorted(staging, key=lambda t: t.name)),
        ]

    views = [c for c in snapshot.cubes.values() if c.kind == "view"]
    if views:
        lines += ["", "## Metric views (Cube)", ""]
        for view in sorted(views, key=lambda c: c.name):
            lines.append(f"**{view.name}** — {view.description or 'no description'}")
            questions = _bullets(view.meta.get("example_questions"), limit=3)
            if questions:
                lines.append(f"  Answers: {questions}")

    if snapshot.notices:
        lines += ["", "## Notices", ""] + [f"- {n}" for n in snapshot.notices]

    lines += ["", *SQL_REMINDER]
    return "\n".join(lines)


def resolve_table(snapshot: Snapshot, name: str) -> str | list[str]:
    """Resolve a possibly unqualified table name to one qualified key.

    Returns the key on a unique match, or the list of candidates otherwise, so
    the caller can ask rather than guess.
    """
    wanted = name.strip().strip("`")
    if wanted in snapshot.tables:
        return wanted
    matches = [key for key in snapshot.tables if key.split(".", 1)[1] == wanted]
    return matches[0] if len(matches) == 1 else sorted(matches)


def render_table(snapshot: Snapshot, key: str) -> str:
    """Render one relation's full dictionary: columns, semantics, and metrics."""
    table = snapshot.tables[key]
    lines = [f"# `{table.qualified}`", ""]
    if table.description:
        lines += [table.description, ""]
    facts = [f"rows: {_fmt_rows(table.row_count)}"]
    if table.engine:
        facts.append(f"engine: {table.engine}")
    if table.sorting_key:
        facts.append(f"sort key: `{table.sorting_key}` — filter on this first")
    lines += [" · ".join(facts), "", "## Columns", ""]
    lines += ["| column | type | notes |", "| --- | --- | --- |"]
    for column in table.columns:
        note = column.description or column.comment or ""
        if column.pii:
            note = f"**PII ({column.pii}).** {note}".strip()
        lines.append(f"| `{column.name}` | {column.type or '—'} | {_cell(note)} |")

    for cube_name in table.cubes:
        cube = snapshot.cubes[cube_name]
        lines += ["", f"## Metrics defined on this table (Cube `{cube.name}`)", ""]
        for caveat in cube.meta.get("caveats") or []:
            lines.append(f"> {caveat}")
        if cube.meta.get("caveats"):
            lines.append("")
        lines += ["| measure | means | formula |", "| --- | --- | --- |"]
        lines.extend(
            f"| `{m.name}` | {_cell(m.description)} | {_cell(str(m.meta.get('formula') or ''))} |"
            for m in cube.measures
        )

    return "\n".join(lines)
