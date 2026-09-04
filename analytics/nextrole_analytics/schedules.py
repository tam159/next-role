"""The one job + schedule that materializes the whole asset graph.

Hourly keeps dashboards ≤1h stale — which is also the resolution of the
message first-seen event-time ledger (see analytics/CLAUDE.md). The schedule
defaults to RUNNING so a fresh ``docker compose up`` needs no UI toggling.
"""

from __future__ import annotations

from dagster import AssetSelection, DefaultScheduleStatus, ScheduleDefinition, define_asset_job

analytics_job = define_asset_job(name="analytics_all", selection=AssetSelection.all())

analytics_schedule = ScheduleDefinition(
    job=analytics_job,
    cron_schedule="7 * * * *",
    default_status=DefaultScheduleStatus.RUNNING,
)
