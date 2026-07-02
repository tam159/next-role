# Backend Architecture — the vendored LangGraph Agent Server

NextRole's backend is a **vendored reimplementation of `langgraph-api` 0.10.0** (the server
behind LangGraph Platform), running the career agent in-process. It replaced the closed
`langchain/langgraph-api:3.13` base image so the project owns every line it runs. This document
explains what lives where, how the pieces talk to each other, and what to know before touching
any of it. File references are given as `path:line` so you can verify.

## Table of contents

1. [TL;DR & topology](#1-tldr--topology)
2. [What is vendored (and what was changed)](#2-what-is-vendored-and-what-was-changed)
3. [The two-plane design](#3-the-two-plane-design)
4. [The run queue](#4-the-run-queue)
5. [Streaming](#5-streaming)
6. [Schema & migrations](#6-schema--migrations)
7. [Configuration knobs](#7-configuration-knobs)
8. [Known gaps vs the official image](#8-known-gaps-vs-the-official-image)
9. [Production sketch](#9-production-sketch)
10. [Vendoring provenance & upgrade path](#10-vendoring-provenance--upgrade-path)

---

## 1. TL;DR & topology

One image (`backend/Dockerfile`, `python:3.13-slim` + uv), two compose services:

- **`backend`** — `uvicorn langgraph_api.server:app` on container **:8000** (host
  `${LANGGRAPH_LOCAL_PORT}`). HTTP + SSE + WebSocket API, auth (noop locally), validation,
  **and graph execution**: an embedded worker pool (`N_JOBS_PER_WORKER`, default 10) claims
  queued runs and executes `career_agent` in-process. Stateless.
- **`core-server`** — `python -m core_server`, gRPC on **:50052** (internal only, no host
  port). The **data plane**: owner of all `assistant` / `thread` / `run` / `cron` SQL, the
  atomic run-queue claim, and the Redis pub/sub fan-out. The backend refuses to finish boot
  until this is reachable (`langgraph_runtime_postgres/lifespan.py` gathers
  `wait_until_grpc_ready()`).
- **`postgres`** (pgvector/pg18) — durable system of record: assistants, threads, runs,
  checkpoints, the KV `store` (DeepAgents' StoreBackend), crons.
- **`redis`** — queue doorbell, streaming bus, control signals, caches/locks. Holds **no
  durable truth**: wiping it loses in-flight streams, never data.

```mermaid
graph TB
    FE["frontend (Next.js)<br/>:${FRONTEND_LOCAL_PORT} → 3000"]
    subgraph image["next-role-backend:local (one image, two commands)"]
        BE["backend<br/>uvicorn langgraph_api.server:app :8000<br/>HTTP · SSE · WS · runs career_agent"]
        CS["core-server<br/>python -m core_server :50052<br/>gRPC data plane"]
    end
    PG[("postgres (pgvector/pg18)<br/>system of record")]
    RD[("redis<br/>doorbell · pub/sub · locks")]
    FE -->|"HTTP/SSE/WS :${LANGGRAPH_LOCAL_PORT}"| BE
    BE -->|gRPC| CS
    BE -. "checkpoints · store · state/history<br/>(direct psycopg)" .-> PG
    CS -->|SQL| PG
    CS --> RD
    BE -. "queue doorbell · stream subscribe" .-> RD
```

The frontend talks to the backend **directly** (no Next.js proxy):
`@langchain/langgraph-sdk` `Client` for REST, `@langchain/react` `useStream` for v2 streaming.

**Hot reload:** the `backend` service runs uvicorn `--reload` over the bind-mounted source, so
edits under `backend/` (first-party `agents/` and vendored server code alike) restart the
server. **`core-server` does not hot-reload** — after editing `backend/core_server/` or
`backend/langgraph_grpc_common/`, run `docker compose restart core-server`.

## 2. What is vendored (and what was changed)

| Package | LOC (approx) | Role |
|---|---|---|
| `langgraph_api/` | 36k | The ASGI server: routes (assistants/threads/runs/store/crons/mcp/a2a), auth, streaming, graph loading, worker, gRPC client |
| `langgraph_runtime/` | 85 | Edition router — `__init__.py` reads `LANGGRAPH_RUNTIME_EDITION` and aliases `langgraph_runtime.*` submodules to the chosen backend in `sys.modules` |
| `langgraph_runtime_postgres/` | 3.5k | Postgres backend: pool + migrations, checkpoint ingestion, queue loop, store, lifespan |
| `langgraph_grpc_common/` | 5.6k | Generated protobuf/gRPC stubs (`proto/`, **do not edit or lint** — see §10) + proto↔python conversion |
| `core_server/` | 2.5k | Python data plane (reimplements the official image's Go `core-api-grpc` sidecar); imports only `langgraph_grpc_common` |

Plus `storage/migrations/` (60 versioned SQL migrations + 2 `.lite` variants), `logging.json`
(uvicorn log config; references `langgraph_api.logging.Formatter`), and `openapi.json` —
**read at import time** from the directory containing `langgraph_api/`
(`langgraph_api/validation.py:13`, `Path(__file__).parent.parent / "openapi.json"`). Moving
either the package or the file breaks server startup.

**Deliberate deviations from upstream 0.10.0** (keep this list current):

1. **License machinery removed.** `langgraph_license` (an always-True stub in the source
   project) is not vendored; its call sites were excised in `langgraph_api/metadata.py`
   (`PLAN = "enterprise"` constant) and `langgraph_runtime_postgres/lifespan.py` (boot check
   and periodic task deleted). NextRole is open source; nothing checks licenses.
2. **Example graphs dropped.** Upstream's `langgraph_api/react_agent/` and `mcp_agent/` demo
   graphs (and their `langchain-community` / `langchain-mcp-adapters` deps) are not vendored.
3. **Mechanical normalization.** `ruff format` at 100 cols + autofixes repo-wide, except
   `langgraph_grpc_common/proto/` which stays byte-pristine (§10).
4. **No Go binary, no `entrypoint.sh`.** The official image starts a Go `core-api-grpc`
   in-process on :50051; here the Python `core_server` runs as a separate compose service on
   :50052 with `CORE_SERVER_GO_FALLBACK=""` (fully native, no forwarding).
5. **`/info` host-metadata constants restored.** The source project's telemetry strip
   removed `REVISION` / `HOST_REVISION_ID` / `DEPLOYMENT_TYPE` / `TENANT_ID` from
   `langgraph_api/metadata.py` while `api/meta.py` still reads them, so `/info` (and
   Prometheus labels) raised `AttributeError`. Re-defined env-driven (all `None`
   self-hosted); consider upstreaming to the source project.

## 3. The two-plane design

Everything pivots on `langgraph_api/feature_flags.py`: `LANGGRAPH_RUNTIME_EDITION=postgres`
makes `IS_POSTGRES_OR_GRPC_BACKEND` true, and every HTTP handler then imports its ops layer
from `langgraph_api.grpc.ops` (thin gRPC clients) instead of in-process ops. core-server is
the only component issuing SQL against the metadata tables.

The split is deliberately **partial** — two data classes bypass gRPC and hit Postgres
directly from the backend process (`langgraph_runtime_postgres/database.py`,
`connect(supports_core_api=...)`):

1. **Checkpoints** (graph state snapshots) — written on the hot path of every superstep;
   the extra gRPC hop would double serialization on the highest-volume writes.
2. **The KV store** (`/store/*`, DeepAgents memory) and **thread state/history reads**.

Why a separate data plane at all: bounded Postgres connections (N backend replicas share a
few core-server pools instead of N pools), and one owner for correctness-critical logic —
the atomic `FOR NO KEY UPDATE SKIP LOCKED` run claim, assistant versioning, joint
run+thread status transitions.

## 4. The run queue

**"Queue of record in Postgres, doorbell in Redis."** A run is a row in `run` with
`status='pending'` — created in the same transaction that flips the thread to `busy`.
Workers never poll in a tight loop:

1. The backend's queue loop (`langgraph_runtime_postgres/queue.py`) waits for a free
   concurrency slot, then calls `Runs.Next(wait=True, limit=free_slots)` over gRPC.
2. core-server tries an immediate claim: `UPDATE run SET status='running' ... WHERE status =
   'pending' ... FOR NO KEY UPDATE SKIP LOCKED` against a **partial index of pending rows
   only** — an index-only scan of a nearly-empty index.
3. Nothing pending → it parks on `BLPOP run:queue` (Redis) for up to 5 s. `Runs.Create`
   rings the doorbell with `LPUSH` — one parked worker wakes instantly.

Durability comes from Postgres (a `pending` row survives any crash), fairness and
exactly-one-claimer from `SKIP LOCKED`, latency from the Redis doorbell. Losing Redis
degrades queue latency to the 5 s timeout; no runs are lost.

Retries: a retriable failure re-pends the run; attempts count in Redis
(`BG_JOB_MAX_RETRIES`, default 3). Per-run wall clock: `BG_JOB_TIMEOUT_SECS` (default 24 h).
Graceful drain on SIGTERM: `BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS` (default 180).

## 5. Streaming

The process that produces tokens (a worker that claimed the run) is not necessarily the
process holding the client's connection — so events rendezvous through Redis pub/sub on
per-run channels (`thread:{tid}:run:{rid}:stream`), fronted by core-server:

worker → gRPC `Runs.Publish` → core-server → Redis `PUBLISH` → core-server (subscriber
stream) → backend → SSE/WebSocket to the browser.

The frontend uses the **v2 event-streaming protocol** (`@langchain/react` `useStream`):
`POST|WebSocket /threads/{thread_id}/stream/events` + `POST /threads/{thread_id}/commands`,
mounted in `langgraph_api/api/event_streaming.py` behind `FF_V2_EVENT_STREAMING` (default
`"true"`). The wire format is `langchain-protocol` (pinned `>=0.0.18` to match the frontend
SDK's bundled version — it is pre-1.0; keep the two in lockstep). Cancellation travels the
same bus on a `:control` channel with a 60 s `SET` to cover the subscribe race.

Meta endpoints: `/ok` (LB health; also pings core-server gRPC health), `/info` (version +
flags), `/metrics` (Prometheus), `/docs` + `/openapi.json`, plus `/mcp` and
`/a2a/{assistant_id}`.

## 6. Schema & migrations

12 tables; the important ones: `assistant`(+`assistant_versions`), `thread` (latest
materialized `values` for fast reads), `run` (**also the queue**), `checkpoints` /
`checkpoint_blobs` / `checkpoint_writes` (state snapshots with `parent_checkpoint_id`
lineage → time travel), `store` (cross-thread KV — DeepAgents memory), `cron`,
`thread_ttl`, `checkpoint_delete_queue`, `schema_migrations`.

**Migrations are owned by this repo** (`backend/storage/migrations/`, versions 000001–000060)
and applied by the **backend at boot** under a Redis lock
(`langgraph_runtime_postgres/database.py` — `CREATE TABLE IF NOT EXISTS schema_migrations`,
skip every `version <= MAX(version)`, apply the rest, one row per version). A DB that is
already at or beyond the shipped max is a clean no-op — which is exactly what happened at
cutover: the official 0.10.0 image had already applied the same 60 versions, so the swap
touched nothing. core-server never migrates; it assumes the schema.

The `.lite` variants of 000017/000029 apply when `LANGGRAPH_POSTGRES_EXTENSIONS=lite`
(default `standard`). `backend/init.sql` only enables the pgvector extension on first
volume creation.

Most foreign keys are deliberately dropped by the later migrations (write-path lock
avoidance, independent GC); referential integrity is app-enforced by core-server.

## 7. Configuration knobs

All read in `langgraph_api/config/__init__.py` unless noted. The compose file sets the
starred ones.

| Env var | Default | Meaning |
|---|---|---|
| `LANGGRAPH_RUNTIME_EDITION` ★ | — (**required**; router raises) | `postgres` selects the gRPC-backed runtime (`langgraph_runtime/__init__.py`) |
| `DATABASE_URI` / `POSTGRES_URI` ★ | — (**required**) | `DATABASE_URI` wins, falls back to `POSTGRES_URI`; plain `postgresql://` DSN |
| `REDIS_URI` ★ | — (**required at import**) | importing `langgraph_api.config` fails without it (also true for pytest importing server modules) |
| `LSD_GRPC_SERVER_ADDRESS` ★ | `localhost:50052` | where the backend dials core-server |
| `MIGRATIONS_PATH` ★ | `/storage/migrations` (official-image layout) | must point at `backend/storage/migrations` |
| `LANGSERVE_GRAPHS` ★ | — | JSON `{graph_id: "path.py:variable"}`; any source containing `/` is loaded as a file path (`langgraph_api/graph.py`) |
| `N_JOBS_PER_WORKER` | `10` | embedded worker concurrency; `0` = web-only, no queue |
| `FF_CRONS_ENABLED` | `true` | cron scheduler in this process (keep exactly one) |
| `FF_V2_EVENT_STREAMING` | `true` | v2 `/stream/events` + `/commands` routes |
| `CORS_ALLOW_ORIGINS` | `*` | fine for local dev; tighten for any shared deployment |
| `LANGGRAPH_AUTH_TYPE` | `noop` | custom auth backends live in `langgraph_api/auth/` |
| `CORE_SERVER_BIND` ★ | `0.0.0.0:50052` | core-server listen address (`core_server/settings.py`) |
| `CORE_SERVER_GO_FALLBACK` ★ | `localhost:50051` | **must be `""`** — otherwise unimplemented RPCs are forwarded to a Go binary that doesn't exist here |
| `CORE_SERVER_POSTGRES_URI` / `CORE_SERVER_REDIS_URI` ★ | derived | core-server's own connections |
| `BG_JOB_MAX_RETRIES` / `BG_JOB_TIMEOUT_SECS` / `BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS` | 3 / 86400 / 180 | run retry/timeout/drain budget |

`LANGSMITH_LANGGRAPH_DESKTOP` is gone: in the official image it only toggled uvicorn
`--reload` in the entrypoint script; the compose `command` now passes `--reload` explicitly.

## 8. Known gaps vs the official image

Real properties of this codebase — accepted for a single-user local deployment, listed so
nobody is surprised later:

1. **No resumable streams / event replay.** Streaming is live pub/sub only; a late joiner
   or `Last-Event-ID` reconnect is not back-filled (final state still recoverable from the
   DB). Biggest behavioral deviation from LangGraph Platform.
2. **No orphan-run sweeper.** A hard-killed worker (OOM/SIGKILL) leaves its run stuck in
   `status='running'` forever; core-server's `Sweep` RPC is a no-op. Mitigation if it ever
   matters: periodically re-pend runs `running` longer than a threshold.
3. **Cron scheduling is not multi-scheduler safe** (`Crons.Next` has no `SKIP LOCKED`) —
   run exactly one process with `FF_CRONS_ENABLED=true`. Locally that's the single
   `backend` service.
4. **Referential integrity is app-enforced** (FKs dropped by design); a data-plane bug can
   orphan rows silently.
5. **The backend is not Postgres-free** — checkpoints/store/state bypass gRPC, so backend
   replicas also consume DB connections (matters only when scaling out, §9).

## 9. Production sketch

Locally, one `backend` container fuses web + workers + cron. To scale (same image, different
commands/env):

- **api-web** — uvicorn, `N_JOBS_PER_WORKER=0`, `FF_CRONS_ENABLED=false`; scale on
  connections/CPU.
- **api-worker** — `python -m langgraph_api.queue_entrypoint`, `N_JOBS_PER_WORKER=K`;
  scale on pending-run backlog (runs are LLM-I/O-bound — push K up before adding pods).
- **cron** — one replica.
- **core-server** — 2–3 replicas behind a headless service with client-side gRPC LB; its
  ceiling is Postgres connections, not RPS. Put PgBouncer (transaction mode) in front of
  Postgres and budget: `core_replicas×pool + worker_replicas×ckpt_pool + web_replicas×state_pool`.
- Set worker `terminationGracePeriodSeconds ≥ BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS`.

Serverless containers (Cloud Run / Fargate / Container Apps) fit this workload with no code
changes; FaaS does not (long runs, persistent gRPC, SSE).

## 10. Vendoring provenance & upgrade path

- **Source:** local project `langgraph_clone0100` — a reconstruction of `langgraph-api`
  **0.10.0** (`/info` of the previously-running official image reported the same version;
  the DB was already at migration 60, so cutover applied nothing).
- **Vendored:** 2026-07-02, branch `feat/vendor-langgraph-server`. The first vendor commit
  is a byte-verbatim snapshot (style hooks skipped) — diff against it to see every local
  change.
- **Generated code:** `langgraph_grpc_common/proto/` is protoc/grpcio-tools output. It is
  excluded from ruff (`[tool.ruff] exclude`) **and** from the pre-commit ruff hooks
  (`.pre-commit-config.yaml` `exclude:` — pre-commit passes filenames explicitly, which
  bypasses ruff's own exclude; an autofix once stripped the side-effect `empty_pb2` imports
  that register protobuf well-known types and broke server import). Regenerate only with
  `grpcio-tools==1.80.0` (dev group) — the runtime `grpcio>=1.80,<1.81` band must match the
  `GRPC_GENERATED_VERSION` baked into the stubs.
- **Quality gates:** vendored dirs run under a scoped `per-file-ignores` entry in
  `backend/pyproject.toml` (stylistic families off; `F`/`E`/`W`/`B`/`I`/`DTZ`/`RUF` stay on
  and are clean) and are excluded from `ty` (`[tool.ty.src]`). First-party `agents/` and
  `tests/` keep the full strict bar. **One-time `S` (security) review at vendoring:** 46
  findings, all by-design — S608 SQL built from trusted internal templates (the data plane
  owns its schema), S104 servers binding 0.0.0.0 inside containers, S311 `random` for retry
  jitter, S110/S112 best-effort cleanup paths. Nothing user-input-reachable.
- **Dependency ceilings are compat pins, not staleness:** `grpcio<1.81` (stubs),
  `protobuf<7`, `sse-starlette<3.4`, `jsonschema-rs<0.45`, `structlog<26`, `langgraph<2`,
  `langchain-protocol<0.1` (wire format shared with the frontend SDK — bump both sides
  together).
- **Re-vendoring a newer upstream:** diff the new reconstruction against the verbatim
  snapshot commit, re-apply the deviations in §2, re-run the normalize pass (ruff format +
  fix; keep proto pristine), re-check the migration max against the live
  `schema_migrations`, and re-run the verification battery (frontend chat e2e, store
  roundtrip, `/mcp`, `/a2a`).
- **Unit tests are not mirrored for vendored dirs** (see `backend/CLAUDE.md`): importing
  server modules requires env scaffolding (`REDIS_URI` at import), upstream ships no tests,
  and the correctness bar is the e2e contract — `backend/tests/server/test_smoke.py`
  (integration-marked) plus the frontend round-trip.
