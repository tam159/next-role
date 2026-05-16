---
name: upgrade-backend-deps
description: Upgrade Python backend dependencies to their latest compatible versions. Resolves the lockfile with `uv lock --upgrade`, syncs the venv, bumps the matching `>=` pins in `backend/pyproject.toml` and `ruff-pre-commit` rev in `.pre-commit-config.yaml`, rebuilds the backend Docker image if requested, and reports what moved vs. what stayed pinned by transitive constraints. Use when the user says "upgrade backend libs", "bump backend deps", "update Python dependencies", or after they've manually run `uv lock --upgrade` and want the pyproject/config files reconciled.
---

Upgrade the backend's Python dependencies, reconcile the version pins in tracked config, and (optionally) rebuild the Docker image so the running container picks up the new versions.

## Workflow

### 1. Resolve and sync

```bash
cd backend
uv lock --upgrade 2>&1 | tee /tmp/uv-upgrade.log
uv sync
```

`uv lock --upgrade` re-resolves every dep against PyPI within the constraint set in `pyproject.toml`. Its stdout lists every `Updated <pkg> vX -> vY` line — that diff is the source of truth for steps 2, 3, and 9. Keep it.

If the log shows `Resolved N packages` with no `Updated` lines, also check whether the lockfile itself changed:

```bash
git diff --stat backend/uv.lock
```

- **No diff at all** → truly up to date. Stop and report "already up to date."
- **Diff exists but only inside `[package.metadata] requires-dist`** → no packages moved, but `pyproject.toml` was edited out-of-band since the last lock and the metadata block just got re-synced. Skip steps 2–7 (nothing to bump or rebuild — step 4's reconcile already happened as part of this `--upgrade` run) and jump to step 8 to lint, then step 9 to report.
- **Diff includes `version = "..."` lines** → real version moves; continue with steps 2 onward.

### 2. Bump pins in `backend/pyproject.toml`

For each package that appears in `[project.dependencies]` **or** `[dependency-groups].dev`, update its `>=X.Y.Z` lower bound to match the new locked version.

**Do not bump transitive dependencies** (boto3, botocore, urllib3, idna, requests, pydantic-core, etc.) — they're not in `pyproject.toml` at all; they're pulled in by direct deps and pinned in `uv.lock`. Only touch lines that already exist in the toml.

If a direct dep didn't move (e.g. held back by another constraint), leave its pin alone.

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

### 5. Check the backend container state

```bash
docker ps --filter "name=backend" --format '{{.Names}} {{.Status}}'
```

Remember whether it was running — needed in step 7.

### 6. Rebuild the backend image

```bash
docker compose build --no-cache backend
```

`--no-cache` because the Dockerfile's `pip install -c /api/constraints.txt -e /deps/*` step can otherwise reuse a stale layer. The rebuild is heavy (a few minutes) — if the user only edited config and doesn't need the container image refreshed yet, ask before running this step.

### 7. Restart the container only if it was running before

```bash
docker compose up -d backend   # only if step 5 showed it was up
```

Don't start the container if the user had it stopped — they may have stopped it intentionally. After restart, verify health:

```bash
docker compose ps backend
docker compose logs backend --tail 30
```

### 8. Lint the edited config files

```bash
cd ..  # back to repo root
pre-commit run --files backend/pyproject.toml .pre-commit-config.yaml backend/uv.lock
```

This runs `toml-sort` / yaml checks so the edits match the repo's formatting. Include `backend/uv.lock` so any reconciliation from step 4 gets validated too.

### 9. Report

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
- **The Docker image bakes deps at build time** (see [backend/Dockerfile](backend/Dockerfile)) — boot does not re-resolve. So a fresh `uv.lock` on the host only reaches the container after `docker compose build`.
- **Frontend deps are a separate flow** — this skill is backend-only.
