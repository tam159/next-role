"""Chart specs, figure construction, and the stored envelope.

Everything here is pure: fixed rows in, a JSON-serialisable dict out. That is
deliberate — it is what lets every chart kind be checked without a warehouse,
and what keeps the model out of the business of emitting figure JSON.
"""

import json
from dataclasses import replace

import pytest
from backend.agents.analytics_agent.charts import (
    ALL_KINDS,
    COLORWAY,
    FIGURE_KINDS,
    SCHEMA,
    ChartData,
    ChartSpec,
    build_envelope,
    build_figure,
    build_kpi,
    build_table,
    chart_path,
    slugify,
    validate_mapping,
)

DAILY = ChartData(
    columns=["day", "runs", "cost"],
    rows=[("2026-09-01", 10, 1.5), ("2026-09-02", 14, 2.25), ("2026-09-03", 7, 0.75)],
)

BY_MODEL = ChartData(
    columns=["day", "model", "tokens"],
    rows=[
        ("2026-09-01", "gpt-5.6-terra", 100),
        ("2026-09-01", "gpt-5.4", 40),
        ("2026-09-02", "gpt-5.6-terra", 120),
    ],
)


_BASE_SPEC = ChartSpec(kind="line", title="Runs per day", x="day", y="runs")


def _spec(**kwargs: object) -> ChartSpec:
    """A line-chart spec over `DAILY`, with fields overridden per test."""
    return replace(_BASE_SPEC, **kwargs)


# ---------------------------------------------------------------------------
# slugs and paths
# ---------------------------------------------------------------------------


def test_slugify_makes_a_filesystem_safe_name():
    assert slugify("Runs per day — failed vs. total!") == "runs-per-day-failed-vs-total"


def test_slugify_falls_back_when_nothing_survives():
    assert slugify("!!!") == "chart"


def test_slugify_truncates_without_a_trailing_dash():
    assert not slugify("a b " * 40).endswith("-")


def test_chart_path_is_namespaced_per_thread():
    assert chart_path("t-1", _spec()) == "/charts/t-1/runs-per-day.plotly.json"


def test_chart_path_honours_an_explicit_slug():
    assert chart_path("t-1", _spec(slug="custom")) == "/charts/t-1/custom.plotly.json"


# ---------------------------------------------------------------------------
# mapping validation
# ---------------------------------------------------------------------------


def test_a_valid_mapping_passes():
    assert validate_mapping(_spec(), DAILY) is None


def test_unknown_kind_lists_the_supported_ones():
    error = validate_mapping(_spec(kind="sankey"), DAILY)
    assert error is not None
    assert "unknown chart kind" in error
    assert "heatmap" in error


def test_a_missing_column_names_what_is_available():
    error = validate_mapping(_spec(y="nope"), DAILY)
    assert error is not None
    assert "`nope`" in error
    assert "`runs`" in error


def test_a_missing_axis_explains_the_shape():
    error = validate_mapping(_spec(y=None), DAILY)
    assert error is not None
    assert "needs `x` and `y`" in error


def test_a_pie_wants_exactly_one_value_column():
    error = validate_mapping(_spec(kind="pie", y=["runs", "cost"]), DAILY)
    assert error is not None
    assert "exactly one `y`" in error


def test_series_and_multiple_y_columns_conflict():
    data = ChartData(columns=["day", "model", "runs", "cost"], rows=[("d", "m", 1, 2.0)])
    error = validate_mapping(_spec(series="model", y=["runs", "cost"]), data)
    assert error is not None
    assert "not both" in error


def test_a_heatmap_needs_all_three_axes():
    error = validate_mapping(_spec(kind="heatmap", series=None), DAILY)
    assert error is not None
    assert "needs `x`" in error


def test_a_kpi_needs_a_value_column():
    error = validate_mapping(_spec(kind="kpi", y=None), DAILY)
    assert error is not None
    assert "needs `y`" in error


def test_a_table_needs_no_mapping():
    assert validate_mapping(ChartSpec(kind="table", title="Rows"), DAILY) is None


def test_an_empty_result_is_rejected_before_plotting():
    error = validate_mapping(_spec(), ChartData(columns=[], rows=[]))
    assert error is not None
    assert "no columns" in error


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(FIGURE_KINDS))
def test_every_figure_kind_builds_json_serialisable_output(kind):
    spec = {
        "histogram": _spec(kind="histogram", x="runs", y=None),
        "heatmap": _spec(kind="heatmap", x="day", series="model", y="tokens"),
        "contour": _spec(kind="contour", x="day", series="model", y="tokens"),
        "pie": _spec(kind="pie", x="day", y="runs"),
    }.get(kind, _spec(kind=kind))
    data = BY_MODEL if kind in {"heatmap", "contour"} else DAILY

    figure = build_figure(spec, data)

    assert json.loads(json.dumps(figure))["data"]


def test_a_template_is_never_embedded():
    # The frontend re-themes at render time; a template is ~40 KB of dead weight
    # that would also lock the chart to one theme.
    assert "template" not in build_figure(_spec(), DAILY)["layout"]


def test_the_categorical_palette_travels_with_the_figure():
    assert build_figure(_spec(), DAILY)["layout"]["colorway"] == list(COLORWAY)


def test_series_becomes_one_trace_per_group_in_first_seen_order():
    figure = build_figure(_spec(x="day", y="tokens", series="model"), BY_MODEL)
    assert [trace["name"] for trace in figure["data"]] == ["gpt-5.6-terra", "gpt-5.4"]


def test_several_y_columns_become_several_traces():
    figure = build_figure(_spec(y=["runs", "cost"]), DAILY)
    assert [trace["name"] for trace in figure["data"]] == ["runs", "cost"]


def test_a_single_trace_hides_the_legend():
    assert build_figure(_spec(), DAILY)["layout"]["showlegend"] is False


def test_stacked_bars_set_barmode():
    assert build_figure(_spec(kind="bar", stacked=True), DAILY)["layout"]["barmode"] == "stack"


def test_percent_formatting_reaches_the_axis():
    figure = build_figure(_spec(y_format="percent"), DAILY)
    assert figure["layout"]["yaxis"]["tickformat"] == ".1%"


def test_a_heatmap_pivots_into_a_matrix_leaving_gaps_null():
    figure = build_figure(
        _spec(kind="heatmap", x="day", series="model", y="tokens"),
        BY_MODEL,
    )
    trace = figure["data"][0]
    assert trace["x"] == ["2026-09-01", "2026-09-02"]
    assert trace["y"] == ["gpt-5.6-terra", "gpt-5.4"]
    # gpt-5.4 has no 2026-09-02 row: a gap, not a zero.
    assert trace["z"][1][1] is None


def test_a_null_series_value_is_labelled_rather_than_dropped():
    data = ChartData(columns=["day", "model", "n"], rows=[("2026-09-01", None, 3)])
    figure = build_figure(_spec(x="day", y="n", series="model"), data)
    assert figure["data"][0]["name"] == "∅"


# ---------------------------------------------------------------------------
# native kinds
# ---------------------------------------------------------------------------


def test_a_table_payload_keeps_columns_and_stringifies_values():
    payload = build_table(DAILY)
    assert payload["columns"] == ["day", "runs", "cost"]
    assert payload["rows"][0] == ["2026-09-01", "10", "1.5"]


def test_a_table_payload_preserves_nulls():
    payload = build_table(ChartData(columns=["a"], rows=[(None,)]))
    assert payload["rows"] == [[None]]


def test_a_kpi_takes_the_first_row_and_a_comparison():
    payload = build_kpi(_spec(kind="kpi", x="day", y="runs"), DAILY)
    assert payload["value"] == 10
    assert payload["previous"] == 14
    assert payload["label"] == "2026-09-01"


def test_a_kpi_without_a_label_column_falls_back_to_the_measure_name():
    payload = build_kpi(ChartSpec(kind="kpi", title="Runs", y="runs"), DAILY)
    assert payload["label"] == "runs"


def test_a_kpi_over_an_empty_result_is_none_not_an_error():
    payload = build_kpi(_spec(kind="kpi", y="runs"), ChartData(columns=["runs"], rows=[]))
    assert payload["value"] is None


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


def test_the_envelope_records_how_the_chart_was_made():
    envelope = build_envelope(_spec(), DAILY, sql="SELECT 1", thread_id="t-1")

    assert envelope["schema"] == SCHEMA
    assert envelope["sql"] == "SELECT 1"
    assert envelope["thread_id"] == "t-1"
    assert envelope["row_count"] == 3
    assert envelope["columns"] == ["day", "runs", "cost"]
    assert envelope["mapping"]["x"] == "day"
    assert envelope["created_at"].endswith("+00:00")


def test_figure_kinds_carry_a_figure_and_no_native_payload():
    envelope = build_envelope(_spec(), DAILY, sql="", thread_id="t")
    assert "figure" in envelope
    assert "table" not in envelope
    assert "kpi" not in envelope


def test_table_kind_carries_a_table_payload_not_a_figure():
    envelope = build_envelope(ChartSpec(kind="table", title="Rows"), DAILY, sql="", thread_id="t")
    assert "table" in envelope
    assert "figure" not in envelope


def test_kpi_kind_carries_a_kpi_payload_not_a_figure():
    envelope = build_envelope(
        ChartSpec(kind="kpi", title="Runs", y="runs"),
        DAILY,
        sql="",
        thread_id="t",
    )
    assert "kpi" in envelope
    assert "figure" not in envelope


def test_a_prebuilt_figure_is_wrapped_verbatim():
    prebuilt = {"data": [{"type": "sankey"}], "layout": {}}
    envelope = build_envelope(_spec(), DAILY, sql="", thread_id="t", figure=prebuilt)
    assert envelope["figure"] == prebuilt


def test_every_envelope_is_json_serialisable():
    for kind in sorted(ALL_KINDS):
        spec = {
            "histogram": _spec(kind="histogram", x="runs", y=None),
            "heatmap": _spec(kind="heatmap", x="day", series="model", y="tokens"),
            "contour": _spec(kind="contour", x="day", series="model", y="tokens"),
            "pie": _spec(kind="pie", x="day", y="runs"),
        }.get(kind, _spec(kind=kind))
        data = BY_MODEL if kind in {"heatmap", "contour"} else DAILY
        json.dumps(build_envelope(spec, data, sql="", thread_id="t"))


# ---------------------------------------------------------------------------
# distribution and grid kinds
# ---------------------------------------------------------------------------

DURATIONS = ChartData(
    columns=["model", "duration_s", "status"],
    rows=[
        ("gpt-5.6-terra", 12.0, "ok"),
        ("gpt-5.6-terra", 30.5, "failed"),
        ("gpt-5.4", 8.25, "ok"),
        ("gpt-5.4", 9.0, "ok"),
    ],
)


def test_every_schema_kind_is_drawn_without_a_script():
    """No kind may depend on the Python fallback, which is off by default."""
    assert {"box", "violin", "contour", "histogram2d"} <= FIGURE_KINDS
    assert FIGURE_KINDS | {"table", "kpi"} == ALL_KINDS


@pytest.mark.parametrize("kind", ["box", "violin"])
def test_a_distribution_needs_only_its_observations(kind):
    assert validate_mapping(_spec(kind=kind, x=None, y="duration_s"), DURATIONS) is None
    error = validate_mapping(_spec(kind=kind, x="model", y=None), DURATIONS)
    assert error is not None
    assert "`y`" in error


def test_a_box_plot_groups_raw_values_by_category():
    figure = build_figure(_spec(kind="box", x="model", y="duration_s"), DURATIONS)
    (trace,) = figure["data"]
    assert trace["type"] == "box"
    assert list(trace["x"]) == ["gpt-5.6-terra", "gpt-5.6-terra", "gpt-5.4", "gpt-5.4"]
    assert list(trace["y"]) == [12.0, 30.5, 8.25, 9.0]


def test_a_violin_splits_series_into_grouped_traces():
    figure = build_figure(
        _spec(kind="violin", x="model", y="duration_s", series="status"),
        DURATIONS,
    )
    assert [t["name"] for t in figure["data"]] == ["ok", "failed"]
    assert all(t["type"] == "violin" for t in figure["data"])
    assert figure["layout"]["violinmode"] == "group"


def test_a_box_without_a_category_is_one_box_per_y_column():
    figure = build_figure(_spec(kind="box", x=None, y="duration_s"), DURATIONS)
    (trace,) = figure["data"]
    assert "x" not in trace
    assert trace["name"] == "duration_s"


def test_a_histogram2d_bins_two_columns():
    figure = build_figure(_spec(kind="histogram2d", x="runs", y="cost"), DAILY)
    (trace,) = figure["data"]
    assert trace["type"] == "histogram2d"
    assert list(trace["x"]) == [10, 14, 7]
    assert list(trace["y"]) == [1.5, 2.25, 0.75]


def test_a_contour_pivots_like_a_heatmap_but_keeps_raw_axes():
    figure = build_figure(_spec(kind="contour", x="day", series="model", y="tokens"), BY_MODEL)
    (trace,) = figure["data"]
    assert trace["type"] == "contour"
    assert trace["y"] == ["gpt-5.6-terra", "gpt-5.4"]
    assert trace["z"][1][1] is None


@pytest.mark.parametrize("kind", ["contour", "histogram2d"])
def test_a_two_axis_kind_takes_exactly_one_value_column(kind):
    error = validate_mapping(
        _spec(kind=kind, x="day", series="model", y=["tokens", "tokens"]),
        BY_MODEL,
    )
    assert error == f"Error: a {kind} takes exactly one `y` column."


def test_a_published_figure_reports_no_row_count():
    """There were no query rows, and "0 rows" reads as an empty result."""
    envelope = build_envelope(
        _spec(kind="scatter"),
        ChartData(columns=[], rows=[]),
        sql="",
        thread_id="t",
        figure={"data": [{"type": "box"}], "layout": {}},
    )

    assert envelope["row_count"] is None


def test_a_queried_chart_still_reports_its_rows():
    assert build_envelope(_spec(), DAILY, sql="SELECT 1", thread_id="t")["row_count"] == 3
