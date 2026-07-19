---
type: PRD
title: "Own the agent server — drop the langchain/langgraph-api base image"
description: "Move the whole agent server in-repo on a python-slim image — replacing the closed langgraph-api base image with our own API, runtime, and core-server."
tags: [backend, infra, streaming]
timestamp: '2026-07-06T18:19:47+07:00'
status: "shipped"
scope: "Backend (server platform, Docker/compose, migrations, streaming)"
version: v1
---

**Extends:** [18_langchain_react_migration](18_langchain_react_migration.md)

> Design, topology, config knobs, and maintenance rules live in
> [`backend/ARCHITECTURE.md`](../../backend/ARCHITECTURE.md) and
> [`backend/README.md`](../../backend/README.md). This PRD records only the why and the
> decisions those documents don't explain.

# Why

The backend image was `FROM langchain/langgraph-api:3.13` — a closed base image owning the
HTTP/SSE API, run queue, checkpointing, migrations, and process lifecycle. NextRole is open
source; shipping on code we can't read, patch, or version-pin (the `:3.13` tag is mutable)
was the single biggest dependency risk in the repo. This change moves the entire server
in-repo (`backend/server/`), builds our own `python:3.13-slim` + `uv` image, and replaces the
official image's in-process Go data-plane sidecar with a Python `core-server` compose service.

# What the user sees

Nothing changes at the product surface — same endpoints (`/docs`, `/mcp`, `/a2a/{assistant}`),
same port, same deterministic `career_agent` assistant id, existing threads intact. What
changed around the edges: `docker compose ps` shows a new `core-server` service; the DB schema
ships as one consolidated migration (local dev DB was reset by agreement — no important data);
and two UX regressions found during the swap are fixed *better than before*: multi-turn
streaming survives idle gaps and SDK stream rotations, and subagent history panes now restore
complete, ordered tool activity from durable checkpoints instead of showing nothing.

# How — the key architectural choices

- **Two compose services from one image, not a faithful single-container clone.** The official
  image hides a Go gRPC data plane inside the container; we run the Python reimplementation
  (`server/core_server/`) as its own service. Same image, different command — one build, and
  the two planes scale/restart independently (core-server has no hot reload; uvicorn does).
- **Two-concept backend root with split quality gates.** `agents/` (the product) keeps the
  full strict ruff/ty bar; everything under `server/` (the platform) runs with a scoped
  `per-file-ignores` block and a ty path-exclude. Generated gRPC stubs
  (`server/grpc_common/proto/`) are excluded at **both** the ruff-config and pre-commit-hook
  level — hooks pass filenames explicitly, which bypasses ruff's own `exclude`, and an F401
  autofix once stripped the side-effect `empty_pb2` imports that register protobuf well-known
  types, breaking every server import.
- **Streaming: pub/sub for live, a structural Redis-stream log for replay.** Live delivery
  (token text, streamed tool args) rides the per-run pub/sub channels; a capped per-thread log
  (`thread:{tid}:events`, structural events only) backs replay for SDK stream rotations,
  reconnects, and history panes. Chunked `messages/*` events are live-only by measurement:
  logging them was 95% of 66 MB per run and trimmed the earliest subagents' tool events out of
  the cap. Getting this split wrong twice (idle-exit killing streams; XREAD-only delivery
  dropping chunks) produced the two worst regressions of the migration — see Decisions.

# Files of interest

| Concern | Path |
|---|---|
| Server platform (all of it) | `backend/server/` (`api`, `runtime`, `runtime_postgres`, `grpc_common`, `core_server`) |
| Image (two-stage uv, venv at `/opt/venv`) | `backend/Dockerfile` |
| `core-server` service + backend env/healthcheck | `docker-compose.yml` |
| Consolidated schema (future changes: `000002+`) | `backend/storage/migrations/000001_init.up.sql` |
| Event fan-out: pub/sub + structural XADD | `backend/server/core_server/servicers/runs.py` (`_fanout_event`) |
| Two-phase thread stream: replay log → tail pub/sub | `backend/server/core_server/servicers/threads.py` (`Stream`) |
| Namespaced history from the checkpointer | `backend/server/api/grpc/ops/threads.py` (`State.list`, ValueError fallback) |
| Agent-shell PATH fix (venv tools resolvable) | `backend/agents/career_agent/shell_backend.py` (`default_shell_env`) |
| Server smoke tests (regression net) | `backend/tests/server/test_smoke.py` |

# Decisions worth remembering

- **Docs are provenance-free by request.** `ARCHITECTURE.md`/`README.md` present the server as
  NextRole's own; license machinery was excised entirely (call sites in `api/metadata.py` and
  `runtime_postgres/lifespan.py`), and `PLAN = "enterprise"` is a constant. Env vars keep their
  `LANGGRAPH_*`/`LANGSERVE_*` names deliberately — they're the SDK ecosystem's contract.
- **Naming converged in two user-driven steps.** First `backend/app/` → `backend/agents/`;
  then the de-prefixed server packages (`langgraph_api` → `api`, …) were grouped under a single
  `server/` parent. Also this exposed a latent repo bug: the ruff `exclude` array had always
  lived under `[tool.ruff.lint]`, which the *formatter* ignores — moved to top-level
  `[tool.ruff]`.
- **One squashed migration, generated from the live schema — not concatenated history.** The
  60 incremental migrations were replaced by a single `000001_init.up.sql` built from
  `pg_dump --schema-only` of the v60 database and verified by reset-and-diff (identical up to
  a cosmetic CHECK-constraint deparse). Consequence: the `pre-vendor` rollback tag now pairs
  with a fresh DB.
- **Subagent transcripts never reach the parent's context.** A supervisor-style
  `SUBAGENT_OUTPUT_MODE="full_history"` was built, verified live, and *reverted on request*:
  tool names/args in the parent's context risk teaching the model to imitate tool syntax, and
  transcripts ride every subsequent turn. The parent re-reads artifact files on demand; UI
  visibility is fully served by the event log + namespaced `/history` (checkpoint-backed —
  the JS SDK's `fetchSubagentHistory` contract, which the old server 400'd on).
- **The LLM stream watchdog stays at its 120 s default.** A one-off Azure stall
  (`StreamChunkTimeoutError`, `chunks_received=2`) was first "fixed" by raising
  `LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S` to 600 — then reverted once the real UI bug (the
  thread-stream idle-exit) was found: fail-fast beats a 10-minute hang. If stalls recur, the
  right lever is queue-level retriability, not a longer leash.
- **Old base image could bake PII.** `backend/.dockerignore` now blocks the upload/output
  content dirs (`**/upload/`, `**/tailored_resume/`, …) from image layers — a latent exposure
  in the previous `ADD . /deps/next-role` build, fixed en passant.

# Deferred (intentional non-goals for v1)

- **FE: subagent panes in long threads.** All subagent conversations are stored and servable
  (verified via `POST /threads/{id}/history` with `checkpoint.checkpoint_ns`), but the SDK's
  namespace discovery scans only the latest ~20 parent checkpoints, so early-turn panes show
  no activity. Separate FE PR; requirement already written.
- **Queue-level retry for provider stream stalls.** `StreamChunkTimeoutError` hard-fails the
  run (not in the retriable set). Revisit if Azure stalls recur: re-pend from the last
  checkpoint instead of raising the watchdog timeout.
- **Renaming the `LANGGRAPH_*` env/config surface.** Hundreds of keys in `api/config`; kept
  verbatim for SDK compatibility. Revisit only if the SDK contract itself changes.

# How to verify end-to-end

1. `docker compose up -d` — `backend` and `core-server` report healthy; backend logs show
   migrations no-op and "gRPC server is ready".
2. `curl localhost:${LANGGRAPH_LOCAL_PORT}/ok` → `{"ok":true}`; `/docs` renders; `/mcp`
   answers an `initialize` handshake; `/a2a/career_agent` speaks JSON-RPC.
3. In the UI: send a message, then a follow-up after >10 s idle — both stream. Ask for a file
   write and watch the `overwrite_file` card appear mid-run with its args growing (the
   tool-arg streaming this migration twice broke and fixed).
4. Run a full prep (parallel subagents), then cold-reload the thread: every subagent card
   shows complete, ordered tool activity.
5. `cd backend && uv run pytest` (unit) and `uv run pytest -m integration` (smoke incl. the
   idle-stream regression test) — green.
