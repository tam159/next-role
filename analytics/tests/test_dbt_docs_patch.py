"""Unit tests for the dbt docs Database-tab patch."""

import json

from nextrole_analytics.dbt_docs_patch import patch_artifacts


def _write(path, payload):
    path.write_text(json.dumps(payload))


def test_patch_fills_blank_database_in_both_artifacts(tmp_path):
    _write(
        tmp_path / "manifest.json",
        {
            "nodes": {
                "model.p.fct_run": {"database": "", "schema": "nextrole_marts"},
                "test.p.unique_x": {"database": "", "schema": "nextrole_marts"},
            },
            "sources": {"source.p.raw_app.run": {"database": "", "schema": "nextrole"}},
            "macros": {"macro.p.m": {"name": "m"}},
        },
    )
    _write(
        tmp_path / "catalog.json",
        {
            "nodes": {
                "model.p.fct_run": {"metadata": {"database": "", "schema": "nextrole_marts"}},
            },
            "sources": {
                "source.p.raw_app.run": {"metadata": {"database": "", "schema": "nextrole"}},
            },
        },
    )

    patched = patch_artifacts(tmp_path, "clickhouse")

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    catalog = json.loads((tmp_path / "catalog.json").read_text())
    assert patched == 5
    assert manifest["nodes"]["model.p.fct_run"]["database"] == "clickhouse"
    assert manifest["sources"]["source.p.raw_app.run"]["database"] == "clickhouse"
    assert catalog["nodes"]["model.p.fct_run"]["metadata"]["database"] == "clickhouse"
    assert catalog["sources"]["source.p.raw_app.run"]["metadata"]["database"] == "clickhouse"
    assert manifest["macros"] == {"macro.p.m": {"name": "m"}}


def test_patch_leaves_populated_database_alone(tmp_path):
    _write(tmp_path / "manifest.json", {"nodes": {"m": {"database": "analytics", "schema": "s"}}})
    _write(tmp_path / "catalog.json", {"nodes": {"m": {"metadata": {"database": "analytics"}}}})

    assert patch_artifacts(tmp_path, "clickhouse") == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["nodes"]["m"]["database"] == "analytics"
