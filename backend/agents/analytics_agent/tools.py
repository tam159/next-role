"""The analytics agent's three tools.

Each is a factory closing over the backend, mirroring `career_agent/tools.py`:
built once at graph assembly so the closure is not duplicated per call. The
docstrings are the tool descriptions the model reads, so they say when to use
the tool, when not to, and what an error looks like.

Nothing raises. Every failure is returned as an `Error: ...` string the model
can read and act on, which is the same contract the career agent's tools keep.
"""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING, Any, Literal, Protocol

from backend.agents.analytics_agent.charts import (
    ALL_KINDS,
    FIGURE_KINDS,
    PUBLISH_ONLY_KINDS,
    ChartData,
    ChartSpec,
    build_envelope,
    chart_path,
    slugify,
    validate_mapping,
)
from backend.agents.analytics_agent.metadata import (
    Snapshot,
    SnapshotCache,
    render_overview,
    render_table,
    resolve_table,
)
from backend.agents.analytics_agent.scratch import ScratchError, open_scratch, safe_segment
from backend.agents.analytics_agent.settings import WarehouseSettings
from backend.agents.analytics_agent.warehouse import QueryOutcome, run_readonly
from backend.agents.analytics_agent.warehouse import render_table as render_rows
from backend.agents.career_agent.scope import current_thread_id
from langchain.tools import tool

if TYPE_CHECKING:
    from deepagents.backends import CompositeBackend
    from langchain_core.tools import BaseTool


class MetadataCache(Protocol):
    """Serves a merged metadata snapshot (structural, so fakes qualify)."""

    def get(self, *, refresh: bool = False) -> Snapshot:
        """Return the current snapshot, refetching when asked."""
        ...


def _thread_id() -> str:
    """Return the active thread id, or a shared bucket when outside a run."""
    return safe_segment(current_thread_id(), fallback="adhoc")


def make_describe_data(cache: MetadataCache | None = None) -> BaseTool:
    """Build the `describe_data` tool over a shared metadata cache."""
    catalog = cache or SnapshotCache()

    @tool
    def describe_data(table: str | None = None, refresh: bool = False) -> str:  # noqa: FBT001, FBT002
        """Describe the warehouse: what tables exist and what their columns mean.

        Call this before writing your first query of a conversation, and again
        for any table you have not yet inspected. The warehouse's column
        semantics are not guessable from names alone — `owner` has a special
        `'default'` value, `interrupted` runs are not failures, and estimated
        cost is a list-price floor, all of which change what a number means.

        With no argument: the layer map, every mart with its row count and what
        one row represents, and the metric views already defined.

        With a table name (`"fct_run"` or `"nextrole_marts.fct_run"`): every
        column with its type, meaning and PII class, the sort key to filter on,
        and the metrics Cube defines over that table.

        Args:
            table: Table to describe, qualified or bare. Omit for the overview.
            refresh: Refetch the metadata instead of using the cached snapshot.
                Use after a pipeline change; the cache is ~10 minutes otherwise.

        Returns:
            Markdown. On an unknown or ambiguous name, an `Error: ...` line
            listing the candidates.

        """
        snapshot = catalog.get(refresh=refresh)
        if table is None:
            return render_overview(snapshot)
        resolved = resolve_table(snapshot, table)
        if isinstance(resolved, str):
            return render_table(snapshot, resolved)
        if resolved:
            candidates = ", ".join(f"`{c}`" for c in resolved)
            return f"Error: '{table}' is ambiguous. Qualify it: {candidates}."
        return (
            f"Error: no table named '{table}'. Call describe_data() with no "
            f"argument to see what exists."
        )

    return describe_data


def _to_csv(outcome: QueryOutcome) -> str:
    """Render a full result as CSV for the Python hand-off."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(outcome.columns)
    writer.writerows(outcome.rows)
    return buffer.getvalue()


def make_run_sql(backend: CompositeBackend, settings: WarehouseSettings | None = None) -> BaseTool:
    """Build the `run_sql` tool over the warehouse and the thread's scratch dir."""
    config = settings or WarehouseSettings()

    @tool
    def run_sql(sql: str, save_as: str | None = None) -> str:
        """Run one read-only SQL query against the ClickHouse warehouse.

        ClickHouse dialect. Only the marts and staging databases are readable,
        and only for reading — the connection has no write privileges at all.
        Always qualify table names (`nextrole_marts.fct_run`) and filter on the
        table's sort key, which `describe_data` reports.

        Results are capped for display. When a query has no LIMIT, one is added
        and the result says so, so a partial answer is never mistaken for a
        complete one.

        Use `save_as` only when a chart or analysis needs more rows than are
        worth reading: it writes the full result to a file for a Python script
        run through `execute`, and returns the path. Do not use it to "look at"
        data — read the table this tool prints instead.

        Args:
            sql: One SELECT (or WITH / DESCRIBE / SHOW / EXPLAIN) statement.
            save_as: Optional file name, e.g. `"daily_cost.csv"`, to export the
                full result for a Python script.

        Returns:
            A summary line, then a markdown table. On failure, an
            `Error: ...` line with the reason and how to recover.

        """
        display_limit = None if save_as else config.display_rows
        outcome = run_readonly(sql, display_limit=display_limit, settings=config)
        if isinstance(outcome, str):
            return outcome

        rendered = render_rows(outcome, max_rows=config.display_rows)
        if not save_as:
            return rendered

        name = safe_segment(save_as, fallback="result.csv")
        try:
            scratch = open_scratch(backend.default, _thread_id())
            path = scratch.write_text(name, _to_csv(outcome))
        except (ScratchError, OSError) as exc:
            return f"{rendered}\n\nError: the result could not be saved: {exc}"
        return f"{rendered}\n\nFull result ({outcome.row_count} rows) saved to `{path}`."

    return run_sql


def _load_figure(backend: CompositeBackend, figure_path: str) -> dict[str, Any] | str:
    """Read a figure JSON a fallback script wrote, or return an `Error: ...`."""
    try:
        scratch = open_scratch(backend.default, _thread_id())
        raw = scratch.read_text(figure_path)
    except (ScratchError, OSError) as exc:
        return f"Error: could not read {figure_path}: {exc}"
    if raw is None:
        return (
            f"Error: no file at {figure_path}. Write the figure there first, e.g. "
            f"`fig.write_json(path)` inside the script you run with execute."
        )
    try:
        figure = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"Error: {figure_path} is not valid JSON ({exc.msg})."
    if not isinstance(figure, dict) or "data" not in figure:
        return (
            f"Error: {figure_path} is not a Plotly figure — expected an object "
            f"with a `data` key. Use `fig.write_json(path)`."
        )
    return figure


def _chart_rows(
    sql: str,
    config: WarehouseSettings,
) -> ChartData | str:
    """Run the chart's query, or return an `Error: ...` explaining why not."""
    outcome = run_readonly(sql, display_limit=None, settings=config)
    if isinstance(outcome, str):
        return outcome
    if outcome.row_count > config.chart_max_rows:
        return (
            f"Error: {outcome.row_count:,} rows is too many to plot (limit "
            f"{config.chart_max_rows:,}). Aggregate — group by day or week, or "
            f"filter to a narrower window."
        )
    if not outcome.rows:
        return (
            "Error: the query returned no rows, so there is nothing to chart. "
            "Widen the filters or check the date range."
        )
    return ChartData(columns=outcome.columns, rows=outcome.rows)


def _prepare_chart(
    spec: ChartSpec,
    *,
    sql: str | None,
    figure_path: str | None,
    backend: CompositeBackend,
    config: WarehouseSettings,
) -> tuple[ChartData, dict[str, Any] | None] | str:
    """Gather the rows and/or figure a chart needs, or explain what is wrong.

    A pre-built figure skips mapping validation: the script that produced it
    already decided the shape, and its rows may not be in the query result.
    """
    if spec.kind in PUBLISH_ONLY_KINDS and not figure_path:
        return (
            f"Error: a {spec.kind} chart cannot be built from SQL here. Write the "
            f"figure with a script and publish it with `figure_path` — see the "
            f"`charts` skill."
        )

    prebuilt: dict[str, Any] | None = None
    if figure_path:
        if spec.kind not in FIGURE_KINDS | PUBLISH_ONLY_KINDS:
            return (
                f"Error: `figure_path` publishes a Plotly figure, so `kind` "
                f"cannot be '{spec.kind}'."
            )
        loaded = _load_figure(backend, figure_path)
        if isinstance(loaded, str):
            return loaded
        prebuilt = loaded

    data = ChartData(columns=[], rows=[])
    if sql:
        rows = _chart_rows(sql, config)
        if isinstance(rows, str):
            return rows
        data = rows

    if prebuilt is None:
        mapping_error = validate_mapping(spec, data)
        if mapping_error:
            return mapping_error
    return data, prebuilt


def make_create_chart(
    backend: CompositeBackend,
    settings: WarehouseSettings | None = None,
) -> BaseTool:
    """Build the `create_chart` tool over the warehouse and artifact storage."""
    config = settings or WarehouseSettings()

    @tool
    def create_chart(  # noqa: PLR0913 — the chart's column mapping is the API
        kind: Literal[
            "line",
            "bar",
            "area",
            "scatter",
            "pie",
            "histogram",
            "heatmap",
            "table",
            "kpi",
            "box",
            "violin",
            "contour",
            "histogram2d",
        ],
        title: str,
        *,
        sql: str | None = None,
        x: str | None = None,
        y: str | list[str] | None = None,
        series: str | None = None,
        z: str | None = None,
        description: str | None = None,
        slug: str | None = None,
        stacked: bool = False,
        y_format: Literal["number", "percent", "currency", "duration_s"] = "number",
        figure_path: str | None = None,
    ) -> str:
        """Draw a chart from a query and save it to the conversation.

        This is the normal way to visualise an answer: pass the SQL and say
        which columns are which. The chart is built here and stored, so it
        renders in the conversation and is still there when the thread is
        reopened later. Prefer it over writing plotting code.

        Choosing a kind: `line` or `area` for a trend over time, `bar` to
        compare categories, `pie` for shares of a whole (at most about six
        slices), `histogram` for a distribution, `heatmap` for two dimensions
        against one value, `table` when the exact figures matter more than the
        shape, and `kpi` for a single headline number.

        For a visual this tool cannot express, write a Plotly figure to the
        thread's scratch directory with a Python script and pass `figure_path`
        instead of `sql` — the figure is wrapped and stored the same way.

        Args:
            kind: Which chart to draw. `box`, `violin`, `contour` and
                `histogram2d` cannot be built from SQL here — write those with a
                script and pass `figure_path`.
            title: Short title shown above the chart.
            sql: Query producing the rows. Omit only with `figure_path`.
            x: Column for the x axis; the labels for a pie; the column axis of
                a heatmap; the label for a kpi.
            y: Column(s) holding the values. A list draws one series each.
            series: Column whose distinct values split `y` into one series each.
            z: Reserved for future kinds; unused today.
            description: One line of context shown under the title, e.g. the
                caveat that applies to the number.
            slug: File name stem. Defaults to a slug of the title; pass the same
                slug again to replace an existing chart.
            stacked: Stack bars or areas instead of grouping them.
            y_format: How to format values: plain numbers, a percentage, US
                dollars, or seconds.
            figure_path: Path to a Plotly figure JSON written by a script,
                instead of `sql`.

        Returns:
            A JSON object with the saved `path`, `title`, `kind`, `row_count`
            and `columns`. On failure, an `Error: ...` line naming the problem
            and the available columns.

        """
        if kind not in ALL_KINDS:
            kinds = ", ".join(sorted(ALL_KINDS))
            return f"Error: unknown chart kind '{kind}'. Use one of: {kinds}."
        if not sql and not figure_path:
            return "Error: pass `sql` to draw a chart, or `figure_path` to publish one."

        spec = ChartSpec(
            kind=kind,
            title=title,
            x=x,
            y=y,
            series=series,
            z=z,
            description=description or "",
            stacked=stacked,
            y_format=y_format,
            slug=slugify(slug) if slug else "",
        )
        prepared = _prepare_chart(
            spec,
            sql=sql,
            figure_path=figure_path,
            backend=backend,
            config=config,
        )
        if isinstance(prepared, str):
            return prepared
        data, prebuilt = prepared

        thread_id = _thread_id()
        envelope = build_envelope(spec, data, sql=sql or "", thread_id=thread_id, figure=prebuilt)
        path = chart_path(thread_id, spec)
        result = backend.write(path, json.dumps(envelope, ensure_ascii=False, default=str))
        if result.error is not None:
            return f"Error: the chart could not be saved: {result.error}"

        return json.dumps(
            {
                "path": path,
                "title": spec.title,
                "kind": spec.kind,
                "row_count": envelope["row_count"],
                "columns": envelope["columns"],
            },
        )

    return create_chart
