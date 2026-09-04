"""The Dagster code location: dlt extraction + dbt transformation, one schedule.

Import-time requirements (mirrors the backend's config-import gotcha):
``ANALYTICS_SOURCE_PG_URI`` must be set (the dlt sources are built here) and the
dbt manifest must exist at ``DBT_MANIFEST_PATH`` — both are guaranteed by the
compose services' environment/command; for host-side ``dagster dev`` see
analytics/CLAUDE.md.
"""

from __future__ import annotations

import os

from dagster import Definitions
from dagster_dbt import DbtCliResource
from dagster_dlt import DagsterDltResource

from nextrole_analytics.assets_dbt import DBT_PROJECT_DIR, dbt_analytics_assets
from nextrole_analytics.assets_dlt import raw_app_assets, raw_auth_assets
from nextrole_analytics.schedules import analytics_job, analytics_schedule

defs = Definitions(
    assets=[raw_app_assets, raw_auth_assets, dbt_analytics_assets],
    resources={
        "dlt": DagsterDltResource(),
        "dbt": DbtCliResource(
            project_dir=os.fspath(DBT_PROJECT_DIR),
            profiles_dir=os.fspath(DBT_PROJECT_DIR),
        ),
    },
    jobs=[analytics_job],
    schedules=[analytics_schedule],
)
