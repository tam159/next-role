"""Merging dbt prose, Cube metrics and ClickHouse facts into one dictionary.

The merge is the reason `describe_data` exists as a tool rather than as SQL the
agent writes itself: no single source carries meaning, types and metrics at
once. These tests pin the join keys, the precedence between sources, and the
degraded output when a source is unreachable.
"""

import json
from pathlib import Path

import pytest
from backend.agents.analytics_agent.metadata import (
    Snapshot,
    build_snapshot,
    render_overview,
    render_table,
    resolve_table,
)
from backend.agents.analytics_agent.settings import WarehouseSettings

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def dbt() -> tuple[dict, dict]:
    """Dbt's manifest and catalog, trimmed to two marts."""
    return _load("manifest.min.json"), _load("catalog.min.json")


@pytest.fixture
def cube() -> dict:
    """Cube's metadata document with one cube and one view."""
    return _load("cube_meta.min.json")


@pytest.fixture
def clickhouse() -> tuple[list[dict], list[dict]]:
    """`system.tables` / `system.columns` rows for the same marts."""
    tables = [
        {
            "database": "nextrole_marts",
            "name": "fct_run",
            "engine": "MergeTree",
            "total_rows": 252,
            "sorting_key": "run_date, owner",
            "comment": "",
        },
        {
            "database": "nextrole_staging",
            "name": "stg_app__runs",
            "engine": "View",
            "total_rows": None,
            "sorting_key": "",
            "comment": "",
        },
    ]
    columns = [
        {
            "database": "nextrole_marts",
            "table": "fct_run",
            "name": "run_id",
            "type": "String",
            "comment": "",
        },
        {
            "database": "nextrole_marts",
            "table": "fct_run",
            "name": "owner",
            "type": "String",
            "comment": "persisted comment",
        },
    ]
    return tables, columns


@pytest.fixture
def snapshot(dbt, cube, clickhouse) -> Snapshot:
    """A snapshot with all three sources present."""
    return build_snapshot(dbt=dbt, cube=cube, clickhouse=clickhouse)


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_relations_are_keyed_on_schema_and_alias_not_the_fake_database(snapshot):
    # manifest.database is the literal label "clickhouse" — using it would key
    # this table as `clickhouse.fct_run` and never match the warehouse.
    assert "nextrole_marts.fct_run" in snapshot.tables
    assert not any(key.startswith("clickhouse.") for key in snapshot.tables)


def test_clickhouse_supplies_counts_and_sort_keys(snapshot):
    table = snapshot.tables["nextrole_marts.fct_run"]
    assert table.row_count == 252
    assert table.sorting_key == "run_date, owner"
    assert table.engine == "MergeTree"


def test_dbt_prose_wins_over_a_persisted_comment(snapshot):
    owner = next(c for c in snapshot.tables["nextrole_marts.fct_run"].columns if c.name == "owner")
    assert owner.description.startswith("Owning user;")
    assert owner.comment == "persisted comment"


def test_columns_only_dbt_knows_are_added_with_catalog_types(snapshot):
    notes = next(c for c in snapshot.tables["nextrole_marts.fct_run"].columns if c.name == "notes")
    assert notes.type == "Nullable(String)"


def test_pii_class_is_carried_through(snapshot):
    name = next(c for c in snapshot.tables["nextrole_marts.dim_user"].columns if c.name == "name")
    assert name.pii == "direct"


def test_cube_members_lose_the_redundant_cube_prefix(snapshot):
    assert [m.name for m in snapshot.cubes["runs"].measures] == ["success_rate"]
    assert [d.name for d in snapshot.cubes["runs"].dimensions] == ["owner"]


def test_a_cube_links_to_the_mart_it_reads(snapshot):
    # No `meta.table` in the fixture — the fallback map resolves runs -> fct_run.
    assert snapshot.tables["nextrole_marts.fct_run"].cubes == ["runs"]


def test_meta_table_overrides_the_fallback_map(dbt, clickhouse):
    cube = {
        "cubes": [
            {
                "name": "runs",
                "type": "cube",
                "meta": {"table": "dim_user"},
                "measures": [],
                "dimensions": [],
            },
        ],
    }
    snapshot = build_snapshot(dbt=dbt, cube=cube, clickhouse=clickhouse)
    assert snapshot.tables["nextrole_marts.dim_user"].cubes == ["runs"]
    assert snapshot.tables["nextrole_marts.fct_run"].cubes == []


def test_grain_is_the_first_sentence(snapshot):
    assert snapshot.tables["nextrole_marts.fct_run"].grain == "Grain: one agent run"


# ---------------------------------------------------------------------------
# degraded sources
# ---------------------------------------------------------------------------


def test_missing_dbt_still_yields_tables_plus_a_notice(cube, clickhouse):
    snapshot = build_snapshot(dbt=None, cube=cube, clickhouse=clickhouse)
    assert "nextrole_marts.fct_run" in snapshot.tables
    assert any("dbt docs unreachable" in n for n in snapshot.notices)


def test_missing_clickhouse_still_yields_dbt_descriptions(dbt, cube):
    snapshot = build_snapshot(dbt=dbt, cube=cube, clickhouse=None)
    table = snapshot.tables["nextrole_marts.fct_run"]
    assert table.description.startswith("Grain: one agent run")
    assert table.row_count is None
    assert any("ClickHouse metadata unavailable" in n for n in snapshot.notices)


def test_missing_cube_notes_the_absent_metric_layer(dbt, clickhouse):
    snapshot = build_snapshot(dbt=dbt, cube=None, clickhouse=clickhouse)
    assert snapshot.cubes == {}
    assert any("Cube unreachable" in n for n in snapshot.notices)


def test_every_source_missing_is_still_a_snapshot():
    snapshot = build_snapshot(dbt=None, cube=None, clickhouse=None)
    assert snapshot.tables == {}
    assert len(snapshot.notices) == 3


# ---------------------------------------------------------------------------
# resolution + rendering
# ---------------------------------------------------------------------------


def test_resolve_accepts_a_bare_table_name(snapshot):
    assert resolve_table(snapshot, "fct_run") == "nextrole_marts.fct_run"


def test_resolve_accepts_a_qualified_name_and_backticks(snapshot):
    assert resolve_table(snapshot, "`nextrole_marts.fct_run`") == "nextrole_marts.fct_run"


def test_resolve_returns_candidates_when_ambiguous(snapshot):
    snapshot.tables["nextrole_staging.fct_run"] = snapshot.tables["nextrole_marts.fct_run"]
    assert resolve_table(snapshot, "fct_run") == [
        "nextrole_marts.fct_run",
        "nextrole_staging.fct_run",
    ]


def test_resolve_returns_an_empty_list_when_unknown(snapshot):
    assert resolve_table(snapshot, "nope") == []


def test_overview_states_the_privacy_boundary(snapshot):
    assert "no document bodies" in render_overview(snapshot)


def test_overview_lists_marts_with_counts_and_grain(snapshot):
    text = render_overview(snapshot)
    assert "| `fct_run` | 252 | Grain: one agent run |" in text


def test_overview_lists_staging_separately(snapshot):
    assert "`stg_app__runs`" in render_overview(snapshot)


def test_overview_surfaces_view_example_questions(snapshot):
    assert "How many users were active this week?" in render_overview(snapshot)


def test_overview_repeats_notices(dbt, clickhouse):
    snapshot = build_snapshot(dbt=dbt, cube=None, clickhouse=clickhouse)
    assert "Cube unreachable" in render_overview(snapshot)


def test_overview_points_at_the_next_step(snapshot):
    assert "describe_data(" in render_overview(snapshot)


def test_overview_ends_with_the_sql_rules(snapshot):
    """The overview is the last thing read before the first query is written.

    Repeating the two rules that actually fail here reaches a model that
    skipped the skill, at the moment it matters.
    """
    text = render_overview(snapshot)

    assert "Before you write SQL" in text
    assert "already a column" in text
    assert "clickhouse-dialect.md" in text
    # Generic form, not a fact about one column.
    assert "`sum(x) AS x`" in text


def test_table_render_leads_with_the_sort_key_advice(snapshot):
    assert "sort key: `run_date, owner` — filter on this first" in render_table(
        snapshot,
        "nextrole_marts.fct_run",
    )


def test_table_render_flattens_wrapped_prose_into_one_row(snapshot):
    text = render_table(snapshot, "nextrole_marts.fct_run")
    data_rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
    assert data_rows, "expected column rows"
    assert all(row.count("|") >= 4 for row in data_rows)


def test_table_render_escapes_pipes_in_prose(snapshot):
    assert "A \\| pipe." in render_table(snapshot, "nextrole_marts.fct_run")


def test_table_render_marks_pii_columns(snapshot):
    assert "**PII (direct).**" in render_table(snapshot, "nextrole_marts.dim_user")


def test_table_render_includes_cube_caveats_and_formulas(snapshot):
    text = render_table(snapshot, "nextrole_marts.fct_run")
    assert "> 'interrupted' means paused for approval - not a failure" in text
    assert "success_count / count" in text


def test_table_render_of_a_staging_view_has_no_row_count(snapshot):
    assert "rows: —" in render_table(snapshot, "nextrole_staging.stg_app__runs")


def test_marts_db_names_follow_the_configured_database():
    settings = WarehouseSettings(db="other")
    assert settings.marts_db == "other_marts"
    assert settings.staging_db == "other_staging"
