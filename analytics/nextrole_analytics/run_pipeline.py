"""Manual one-shot extraction, bypassing Dagster — for verification and debugging.

Run inside the analytics container (env is already wired there):

    docker compose exec dagster-daemon python -m nextrole_analytics.run_pipeline

Shares pipeline names (and therefore incremental-cursor state) with the
scheduled Dagster assets.
"""

from __future__ import annotations

from nextrole_analytics.assets_dlt import app_pipeline, auth_pipeline
from nextrole_analytics.dlt_sources import nextrole_app_source, nextrole_auth_source


def main() -> None:
    """Run both extraction pipelines once and print their load summaries."""
    app_info = app_pipeline().run(nextrole_app_source())
    print(app_info)  # noqa: T201
    auth_info = auth_pipeline().run(nextrole_auth_source())
    print(auth_info)  # noqa: T201


if __name__ == "__main__":
    main()
