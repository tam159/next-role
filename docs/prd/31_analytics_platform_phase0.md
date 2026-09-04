---
type: PRD
title: "Analytics Platform Phase 0 — warehouse, pipeline, semantic layer, BI"
description: "A compose-native analytics stack — dlt into ClickHouse, dbt marts, Cube metrics, Superset, orchestrated hourly by Dagster — that lands structure and metrics from the agent's Postgres (never document bodies) and ships a seeded users/conversations/runs/cost dashboard plus an agent-facing metadata dictionary."
tags: [infra, storage, llm]
timestamp: '2026-09-04T10:40:00+07:00'
status: "shipped"
scope: "analytics/ + docker-compose (ClickHouse · Dagster · dbt · Cube · Superset)"
version: v1
---

# Why

NextRole had no way to see itself: who signs up, how much the agent runs, how often runs fail or stall on approvals, what the LLM spend is per model or user. Everything analytically interesting sat inside JSONB columns of the LangGraph runtime (`thread.values.messages[].usage_metadata`, `run.kwargs`, `checkpoints.metadata`) next to PII-dense msgpack blobs — unqueryable in place and unsafe to point a BI tool (or an agent) at. The [analytics blueprint](../ideas/analytics-platform-plan.html) settled the stack in one pass — open source, one engine (ClickHouse) at every self-run scale, nothing to migrate later — and this PRD executes its Phase 0: the services, the first ELT pipeline, and a dashboard that answers the day-one questions. It also lays the metadata foundation the blueprint's Phase 0.5 analytics agent will read.

# What the user sees

`docker compose up -d` now also brings up the analytics stack: `clickhouse`, `dagster-webserver` + `dagster-daemon`, `cube`, `superset` + `superset-worker`, `dbt-docs`, and two idempotent one-shots (`analytics-db-init`, `superset-init`). After one materialisation (`Materialize all` in Dagster, or the documented `dagster asset materialize` exec) the hourly schedule keeps it fresh. Four UIs, ports from `.env`: the Superset **NextRole Overview** dashboard (14 charts — users, signups, active users, threads and runs per day, run status mix, p50/p95 duration, tokens and estimated cost by model, messages by role, per-user table), the Dagster asset graph (25 assets: 9 dlt extraction assets → 9 staging views → 6 marts + seed, with dbt tests as asset checks), the Cube Playground (6 cubes, 3 views, every member described), and the generated dbt docs site. The new top-level `analytics/` app holds all of it; `analytics/README.md` is the overview, `analytics/CLAUDE.md` the working guide.

Deliberately absent: no PostHog container or event instrumentation (deferred by decision), no Makefile (compose services + documented `docker compose exec/restart` commands are the repo convention), no hot reload for pipeline code (restart the Dagster services and `dbt-docs`), and Superset's seeded charts do **not** go through Cube — see Decisions.

# How — the key architectural choices

**A second uv virtual project and one image for the whole pipeline.** `analytics/` mirrors `backend/`'s pattern (python-slim two-stage, uv-locked venv at `/opt/venv`, whole-repo bind mount with an anonymous `.venv` mask) and the `next-role-analytics:local` image runs dlt, dbt and both Dagster processes; each container `dbt parse`s its own manifest into `/tmp/dbt-target` at boot so the bind mount is never written. A tiny translator keys dlt assets as `[dataset, resource]`, which equals dbt's default source keys — so extraction → staging → marts is one connected lineage graph with no dbt-side translator. Rejected: reusing the backend image (would drag dagster/dbt/dlt into the agent's dependency set and couple two release cadences).

**Extraction is the privacy boundary.** The blueprint's doctrine — structure and metrics, never document bodies — is enforced in `nextrole_analytics/dlt_sources/transforms.py` as pure functions with unit tests pinning the contract: message content, `run.kwargs` input, store values and checkpoint channel state are never yielded; email → sha256 + domain, IP → /24, OAuth secrets never selected, `store.value` reduced to `length()` in SQL so it never crosses the wire. The pipeline connects through a read-only `analytics_ro` Postgres role that `analytics-db-init` creates idempotently on every `up` (the repo's `init.sql` only runs on a fresh volume, and the dev volume already existed). Rejected: dlt's `sql_database` source over whole tables with column-drop hints downstream — payloads would still transit the extractor and land in dlt's staging tables.

**dbt owns tables, Cube owns metrics; Superset reads the marts.** Marts are a star around `fct_run` (`fct_message`, `fct_thread`, `fct_session`, `dim_user`, `dim_assistant`, `model_prices`), each column described for an agent reader with shared prose in `{% docs %}` blocks; Cube defines measures, joins, views and `meta` hints (synonyms, units, formulas, example questions, caveats) served verbatim by `/cubejs-api/v1/meta` and a Postgres-wire SQL API. The seeded dashboard is a version-controlled Superset export bundle imported at boot through `ImportAssetsCommand`, with charts on the ClickHouse marts directly and the Cube connection registered for SQL Lab — the robust path for hand-authored bundles, while the metric definitions the future agent consumes still live in exactly one place.

# Files of interest

| Concern | Path |
|---|---|
| Compose services (9) + volumes | `docker-compose.yml` (`clickhouse` … `superset-worker`, the `Analytics stack` block) |
| Env surface (`CLICKHOUSE_*`, `DAGSTER_*`, `DBT_DOCS_*`, `CUBE_*`, `SUPERSET_*`, `ANALYTICS_RO_PASSWORD`) | `.env.example`, README "Environment variables" → **Analytics** |
| Extraction privacy gate + tests | `analytics/nextrole_analytics/dlt_sources/transforms.py`, `analytics/tests/test_transforms.py`, `analytics/tests/test_message_explode.py` |
| dlt sources (runtime + Better Auth, inspector-gated) | `analytics/nextrole_analytics/dlt_sources/app_source.py`, `auth_source.py` |
| Dagster code location, dlt/dbt assets, hourly schedule | `analytics/nextrole_analytics/definitions.py`, `assets_dlt.py`, `assets_dbt.py`, `translators.py` (`RawDatasetTranslator`), `schedules.py` |
| dbt project: sources, staging, marts, seed, doc blocks, source guard | `analytics/dbt/models/sources.yml`, `models/staging/`, `models/marts/`, `seeds/model_prices.csv`, `models/docs/shared.md`, `macros/source_relation_exists.sql` |
| Message first-seen ledger | `analytics/dbt/models/staging/stg_app__messages.sql` |
| Cube cubes + views with agent `meta` | `analytics/cube/model/cubes/*.yml`, `views/*.yml` |
| Superset image, config, assets-import, dashboard bundle | `analytics/superset/Dockerfile`, `superset_config.py`, `import_assets.py`, `assets/dashboard_export/` |
| dbt docs Database-tab fix | `analytics/nextrole_analytics/dbt_docs_patch.py` |
| Warehouse dev tuning | `analytics/clickhouse/config.d/low-memory.xml`, `users.d/query-limits.xml` |
| Tooling: ruff/ty/pytest/sqlfluff config, hooks, CI | `analytics/pyproject.toml`, `.pre-commit-config.yaml` (`analytics-*` hooks), `.github/workflows/analytics-tests.yml`, `ci.yml`, `hygiene.yml` |

# Decisions worth remembering

- **PostHog deferred, per product decision.** The blueprint's Phase 0 includes event instrumentation; the owner chose to ship the warehouse + dashboard first because the day-one questions (users, threads, runs, cost) come from operational data. PostHog Cloud (the blueprint's pick — no container) plus the event taxonomy is its own feature.
- **Seeded charts read the marts, not Cube's SQL API.** A hand-authored import bundle over Cube's pg-wire needs `MEASURE()` pushdown semantics per chart and breaks silently on import; ClickHouse datasets are plain columns + SQL. The doctrine survives because metrics are *defined* in Cube (and the agent will query Cube), not because BI is forced through it.
- **`superset import-dashboards` never overwrites charts.** The CLI treats charts/datasets/databases as dependencies and imports them once by UUID, which broke bundle iteration; `import_assets.py` calls `ImportAssetsCommand` ("will overwrite everything") with `metadata.yaml` `type: assets`. Also learned the hard way: chart `params` must be a YAML mapping, dashboard position nodes need a numeric placeholder `chartId` next to `uuid`, and a restarted one-shot keeps `/tmp` — `rm -rf /tmp/bundle` before `cp -r` or the copy nests into stale files.
- **Superset preset ranges exclude today.** "Last week"/"Last month" resolve to whole days ending at today-midnight, which hid all day-one data (everything is stamped "today" by the first-seen ledger). KPIs use rolling `DATEADD(DATETIME("now"), -N, day) : now`.
- **Bronze is plain MergeTree deduped by dlt's merge disposition — no `FINAL`.** The blueprint's ReplacingMergeTree intent didn't survive contact: dlt's `clickhouse_adapter` engine hint had no effect and `FINAL` on MergeTree is illegal. dlt's delete+insert merge already yields one row per key.
- **dlt only materialises columns it has seen values for.** All-NULL shaped fields (`main_model_override` on every historical run, `ip_prefix`) silently never became columns and dbt failed to compile. Every optional shaped field now carries an explicit `columns=` hint; the hints apply only when rows flow, so an incremental no-op won't ALTER an existing table (a full re-backfill was needed once).
- **Two ClickHouse SQL traps in the marts.** LEFT JOIN misses fill non-Nullable columns with type defaults (`1970-01-01`, `false`) instead of NULL — `dim_user` guards them with `if(u.key = o.key, u.col, null)`. And an unaliased qualified column that also exists on a joined relation is created literally as `m.message_key` — `fct_message` aliases those two explicitly, marked `-- noqa: AL09` because `sqlfluff fix` would strip the "self-alias" and re-break the mart.
- **Message event time is a first-seen ledger.** Source messages carry no timestamp; `stg_app__messages` is insert-only and stamps `first_seen_at` at capture. Accurate to the hourly cadence going forward, while pre-deployment history lumps at the first backfill (`thread_created_date` is carried for re-bucketing). Joining messages to run windows was rejected as still wrong for human turns and forks.
- **Token and cost figures are a floor (~10% coverage) — recorded in the metadata, fixed elsewhere.** Data exploration showed `usage_metadata` on 76/768 AI messages: OpenAI Responses-API ids (`resp_…`) always carry it, streamed chunk-merged ones (`lc_run--…`) almost never. Every token/cost description says so; the remedy (`stream_usage=True` on the chat models) is a backend change owned by a separate session.
- **The model-override key is `main_agent_model`, not `main_model`.** Found while grounding descriptions in data: the middleware from [Configurable LLM Models](15_configurable_llm_models.md) reads `configurable.main_agent_model` / `subagent_model`; the extractor was reading a key that never exists. No run has set an override yet, so no data changed — but the column would have stayed NULL forever.
- **Metadata contract: descriptions must say what a number means, excludes, and when it misleads — never echo the column name.** Shared semantics (owner, statuses, usage coverage, first-seen, cost) live once in `dbt/models/docs/shared.md`; Cube members carry `description` + `meta`. The `owner` join semantics come from [Multi-User Authentication](26_multi_user_auth.md) (`metadata.owner`, `'default'` = single-user mode) and "`interrupted` is an approval pause, not a failure" from [HiL Approval for execute](29_execute_tool_hitl_approval.md).
- **sqlfluff: curated `core` rules only, config in `pyproject.toml`.** The owner wanted format-level consistency without a lint treadmill: `rules = core`, `exclude_rules = CP03, CP05` (ClickHouse functions/types are case-sensitive camelCase), jinja templater with dbt builtins + project macros, 100-char lines. Config moved from `.sqlfluff` into `[tool.sqlfluff.*]` to consolidate; `.sqlfluffignore` stays because `exclude_paths` is not a real option (unknown keys are ignored silently and a directory sweep hit `dbt/target`).
- **dbt docs as a compose service, with a Database-tab patch.** Chosen over a Makefile `docker compose run --rm` so viewing needs no local command; regenerates against the warehouse at boot. dbt-clickhouse leaves `database` blank on every relation, which renders the docs' Database tab empty — `dbt_docs_patch.py` labels the artifacts `clickhouse` between `generate` and `serve`.
- **Dagster asset modules must not use `from __future__ import annotations`** (the `context` annotation validator can't resolve strings), the local `analytics/dagster/` config dir makes isort misclassify the `dagster` package as first-party (`known-third-party`), and `superset-worker` needs a Celery-ping healthcheck because it inherits the image's web-port probe.

# Deferred (intentional non-goals for v1)

- **Product analytics (PostHog Cloud + the ~15-event taxonomy, `training_consent`).** Its own feature; the activation funnel needs it.
- **LLM trace store (Langfuse).** Also the proper fix for both the token-coverage floor and message event time; blueprint Phase 1.
- **Token usage on streamed responses (`stream_usage=True`).** Backend change, owned separately.
- **The analytics agent (blueprint Phase 0.5)** over Cube's REST API with per-user security context; Cube runs in dev mode (no JWT/`queryRewrite`) until then, and ClickHouse row policies wait for human warehouse logins.
- **elementary** (test history, anomaly tests) and **OpenMetadata** — when there is an audience for them.
- **Artifact inventory from object storage.** `fct_thread.file_count` is ~0 because artifacts live in the store from [Object Storage for Binary Artifacts](25_object_storage_artifacts.md); a daily listing resource would give real output metrics.
- **Generating Cube dimensions from the dbt manifest (`cube_dbt`)** — only if column-description drift between the two becomes a cost.
- **Making `analytics-tests` a required CI check.** The workflow exists (dorny/paths-filter + job-level `if`); branch protection is the owner's call.

# How to verify end-to-end

1. `cp .env.example .env`, fill the three `generate:` secrets in the analytics block, `docker compose up -d --build`. All new services report healthy; `analytics-db-init` and `superset-init` exit 0 (`docker compose ps`).
2. `docker compose exec dagster-daemon dagster asset materialize --select "*" -m nextrole_analytics.definitions` → ends with `RUN_SUCCESS` and dbt `PASS=54`. Re-run: identical counts (idempotent).
3. In ClickHouse (`/play` on `CLICKHOUSE_HTTP_LOCAL_PORT`): `raw_app___run`/`raw_app___thread`/`raw_app___checkpoint_meta` row counts equal Postgres `run`/`thread`/`checkpoints`; `raw_app___message` has no content column; `nextrole_marts.fct_run` status mix matches `SELECT status, count(*) FROM run GROUP BY 1`.
4. Dagster UI: 25 assets in one connected graph (`raw_app/* → staging/* → marts/*`), asset checks green, `analytics_all_schedule` RUNNING at `7 * * * *`.
5. Cube: `curl :CUBE_LOCAL_PORT/readyz`; `/cubejs-api/v1/meta` lists 6 cubes + 3 views with `description` and `meta`; via psql on `CUBE_SQL_LOCAL_PORT`: `SELECT MEASURE(count) FROM runs` equals the run count.
6. Superset (`admin`/`admin`): the **NextRole Overview** dashboard renders all 14 charts with non-zero users/runs/cost; both database connections exist; re-running `docker compose up -d superset-init` re-imports without duplicating.
7. dbt docs on `DBT_DOCS_LOCAL_PORT`: Project tab shows marts/staging/seeds; Database tab shows `clickhouse → nextrole / nextrole_staging / nextrole_marts`; `fct_message` columns all carry descriptions.
8. `cd analytics && uv run pytest && uv run ruff check . && uv run ty check && uv run sqlfluff lint dbt/` — all green; `pre-commit run --all-files` runs the `analytics-typecheck` and `analytics-sqlfluff-*` hooks.
