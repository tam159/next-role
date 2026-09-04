# analytics/CLAUDE.md

Python 3.13 analytics app executing `docs/ideas/analytics-platform-plan.html` Phase 0: dlt
extraction → ClickHouse warehouse → dbt models → Cube semantic layer → Superset dashboards,
orchestrated by Dagster. Runs as the `clickhouse` / `dagster-*` / `cube` / `superset*` compose
services; nothing here gates the app services. The product/architecture overview with screenshots
is [`README.md`](README.md); this file is the working guide.

## The golden rule — structure and metrics, never document bodies

The warehouse must never receive CV/JD/chat text, run input payloads (`run.kwargs`), store values,
or checkpoint channel state. All extraction shaping lives in
`nextrole_analytics/dlt_sources/transforms.py` (pure functions), and the contract is pinned by
`tests/test_transforms.py` + `tests/test_message_explode.py` — extend the tests **before** shipping
any new extracted field. Review `transforms.py` first on any extraction change.

## Tooling

- **Package manager**: `uv` only (`uv add`, `uv run`); never `pip install`. Second uv virtual
  project — independent lockfile from `backend/`.
- **Lint/format**: `uv run ruff check --fix` / `uv run ruff format` (line length 100, `select = ["ALL"]`
  — config in `analytics/pyproject.toml`, kept in sync with backend's ignore list).
- **Type check**: `uv run ty check` (pre-commit hook `analytics-typecheck` gates on it).
- **SQL (dbt models)**: `uv run sqlfluff format dbt/` → `uv run sqlfluff fix dbt/` → `uv run sqlfluff lint dbt/`
  (ClickHouse dialect, jinja templater with dbt stubs + project macros; config in `analytics/pyproject.toml`
  under `[tool.sqlfluff.*]`, path exclusions in `analytics/.sqlfluffignore`).
  Deliberately light: the curated `core` rule group only, lowercase keywords/identifiers, 100-char lines;
  CP03/CP05 are excluded because ClickHouse functions/types are case-sensitive camelCase. Pre-commit runs
  fix + lint on `analytics/**/*.sql`. Use `-- noqa: <RULE>` for deliberate exceptions (see fct_message's
  self-aliases) — never widen the exclude list to silence one line.
- **Tests**: `uv run pytest` from `analytics/`.
- **Version lockstep**: `dagster-*` integration libs (0.29.x) exact-pin dagster core (1.13.x) — bump
  together; `dbt-core` stays `<1.11` until `dbt-clickhouse` raises its ceiling; Python ceiling
  `<3.14` comes from dagster-dbt.

## Layout

- `nextrole_analytics/` — the Dagster code location. `dlt_sources/` (extraction + transforms),
  `assets_dlt.py` / `assets_dbt.py` / `translators.py` / `schedules.py` / `definitions.py`,
  `run_pipeline.py` (manual dlt run).
- `dbt/` — dbt project: `models/sources.yml` (bronze `raw_app___*` / `raw_auth___*`),
  `models/staging/` (views + the `stg_app__messages` first-seen ledger), `models/marts/`
  (star schema: `fct_run`, `fct_message`, `fct_thread`, `fct_session`, `dim_user`,
  `dim_assistant`), `seeds/model_prices.csv` (editable token prices → `est_cost_usd`).
- **Metadata contract** ("dbt owns tables, Cube owns metrics"): every mart column carries an
  agent-facing description in `dbt/models/marts/marts.yml` (grain, semantics, value domains,
  caveats); shared prose lives in `dbt/models/docs/shared.md` as `{% docs %}` blocks — extend a block
  rather than restating column meaning across models. Metric definitions, formulas, synonyms,
  units, example questions and caveats live on Cube members/cubes as `description` + `meta`
  (exposed verbatim by `/cubejs-api/v1/meta`). Don't write "input_tokens: input tokens" — say what
  the number means, what it excludes, and when it misleads.
- `cube/model/` — Cube cubes + views (every member described: the future analytics agent's
  vocabulary via `/cubejs-api/v1/meta`).
- `superset/` — image (drivers), `superset_config.py`, and `assets/dashboard_export/` (the seeded
  "NextRole Overview" bundle imported by the `superset-init` one-shot).
- `dagster/` — `dagster.yaml` (Postgres-backed instance) + `workspace.yaml`.
- `clickhouse/` — dev low-memory server config + per-query limits.

## Local dev loop

- **No hot reload** for pipeline code: after editing anything under `nextrole_analytics/` or
  `dbt/`, run `docker compose restart dagster-webserver dagster-daemon dbt-docs` (the boot commands
  re-run `dbt parse` for a fresh manifest and `dbt docs generate` for the docs site). Cube reloads its model dir on its own in dev mode; Superset
  bundle changes need `docker compose up -d superset-init` to re-import.
- **Trigger the whole pipeline now** (instead of waiting for the hourly schedule):
  `docker compose exec dagster-daemon dagster asset materialize --select "*" -m nextrole_analytics.definitions`
  — or click "Materialize all" in the Dagster UI.
- **Manual dlt-only run** (bronze only): `docker compose exec dagster-daemon python -m nextrole_analytics.run_pipeline`.
- **dbt by hand** (inside the container, env is preset):
  `docker compose exec dagster-daemon dbt build --project-dir /deps/next-role/analytics/dbt --profiles-dir /deps/next-role/analytics/dbt`.
- **Import-time gotcha** (mirrors the backend's config import): importing
  `nextrole_analytics.definitions` needs `ANALYTICS_SOURCE_PG_URI` set and the dbt manifest present
  (`dbt parse` first). Host-side `dagster dev` therefore needs both; the compose services guarantee
  them.
- UIs (host ports from `.env`): Dagster `http://localhost:<DAGSTER_LOCAL_PORT>/`, Superset
  `http://localhost:<SUPERSET_LOCAL_PORT>/` (login `SUPERSET_ADMIN_*`), Cube Playground
  `http://localhost:<CUBE_LOCAL_PORT>/`, dbt docs `http://localhost:<DBT_DOCS_LOCAL_PORT>/` (the
  `dbt-docs` service runs `dbt docs generate` against the warehouse at boot — no local dbt needed;
  `nextrole_analytics/dbt_docs_patch.py` then labels the artifacts' blank ClickHouse `database` so the
  Database tab groups as clickhouse → schema → table),
  ClickHouse `http://localhost:<CLICKHOUSE_HTTP_LOCAL_PORT>/play`.

## Warehouse layout

One ClickHouse database per layer: bronze in `${CLICKHOUSE_DB}` (`raw_app___*`, `raw_auth___*` —
deduped by dlt's merge write disposition, so staging reads them plain), staging views in
`${CLICKHOUSE_DB}_staging`, marts in `${CLICKHOUSE_DB}_marts` (MergeTree, ordered by (date,
owner)). dlt incremental cursors persist in the destination (`_dlt_pipeline_state`), so container
recreation never re-backfills. The transient `raw_*_staging___*` tables are dlt merge machinery —
ignore them.

## Known limitation — message event time

Source messages carry no timestamp: `stg_app__messages` stamps `first_seen_at` at capture, so
token/cost time-series are accurate to the schedule cadence going forward, while history backfilled
before the pipeline existed lumps at deploy date (`thread_created_date` is carried for re-bucketing).
Langfuse traces (a later blueprint phase) replace this with real per-message timing.

## Superset dashboard bundle

`superset/assets/dashboard_export/` is a hand-maintained Superset export bundle (databases →
datasets → charts → dashboard, cross-referenced by fixed UUIDs; connection URIs carry
`__..._URI__` placeholders that `superset-init` fills from env). To change a chart: edit it in the
Superset UI, export the dashboard, and copy the updated chart YAML back into the bundle — don't
hand-tune `params` blind.
