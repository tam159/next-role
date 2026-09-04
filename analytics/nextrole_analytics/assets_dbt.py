"""Transformation assets: the dbt project (staging → marts) as Dagster assets.

The manifest is parsed *before* the dagster process starts (the compose command
runs ``dbt parse --target-path /tmp/dbt-target``; ``DBT_MANIFEST_PATH`` points
at the result). For host-side ``dagster dev``, run the same ``dbt parse`` first
— importing this module fails loudly if the manifest is missing.

No ``from __future__ import annotations`` here: Dagster validates the
``context`` parameter's annotation at definition time and cannot resolve the
stringified form.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from dagster import AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets

DBT_PROJECT_DIR = Path(__file__).resolve().parent.parent / "dbt"
DBT_MANIFEST_PATH = Path(
    os.environ.get("DBT_MANIFEST_PATH", str(DBT_PROJECT_DIR / "target" / "manifest.json")),
)


@dbt_assets(manifest=DBT_MANIFEST_PATH)
def dbt_analytics_assets(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    """``dbt build`` — seeds, models, and tests in dependency order."""
    yield from dbt.cli(["build"], context=context).stream()
