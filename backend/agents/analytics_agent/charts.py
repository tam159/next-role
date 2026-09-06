"""Turn query results into chart envelopes the frontend can render.

Figures are built here, server-side, from rows the agent already fetched — the
model never re-types data into a plotting script. The output is a JSON envelope
stored as an artifact, so a chart survives the run that made it and re-renders
when an old thread is reopened.

Two kinds deliberately skip Plotly. A `table` and a `kpi` are HTML the frontend
draws with its own design tokens: they look like the rest of the product, and
they keep the browser on Plotly's small cartesian bundle, which carries no
`table` or `indicator` trace.

Colors here are plain values rather than a Plotly template. The frontend
re-themes every figure at render time for light and dark, so an envelope must
not bake in one theme's palette.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import plotly.graph_objects as go

ChartKind = Literal[
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
]

#: Kinds this module draws from query rows.
FIGURE_KINDS = frozenset({"line", "bar", "area", "scatter", "pie", "histogram", "heatmap"})

#: Kinds the frontend's Plotly bundle renders but this module does not build:
#: they come from a script through `figure_path`. Keeping them in the schema is
#: the point of the fallback — a box plot is exactly what it exists to publish.
PUBLISH_ONLY_KINDS = frozenset({"box", "violin", "contour", "histogram2d"})

#: `table` and `kpi` are native frontend components, not Plotly traces.
ALL_KINDS = FIGURE_KINDS | PUBLISH_ONLY_KINDS | {"table", "kpi"}

#: Envelope version. Bump when the shape changes in a way the frontend must
#: branch on; the frontend refuses anything it does not recognise.
SCHEMA = "nextrole.chart/v1"

#: Categorical palette, matching the app's accent and file-category hues.
#: Ordered so neighbouring series stay distinguishable.
COLORWAY = (
    "#0e9f6e",
    "#e0623c",
    "#5b5bd6",
    "#2563eb",
    "#c47a16",
    "#8a5a9e",
    "#4a6b8a",
    "#5a8a4a",
    "#b56a7a",
    "#d9785a",
)

_TICKFORMAT = {
    "number": ",.0f",
    "percent": ".1%",
    "currency": "$,.2f",
    "duration_s": ",.1f",
}

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "chart", max_length: int = 60) -> str:
    """Turn a title into a filesystem-safe slug for the chart's path."""
    slug = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")[:max_length].strip("-")
    return slug or fallback


@dataclass
class ChartSpec:
    """What to draw, and how to read the result columns."""

    kind: ChartKind
    title: str
    x: str | None = None
    y: str | list[str] | None = None
    series: str | None = None
    z: str | None = None
    description: str = ""
    stacked: bool = False
    orientation: Literal["v", "h"] = "v"
    y_format: str = "number"
    slug: str = ""

    @property
    def y_columns(self) -> list[str]:
        """`y` normalized to a list (empty when unset)."""
        if self.y is None:
            return []
        return [self.y] if isinstance(self.y, str) else list(self.y)

    def mapping(self) -> dict[str, Any]:
        """Return the column mapping, recorded in the envelope for later inspection."""
        return {
            "x": self.x,
            "y": self.y,
            "series": self.series,
            "z": self.z,
            "stacked": self.stacked,
            "orientation": self.orientation,
            "y_format": self.y_format,
        }


@dataclass
class ChartData:
    """Rows to plot, as returned by the warehouse."""

    columns: list[str]
    rows: list[tuple[Any, ...]] = field(default_factory=list)

    def index(self, column: str) -> int:
        """Position of `column` in the result."""
        return self.columns.index(column)

    def values(self, column: str) -> list[Any]:
        """Every value in `column`, in row order."""
        position = self.index(column)
        return [row[position] for row in self.rows]


#: Per-kind mapping requirements: which spec fields must be set, and the
#: sentence that explains the shape when one is missing. Table-driven so adding
#: a kind is one row rather than another branch.
_REQUIREMENTS: dict[str, tuple[tuple[str, ...], str]] = {
    "table": ((), ""),
    "kpi": (("y",), "a kpi needs `y` (the column holding the number)"),
    "histogram": (("x",), "a histogram needs `x` (the column to bin)"),
    "heatmap": (
        ("x", "series", "y"),
        "a heatmap needs `x` (columns), `series` (rows) and `y` (the value)",
    ),
    "pie": (("x", "y"), "a pie needs `x` (the labels) and one `y` (the values)"),
    "line": (("x", "y"), "a line chart needs `x` and `y`"),
    "area": (("x", "y"), "an area chart needs `x` and `y`"),
    "bar": (("x", "y"), "a bar chart needs `x` and `y`"),
    "scatter": (("x", "y"), "a scatter chart needs `x` and `y`"),
}


def _missing_requirement(spec: ChartSpec) -> str | None:
    """Return the first unset required field's explanation, if any."""
    required, message = _REQUIREMENTS[spec.kind]
    present = {"x": spec.x, "y": spec.y_columns, "series": spec.series, "z": spec.z}
    if any(not present[field_name] for field_name in required):
        return message
    return None


def validate_mapping(spec: ChartSpec, data: ChartData) -> str | None:
    """Return `None` when the spec can be drawn, else an `Error: ...` string.

    Messages name the available columns, so a mistake is correctable in one
    turn instead of by trial and error.
    """
    if spec.kind not in ALL_KINDS:
        kinds = ", ".join(sorted(ALL_KINDS))
        return f"Error: unknown chart kind '{spec.kind}'. Use one of: {kinds}."
    if not data.columns:
        return "Error: the query returned no columns, so there is nothing to plot."

    available = ", ".join(f"`{c}`" for c in data.columns)
    referenced = [c for c in [spec.x, spec.series, spec.z, *spec.y_columns] if c]
    missing = [c for c in dict.fromkeys(referenced) if c not in data.columns]
    if missing:
        named = ", ".join(f"`{c}`" for c in missing)
        return f"Error: column(s) {named} are not in the result. Available: {available}."

    shape_error = _missing_requirement(spec)
    if shape_error:
        return f"Error: {shape_error}. Available: {available}."
    return _conflicting_mapping(spec)


def _conflicting_mapping(spec: ChartSpec) -> str | None:
    """Return an explanation when the mapping is complete but self-contradictory."""
    if spec.kind == "pie" and len(spec.y_columns) != 1:
        return "Error: a pie takes exactly one `y` column."
    if spec.series and len(spec.y_columns) > 1:
        return (
            "Error: use either `series` (one column split into traces) or several `y` "
            "columns, not both."
        )
    return None


# ---------------------------------------------------------------------------
# figure construction
# ---------------------------------------------------------------------------


def _grouped(data: ChartData, spec: ChartSpec) -> list[tuple[str, list[Any], list[Any]]]:
    """Split rows into (label, x values, y values) traces.

    `series` splits one y column into a trace per distinct value; several `y`
    columns become one trace each. Group order follows first appearance, so a
    chart's legend matches the order the query returned.
    """
    y_column = spec.y_columns[0]
    if not spec.series:
        return [(y, data.values(spec.x or y), data.values(y)) for y in spec.y_columns]

    x_index, y_index, series_index = (
        data.index(spec.x or ""),
        data.index(y_column),
        data.index(spec.series),
    )
    groups: dict[str, tuple[list[Any], list[Any]]] = {}
    for row in data.rows:
        label = "∅" if row[series_index] is None else str(row[series_index])
        xs, ys = groups.setdefault(label, ([], []))
        xs.append(row[x_index])
        ys.append(row[y_index])
    return [(label, xs, ys) for label, (xs, ys) in groups.items()]


def _traces(spec: ChartSpec, data: ChartData) -> list[Any]:
    """Build the traces for a cartesian kind."""
    if spec.kind == "histogram":
        return [go.Histogram(x=data.values(spec.x or ""), name=spec.x or "")]
    if spec.kind == "pie":
        return [
            go.Pie(
                labels=data.values(spec.x or ""),
                values=data.values(spec.y_columns[0]),
                hole=0.45,
            ),
        ]

    traces: list[Any] = []
    for label, xs, ys in _grouped(data, spec):
        if spec.kind == "bar":
            traces.append(go.Bar(name=label, x=xs, y=ys, orientation=spec.orientation))
        elif spec.kind == "scatter":
            traces.append(go.Scatter(name=label, x=xs, y=ys, mode="markers"))
        elif spec.kind == "area":
            traces.append(
                go.Scatter(
                    name=label,
                    x=xs,
                    y=ys,
                    mode="lines",
                    fill="tonexty" if spec.stacked else "tozeroy",
                    stackgroup="one" if spec.stacked else None,
                ),
            )
        else:
            traces.append(go.Scatter(name=label, x=xs, y=ys, mode="lines+markers"))
    return traces


def _heatmap(spec: ChartSpec, data: ChartData) -> go.Heatmap:
    """Pivot three columns into a dense matrix.

    Missing combinations stay `None` so the frontend renders a gap rather than
    a zero, which would read as a real measurement.
    """
    x_values = list(dict.fromkeys(data.values(spec.x or "")))
    rows = list(dict.fromkeys(data.values(spec.series or "")))
    lookup = {
        (row[data.index(spec.x or "")], row[data.index(spec.series or "")]): row[
            data.index(spec.y_columns[0])
        ]
        for row in data.rows
    }
    z = [[lookup.get((x, row)) for x in x_values] for row in rows]
    return go.Heatmap(x=[str(x) for x in x_values], y=[str(r) for r in rows], z=z)


def build_figure(spec: ChartSpec, data: ChartData) -> dict[str, Any]:
    """Build a Plotly figure dict for a figure kind.

    The layout carries structure only — titles, axis formats, legend placement.
    Colors beyond the categorical `colorway` are left to the frontend, which
    applies the viewer's theme at render time.
    """
    traces = [_heatmap(spec, data)] if spec.kind == "heatmap" else _traces(spec, data)
    figure = go.Figure(data=traces)
    y_title = ", ".join(spec.y_columns) if spec.y_columns else ""
    figure.update_layout(
        colorway=list(COLORWAY),
        barmode="stack" if (spec.stacked and spec.kind == "bar") else "group",
        showlegend=len(traces) > 1,
        legend={"orientation": "h", "y": -0.2},
        margin={"l": 56, "r": 16, "t": 8, "b": 40},
        hovermode="closest" if spec.kind in {"scatter", "pie", "heatmap"} else "x unified",
        xaxis={"title": {"text": spec.x or ""}},
        yaxis={
            "title": {"text": y_title},
            "tickformat": _TICKFORMAT.get(spec.y_format, ""),
        },
    )
    payload = figure.to_plotly_json()
    # A Plotly template is ~40 KB of theme the frontend overrides anyway.
    payload.get("layout", {}).pop("template", None)
    return payload


def build_table(data: ChartData) -> dict[str, Any]:
    """Render rows as a table payload the frontend draws with its own styles."""
    return {
        "columns": list(data.columns),
        "rows": [[None if v is None else str(v) for v in row] for row in data.rows],
    }


def build_kpi(spec: ChartSpec, data: ChartData) -> dict[str, Any]:
    """Render a single headline number, with an optional comparison row.

    Takes the first row's `y` value. A second row, when present, becomes the
    comparison the frontend shows underneath — the usual "this week vs last".
    """
    values = data.values(spec.y_columns[0])
    labels = data.values(spec.x) if spec.x in data.columns else []
    label = labels[0] if labels else None
    return {
        "value": values[0] if values else None,
        "label": str(label) if label is not None else spec.y_columns[0],
        "format": spec.y_format,
        "previous": values[1] if len(values) > 1 else None,
    }


def build_envelope(
    spec: ChartSpec,
    data: ChartData,
    *,
    sql: str,
    thread_id: str,
    figure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the stored artifact: the drawing plus how it was produced.

    `sql` and `mapping` travel with the chart so a reader months later can see
    exactly what was measured, and the agent can rebuild or adjust it.
    """
    envelope: dict[str, Any] = {
        "schema": SCHEMA,
        "title": spec.title,
        "description": spec.description,
        "kind": spec.kind,
        "sql": sql,
        "mapping": spec.mapping(),
        # None, not 0, when the figure came from a script: there were no query
        # rows to count, and "0 rows" reads as an empty result.
        "row_count": len(data.rows) if sql else None,
        "columns": list(data.columns),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "thread_id": thread_id,
    }
    if spec.kind == "table":
        envelope["table"] = build_table(data)
    elif spec.kind == "kpi":
        envelope["kpi"] = build_kpi(spec, data)
    else:
        envelope["figure"] = figure if figure is not None else build_figure(spec, data)
    return envelope


def chart_path(thread_id: str, spec: ChartSpec) -> str:
    """Virtual path for a chart artifact, namespaced by thread.

    Per-thread so two conversations can both have a "runs by day" chart without
    one overwriting the other.
    """
    return f"/charts/{thread_id}/{spec.slug or slugify(spec.title)}.plotly.json"
