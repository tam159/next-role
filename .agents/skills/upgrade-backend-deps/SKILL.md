---
name: upgrade-backend-deps
description: Upgrade Python backend dependencies to their latest compatible versions. Resolves the lockfile with `uv lock --upgrade`, syncs the venv, bumps the matching `>=` pins in `backend/pyproject.toml` and `ruff-pre-commit` rev in `.pre-commit-config.yaml`, runs the backend unit tests, rebuilds the backend Docker image if requested, verifies thread state still loads on the restarted stack, opens a PR with the change (watching CI for upgrade fallout), and reports what moved vs. what stayed pinned by transitive constraints. Use when the user says "upgrade backend libs", "bump backend deps", "update Python dependencies", or after they've manually run `uv lock --upgrade` and want the pyproject/config files reconciled.
---

Upgrade the backend's Python dependencies, reconcile the version pins in tracked config, and (optionally) rebuild the Docker image so the running container picks up the new versions.

## Workflow

### 1. Resolve and sync

```bash
cd backend
uv lock --upgrade 2>&1 | tee /tmp/uv-upgrade.log
uv sync
```

`uv lock --upgrade` re-resolves every dep against PyPI within the constraint set in `pyproject.toml`. Its stdout lists every `Updated <pkg> vX -> vY` line — that diff is the source of truth for steps 2, 3, 5, and 11. Keep it.

If the log shows `Resolved N packages` with no `Updated` lines, also check whether the lockfile itself changed:

```bash
git diff --stat backend/uv.lock
```

- **No diff at all** → truly up to date. Stop and report "already up to date."
- **Diff exists but only inside `[package.metadata] requires-dist`** → no packages moved, but `pyproject.toml` was edited out-of-band since the last lock and the metadata block just got re-synced. Skip steps 2–8 (nothing to bump, test, or rebuild — step 4's reconcile already happened as part of this `--upgrade` run, and no installed versions changed) and jump to step 9 to lint, then step 10 to commit/open a PR (the reconciled `uv.lock` still needs to land), then step 11 to report.
- **Diff includes `version = "..."` lines** → real version moves; continue with steps 2 onward.

### 2. Bump pins in `backend/pyproject.toml`

For each package that appears in `[project.dependencies]` **or** `[dependency-groups].dev`, update its `>=X.Y.Z` lower bound to match the new locked version.

**Do not bump transitive dependencies** (boto3, botocore, urllib3, idna, requests, pydantic-core, etc.) — they're not in `pyproject.toml` at all; they're pulled in by direct deps and pinned in `uv.lock`. Only touch lines that already exist in the toml.

If a direct dep didn't move (e.g. held back by another constraint), leave its pin alone.

**Server compat pins are not staleness.** The block under `# --- Agent server runtime ---` in `[project.dependencies]` carries upper bounds (`grpcio<1.81`, `protobuf<7`, `sse-starlette<3.4`, `jsonschema-rs<0.45`, `structlog<26`, `langgraph<2`, `langchain-protocol<0.1`) that encode compatibility requirements of the server packages (see `backend/ARCHITECTURE.md` §10). Never delete or raise these ceilings as part of a routine bump — the `grpcio` band in particular must match the generated proto stubs, and `langchain-protocol` must move in lockstep with the frontend's `@langchain/langgraph-sdk`. If an upgrade is blocked by one of them, report it as "held by a server compat pin" rather than forcing it.

### 3. Bump tool revs in `.pre-commit-config.yaml`

Only one entry there is coupled to the uv lockfile: `astral-sh/ruff-pre-commit`'s `rev:` should match the `ruff` version in `dependency-groups.dev`. If `ruff` upgraded, bump the `rev` (prepend `v`, e.g. `0.15.13` → `v0.15.13`).

The other pinned repos (`pre-commit-hooks`, `gitleaks`) aren't managed by `uv` — leave them unless the user explicitly asks.

The `local` hooks invoke `uv run ty check` / `pnpm exec ...` and pick up versions from `pyproject.toml` / `package.json` automatically — no rev to update.

### 4. Reconcile lockfile metadata after editing `pyproject.toml`

```bash
cd backend
uv lock        # NOT --upgrade
```

Plain `uv lock` re-snapshots `pyproject.toml` into the lockfile's `[package.metadata] requires-dist` block without hitting PyPI for new versions. Skipping this step is what produces "noisy `uv.lock` diff with no version changes" on the next person's machine: the `>=` pins you just edited in step 2 are otherwise only present in `pyproject.toml`, not mirrored into the lockfile metadata.

This should be a fast (sub-second) no-op resolve. If it reports `Updated <pkg>` lines here, something raised a floor above the previously locked version — go back to step 2 and treat it as a real upgrade.

### 5. Run the backend unit tests locally

`uv sync` (step 1) already installed the upgraded deps into the host venv, so the suite now exercises the new versions. Run it before committing — a dependency bump can change behavior, not just version numbers, and this is a seconds-long gate versus a multi-minute CI round-trip:

```bash
uv run pytest   # from backend/; unit tests only — addopts excludes integration + eval
```

If something fails it's almost always an upgraded library changing a contract — diagnose the offending package (cross-check the `uv lock --upgrade` log), fix it, and stage the fix alongside the config edits when you commit (step 10). Don't push a known-red upgrade.

> Real example: a `deepagents` 0.6.x bump changed `CompositeBackend.ls()` to report a missing directory as a `path_not_found` error instead of an empty listing, which broke `list_files`. The fix was to normalize it back to `[]`. This local run is exactly the gate that catches that before CI.

> Second real example (2026-09, PR #78): `langgraph-checkpoint-postgres` 3.1.1 → 3.1.2 changed the *private* stage-1 walk helpers (`BasePostgresSaver._ingest_stage1_page` / `_try_advance_walks`) that the vendored checkpointer calls, and every `GET /threads/{id}/state` returned 500 — the UI looked like all threads had vanished — while this suite **and CI stayed green**, because the server packages carry no mirrored unit tests. `tests/server/test_delta_walk_contract.py` now pins that contract; if it trips, port the upstream `aio.py` diff into `server/runtime_postgres/checkpoint.py` and raise the floor (see `backend/ARCHITECTURE.md` §10) rather than pinning the package back.

**A green unit run says nothing about `backend/server/**`.** Those packages are deliberately untested at the unit level and they couple to library internals — step 8b is their gate.

Integration tests (`uv run pytest -m integration`) need the local stack up — run them too if it's already running, but don't start it just for this. Be aware that the thread/store smoke tests in `tests/server/test_smoke.py` **skip when `LANGGRAPH_AUTH` is set** (the default local multi-user setup), so a green `-m integration` run does not prove the thread path works either — again, step 8b does.

### 6. Check the backend container state

```bash
docker ps --filter "name=backend" --format '{{.Names}} {{.Status}}'
```

Remember whether it was running — needed in step 8.

### 7. Rebuild the backend image

```bash
docker compose build backend
```

No `--no-cache` needed: the Dockerfile's dependency layer is keyed on `COPY pyproject.toml uv.lock` followed by `uv sync --frozen`, so a changed lockfile invalidates exactly that layer and an unchanged one reuses cache. The same image serves both the `backend` and `core-server` services (shared `image:` tag) — one build covers both. If the user only edited config and doesn't need the container image refreshed yet, ask before running this step.

### 8. Restart the container only if it was running before

```bash
docker compose up -d backend core-server   # only if step 6 showed it was up
```

(`core-server` runs the same image, so recreate both to keep them on the same build.)

Don't start the container if the user had it stopped — they may have stopped it intentionally. After restart, verify health:

```bash
docker compose ps backend
docker compose logs backend --tail 30
```

### 8b. Verify thread state on the restarted server

A healthy `/ok` only proves the process booted. Loading a thread runs the vendored checkpointer's delta-channel walk, which depends on `langgraph-checkpoint-postgres` internals (step 5's second example) — exercise it against a real thread before calling the upgrade good. Either reload the UI and confirm the backend logs show `GET /threads/.../state 200` rather than `500`:

```bash
docker compose logs backend --since 5m --no-log-prefix | grep -E 'GET /threads/[^ ]+/state [0-9]{3}'
```

or drive the walk directly inside the container — this works with auth enabled, no token needed:

```bash
TID=$(docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select thread_id from checkpoints order by checkpoint_id desc limit 1"')
docker compose exec -T -e TID="$TID" backend python - <<'EOF'
import asyncio, os
from server.runtime_postgres import database
from server.runtime_postgres.checkpoint import Checkpointer

async def main():
    await database.start_pool()
    try:
        cp = Checkpointer()
        cfg = {"configurable": {"thread_id": os.environ["TID"], "checkpoint_ns": ""}}
        tup = await cp.aget_tuple(cfg)
        channels = sorted(tup.checkpoint["channel_versions"])
        hist = await cp.aget_delta_channel_history(cfg, channels=channels)
        print("walk OK —", {ch: len(h.get("writes", [])) for ch, h in hist.items()})
    finally:
        await database.stop_pool()

asyncio.run(main())
EOF
```

A `TypeError` or missing-argument failure here means a checkpoint-library bump changed the private helper contract: fix forward in `server/runtime_postgres/checkpoint.py`, in this same PR.

Hot-reload note: the `backend` service bind-mounts the repo and runs uvicorn `--reload`, so a source fix to the vendored runtime is live in the running container the moment you save it — re-run this check without rebuilding. `core-server` imports the same runtime modules but has **no** hot reload, so after such a fix run `docker compose up -d core-server`, and rebuild (step 7) so the image matches the source before the PR.

### 9. Lint the edited config files

```bash
cd ..  # back to repo root
pre-commit run --files backend/pyproject.toml .pre-commit-config.yaml backend/uv.lock
# `pre-commit` not on PATH in this shell? It's a dev dep: backend/.venv/bin/pre-commit run --files ...
```

This runs `toml-sort` / yaml checks so the edits match the repo's formatting. Include `backend/uv.lock` so any reconciliation from step 4 gets validated too, plus any source/test files you touched to fix fallout (ruff + ty run on those). If `ruff` or `ty` themselves moved, also run the full sweep once (`pre-commit run --all-files`) — new rules can bite `analytics/` and `frontend/` hooks that share the config.

### 10. Commit and open a PR

Once lint is green, land the change on a branch and open a PR — don't commit dep bumps straight to the default branch (`main` is protected, and its CI checks are exactly what catch upgrade fallout).

```bash
cd ..  # repo root, if not already there
git switch -c chore/upgrade-backend-deps   # skip if already on a non-default branch (e.g. a worktree branch)
git add backend/pyproject.toml backend/uv.lock .pre-commit-config.yaml
# also stage any source/test files you touched to fix upgrade fallout (see below)
git commit -m "chore: upgrade backend dependencies"   # Conventional Commit; put the step-11 summary in the body
git push -u origin HEAD
```

Then open the PR against `main`. This repo prefers the **GitHub MCP tools** for repo interactions, so use `mcp__github__create_pull_request` (owner `tam159`, repo `next-role`, base `main`) rather than `gh pr create`. Reuse the upgraded / held-back summary from step 11 as the PR body.

**Watch CI even though step 5 passed.** CI runs on a different Python than the host venv (3.14 vs 3.13 here), so it can still surface a failure the local suite didn't. Watch the PR's `code-quality` and `backend-tests` checks; if one fails, fix it **in the same PR** and push again:

```bash
gh run watch <run-id> --repo tam159/next-role --exit-status
```

Leave the actual merge to the user unless they explicitly ask you to merge.

### 11. Report

Pick the shape that matches what actually happened:

- **Versions moved** — list:
  - **Upgraded:** direct deps from `pyproject.toml` that moved (with old → new versions). Pull these from the `uv lock --upgrade` log filtered to direct deps.
  - **Held back:** direct deps in `pyproject.toml` that did NOT appear in the upgrade log. Worth flagging because they're stuck on something (likely an upper-bound constraint from a transitive dep, or just already at latest).
- **No versions moved, but lockfile reconciled** — report: "Already at latest reachable versions. `uv.lock`'s `requires-dist` metadata was out of sync with `pyproject.toml` (someone edited pins without re-running `uv lock`); resynced in this run — stage `backend/uv.lock` alongside any existing staged pin edits."
- **No diff at all** — report: "Already up to date."

Don't enumerate transitive bumps unless the user asks — there are usually dozens and they're noise.

## Why not `uv pip list --outdated`?

After `uv lock --upgrade`, anything that *could* upgrade within the current constraints already did. `pip list --outdated` would only flag packages that are also blocked by upper bounds — exactly the "held back" set you can derive more cheaply by diffing the upgrade log against the `pyproject.toml` direct-deps list. Skip the extra command.

## Gotchas

- **Don't run `pip install` or edit `uv.lock` by hand.** `uv` owns the lock.
- **`uv lock --upgrade` does two things, one of them silent.** It re-resolves PyPI for newer versions (the `Updated <pkg>` log lines), and it snapshots `pyproject.toml`'s current `requires-dist` into the lockfile's `[package.metadata]` block. The snapshot is silent — no log line. If you hand-edit `>=` pins in `pyproject.toml` and don't follow up with `uv lock`, the lockfile metadata drifts and the next person's `uv lock --upgrade` will produce a noisy "metadata-only" diff with zero `Updated` lines. Step 4 of this skill exists to prevent that.
- **The Docker image bakes deps at build time** (see [backend/Dockerfile](backend/Dockerfile)) — boot does not re-resolve. So a fresh `uv.lock` on the host only reaches the container after `docker compose build`. Source is different: the repo is bind-mounted into `backend` with hot reload, so code fixes are live on save, while `core-server` needs `docker compose up -d core-server` to see them.
- **The vendored server couples to private library APIs.** `backend/server/runtime_postgres/checkpoint.py` calls `BasePostgresSaver._ingest_stage1_page` / `_try_advance_walks` from `langgraph-checkpoint-postgres` and mirrors their stage-1 SQL column contract (`ver_i`/`hb_i`/`inline_i`). A bump of that package can break `GET /threads/{id}/state` with every other gate green. `tests/server/test_delta_walk_contract.py` trips first; the fix is to port the upstream `aio.py` diff into the vendored walk and raise the floor, not to pin the package back (`backend/ARCHITECTURE.md` §10). Step 8b is the runtime check.
- **`-m integration` is weaker than it looks under local auth.** The thread/store smoke tests skip when `LANGGRAPH_AUTH` is set, so they can't catch a broken thread path on a default local stack — step 8b can.
- **Frontend deps are a separate flow** — this skill is backend-only.
