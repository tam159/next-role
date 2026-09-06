"""The three tools as the model experiences them.

Every failure must come back as readable text rather than an exception: the
model's only recovery path is reading what went wrong. These tests drive the
tools with a fake warehouse and an in-memory object store.
"""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from backend.agents.analytics_agent import tools as tools_mod
from backend.agents.analytics_agent.metadata import Snapshot, TableMeta
from backend.agents.analytics_agent.settings import WarehouseSettings
from backend.agents.analytics_agent.tools import (
    make_create_chart,
    make_describe_data,
    make_run_sql,
)
from backend.agents.analytics_agent.warehouse import QueryOutcome
from backend.agents.career_agent.object_backend import ObjectStoreBackend
from backend.agents.career_agent.shell_backend import VirtualPathShellBackend
from deepagents.backends import CompositeBackend
from obstore.store import MemoryStore
from pydantic import ValidationError

THREAD = "thread-1"


@pytest.fixture
def store() -> MemoryStore:
    """Fresh in-memory object store per test."""
    return MemoryStore()


@pytest.fixture
def backend(tmp_path, store) -> CompositeBackend:
    """A composite backend with real chart routing and a host-shell default."""
    return CompositeBackend(
        default=VirtualPathShellBackend(root_dir=str(tmp_path), virtual_mode=True),
        routes={
            "/charts/": ObjectStoreBackend("charts", store_factory=lambda: store),
            "/reports/": ObjectStoreBackend("reports", store_factory=lambda: store),
        },
    )


@pytest.fixture(autouse=True)
def _thread(monkeypatch, tmp_path):
    """Pin the thread id and keep scratch inside the test's tmp dir."""
    monkeypatch.setattr(tools_mod, "current_thread_id", lambda: THREAD)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))


_BASE_OUTCOME = QueryOutcome(
    columns=["day", "runs"],
    rows=[("2026-09-01", 3), ("2026-09-02", 5)],
    elapsed_s=0.1,
)


def _outcome(**kwargs: object) -> QueryOutcome:
    """A two-row result, with fields overridden per test."""
    return replace(_BASE_OUTCOME, **kwargs)


def _call(tool, **kwargs) -> str:
    """Invoke a structured tool the way the agent runtime does."""
    return tool.invoke(kwargs)


# ---------------------------------------------------------------------------
# describe_data
# ---------------------------------------------------------------------------


class FakeCache:
    """Serves a fixed snapshot and records refresh requests."""

    def __init__(self, snapshot):
        """Wrap the snapshot this cache always returns."""
        self.snapshot = snapshot
        self.refreshed = False

    def get(self, *, refresh: bool = False):
        """Return the snapshot, recording whether a refresh was asked for."""
        self.refreshed = self.refreshed or refresh
        return self.snapshot


@pytest.fixture
def snapshot() -> Snapshot:
    """A snapshot holding one mart."""
    snap = Snapshot()
    snap.tables["nextrole_marts.fct_run"] = TableMeta(
        database="nextrole_marts",
        name="fct_run",
        description="Grain: one run.",
        row_count=5,
    )
    return snap


def test_describe_data_without_a_table_returns_the_overview(snapshot):
    text = _call(make_describe_data(FakeCache(snapshot)))

    assert "# NextRole warehouse" in text
    assert "`fct_run`" in text


def test_describe_data_with_a_bare_name_resolves_it(snapshot):
    text = _call(make_describe_data(FakeCache(snapshot)), table="fct_run")

    assert "# `nextrole_marts.fct_run`" in text


def test_describe_data_names_the_candidates_when_ambiguous(snapshot):
    snapshot.tables["nextrole_staging.fct_run"] = snapshot.tables["nextrole_marts.fct_run"]

    text = _call(make_describe_data(FakeCache(snapshot)), table="fct_run")

    assert text.startswith("Error:")
    assert "nextrole_staging.fct_run" in text


def test_describe_data_on_an_unknown_table_points_back_to_the_overview(snapshot):
    text = _call(make_describe_data(FakeCache(snapshot)), table="nope")

    assert text.startswith("Error: no table named 'nope'")
    assert "describe_data()" in text


def test_describe_data_forwards_the_refresh_flag(snapshot):
    cache = FakeCache(snapshot)

    _call(make_describe_data(cache), refresh=True)

    assert cache.refreshed is True


# ---------------------------------------------------------------------------
# run_sql
# ---------------------------------------------------------------------------


def test_run_sql_renders_a_markdown_table(backend):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        text = _call(make_run_sql(backend), sql="SELECT day, runs FROM t")

    assert "| day | runs |" in text
    assert "| 2026-09-01 | 3 |" in text


def test_run_sql_returns_warehouse_errors_verbatim(backend):
    with patch.object(tools_mod, "run_readonly", return_value="Error: nope"):
        text = _call(make_run_sql(backend), sql="DROP TABLE t")

    assert text == "Error: nope"


def test_run_sql_applies_the_display_limit_by_default(backend):
    settings = WarehouseSettings(display_rows=25)
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()) as query:
        _call(make_run_sql(backend, settings), sql="SELECT 1")

    assert query.call_args.kwargs["display_limit"] == 25


def test_save_as_asks_for_the_whole_result(backend):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()) as query:
        _call(make_run_sql(backend), sql="SELECT 1", save_as="rows.csv")

    assert query.call_args.kwargs["display_limit"] is None


def test_save_as_writes_csv_and_reports_the_path(backend, tmp_path):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        text = _call(make_run_sql(backend), sql="SELECT 1", save_as="rows.csv")

    path = text.rsplit("`", 2)[-2]
    assert THREAD in path
    with open(path) as handle:  # noqa: PTH123 — asserting on the real file
        assert handle.read().splitlines()[0] == "day,runs"
    assert "2 rows" in text


def test_save_as_never_writes_into_the_repository(backend, tmp_path):
    """Scratch must not travel through the composite backend.

    The default route is jailed under the agent package, so a write there would
    land in the bind-mounted source tree instead of the shell's own filesystem.
    """
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        text = _call(make_run_sql(backend), sql="SELECT 1", save_as="rows.csv")

    assert list((tmp_path / "nextrole-analytics").iterdir())
    assert "/charts/" not in text


def test_save_as_sanitises_a_traversing_file_name(backend, tmp_path):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        text = _call(make_run_sql(backend), sql="SELECT 1", save_as="../../escape.csv")

    assert "escape.csv" not in text
    assert "result.csv" in text


# ---------------------------------------------------------------------------
# create_chart
# ---------------------------------------------------------------------------


def _saved(store: MemoryStore) -> dict:
    """The single chart envelope written to the object store."""
    keys = [str(meta["path"]) for meta in store.list().collect()]
    assert len(keys) == 1, keys
    return json.loads(bytes(store.get(keys[0]).bytes()))


def test_create_chart_stores_an_envelope_and_reports_its_path(backend, store):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        result = _call(
            make_create_chart(backend),
            kind="line",
            title="Runs per day",
            sql="SELECT day, runs FROM t",
            x="day",
            y="runs",
        )

    payload = json.loads(result)
    assert payload["path"] == f"/charts/{THREAD}/runs-per-day.plotly.json"
    assert payload["row_count"] == 2
    assert payload["columns"] == ["day", "runs"]

    envelope = _saved(store)
    assert envelope["schema"] == "nextrole.chart/v1"
    assert envelope["sql"] == "SELECT day, runs FROM t"
    assert envelope["figure"]["data"]


def test_create_chart_returns_mapping_errors_before_writing(backend, store):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        result = _call(
            make_create_chart(backend),
            kind="line",
            title="Bad",
            sql="SELECT 1",
            x="day",
            y="missing",
        )

    assert result.startswith("Error:")
    assert store.list().collect() == []


def test_create_chart_refuses_an_unaggregated_result(backend):
    settings = WarehouseSettings(chart_max_rows=1)
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        result = _call(
            make_create_chart(backend, settings),
            kind="line",
            title="Too many",
            sql="SELECT 1",
            x="day",
            y="runs",
        )

    assert "too many to plot" in result
    assert "Aggregate" in result


def test_create_chart_refuses_an_empty_result(backend):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome(rows=[])):
        result = _call(
            make_create_chart(backend),
            kind="line",
            title="Nothing",
            sql="SELECT 1",
            x="day",
            y="runs",
        )

    assert "no rows" in result


def test_create_chart_needs_sql_or_a_figure(backend):
    result = _call(make_create_chart(backend), kind="line", title="Empty")

    assert "pass `sql`" in result


def test_an_unknown_kind_is_rejected_by_the_tool_schema(backend):
    """The `kind` literal constrains the model before the body ever runs.

    Worth pinning: it is why the model gets a schema error naming every valid
    kind rather than a free-form string it has to parse.
    """
    with pytest.raises(ValidationError, match="'line', 'bar'"):
        _call(make_create_chart(backend), kind="sankey", title="X", sql="SELECT 1")


def test_a_table_kind_stores_rows_instead_of_a_figure(backend, store):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        _call(make_create_chart(backend), kind="table", title="Rows", sql="SELECT 1")

    envelope = _saved(store)
    assert envelope["table"]["columns"] == ["day", "runs"]
    assert "figure" not in envelope


def test_a_kpi_kind_stores_a_single_number(backend, store):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        _call(
            make_create_chart(backend),
            kind="kpi",
            title="Runs",
            sql="SELECT 1",
            y="runs",
        )

    assert _saved(store)["kpi"]["value"] == 3


def test_an_explicit_slug_sets_the_file_name(backend):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        result = _call(
            make_create_chart(backend),
            kind="line",
            title="Runs per day",
            sql="SELECT 1",
            x="day",
            y="runs",
            slug="daily runs!",
        )

    assert json.loads(result)["path"].endswith("daily-runs.plotly.json")


# ---------------------------------------------------------------------------
# create_chart(figure_path=...) — the Python fallback's exit
# ---------------------------------------------------------------------------


def _write_scratch(tmp_path, name: str, content: str) -> str:
    directory = tmp_path / "nextrole-analytics" / THREAD
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content)
    return str(path)


def test_a_script_figure_is_published_verbatim(backend, store, tmp_path):
    figure = {"data": [{"type": "box", "y": [1, 2, 3]}], "layout": {}}
    path = _write_scratch(tmp_path, "box.plotly.json", json.dumps(figure))

    result = _call(
        make_create_chart(backend),
        kind="scatter",
        title="Duration by model",
        figure_path=path,
    )

    assert json.loads(result)["path"].endswith("duration-by-model.plotly.json")
    assert _saved(store)["figure"] == figure


def test_a_missing_script_figure_explains_the_recipe(backend, tmp_path):
    result = _call(
        make_create_chart(backend),
        kind="scatter",
        title="X",
        figure_path=str(tmp_path / "nothing.json"),
    )

    assert "no file at" in result
    assert "write_json" in result


def test_invalid_json_from_a_script_is_reported(backend, tmp_path):
    path = _write_scratch(tmp_path, "broken.json", "{not json")

    result = _call(make_create_chart(backend), kind="scatter", title="X", figure_path=path)

    assert "not valid JSON" in result


def test_json_that_is_not_a_figure_is_reported(backend, tmp_path):
    path = _write_scratch(tmp_path, "rows.json", json.dumps({"rows": [1, 2]}))

    result = _call(make_create_chart(backend), kind="scatter", title="X", figure_path=path)

    assert "not a Plotly figure" in result


def test_a_box_plot_can_be_published_from_a_script(backend, store, tmp_path):
    """The fallback's whole purpose: publish a figure this module cannot draw."""
    figure = {"data": [{"type": "box", "y": [1, 2, 3]}], "layout": {}}
    path = _write_scratch(tmp_path, "box.plotly.json", json.dumps(figure))

    result = _call(
        make_create_chart(backend),
        kind="box",
        title="Duration by status",
        figure_path=path,
    )

    assert json.loads(result)["kind"] == "box"
    assert _saved(store)["figure"] == figure


def test_a_publish_only_kind_without_a_figure_says_how_to_make_one(backend):
    with patch.object(tools_mod, "run_readonly", return_value=_outcome()):
        result = _call(
            make_create_chart(backend),
            kind="box",
            title="X",
            sql="SELECT 1",
            y="runs",
        )

    assert "cannot be built from SQL" in result
    assert "figure_path" in result


def test_a_native_kind_cannot_be_published_from_a_figure(backend, tmp_path):
    path = _write_scratch(tmp_path, "f.json", json.dumps({"data": [], "layout": {}}))

    result = _call(make_create_chart(backend), kind="table", title="X", figure_path=path)

    assert "cannot be 'table'" in result


def test_a_storage_failure_is_reported_not_raised(backend):
    with (
        patch.object(tools_mod, "run_readonly", return_value=_outcome()),
        patch.object(backend, "write", return_value=SimpleNamespace(error="bucket missing")),
    ):
        result = _call(
            make_create_chart(backend),
            kind="line",
            title="X",
            sql="SELECT 1",
            x="day",
            y="runs",
        )

    assert "could not be saved" in result
    assert "bucket missing" in result
