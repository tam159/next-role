"""Extraction assets: the two dlt pipelines (runtime + auth) as Dagster assets.

Pipeline names are shared with ``run_pipeline.py`` so manual runs and scheduled
runs read/write the same incremental-cursor state (persisted in the destination,
so it survives container recreation).

No ``from __future__ import annotations`` here: Dagster validates the
``context`` parameter's annotation at definition time and cannot resolve the
stringified form.
"""

from collections.abc import Iterator
from typing import Any

import dlt as dlt_lib
from dagster import AssetExecutionContext
from dagster_dlt import DagsterDltResource, dlt_assets

from nextrole_analytics.dlt_sources import nextrole_app_source, nextrole_auth_source
from nextrole_analytics.translators import RawDatasetTranslator

APP_PIPELINE_NAME = "app_to_clickhouse"
AUTH_PIPELINE_NAME = "auth_to_clickhouse"


def app_pipeline() -> Any:  # noqa: ANN401 - dlt.Pipeline is not exported as a stable annotation
    """Build the runtime-tables pipeline (dataset ``raw_app``)."""
    return dlt_lib.pipeline(
        pipeline_name=APP_PIPELINE_NAME,
        destination="clickhouse",
        dataset_name="raw_app",
    )


def auth_pipeline() -> Any:  # noqa: ANN401
    """Build the Better Auth pipeline (dataset ``raw_auth``)."""
    return dlt_lib.pipeline(
        pipeline_name=AUTH_PIPELINE_NAME,
        destination="clickhouse",
        dataset_name="raw_auth",
    )


@dlt_assets(
    dlt_source=nextrole_app_source(),
    dlt_pipeline=app_pipeline(),
    name="raw_app_load",
    group_name="extraction",
    dagster_dlt_translator=RawDatasetTranslator("raw_app"),
)
def raw_app_assets(context: AssetExecutionContext, dlt: DagsterDltResource) -> Iterator[Any]:
    """Load run/thread/message/checkpoint_meta/store_item/assistant into bronze."""
    yield from dlt.run(context=context)


@dlt_assets(
    dlt_source=nextrole_auth_source(),
    dlt_pipeline=auth_pipeline(),
    name="raw_auth_load",
    group_name="extraction",
    dagster_dlt_translator=RawDatasetTranslator("raw_auth"),
)
def raw_auth_assets(context: AssetExecutionContext, dlt: DagsterDltResource) -> Iterator[Any]:
    """Load user/session/account into bronze (no-op until the auth migration ran)."""
    yield from dlt.run(context=context)
