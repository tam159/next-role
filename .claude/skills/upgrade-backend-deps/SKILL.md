---
name: upgrade-backend-deps
description: Upgrade Python backend dependencies to their latest compatible versions. Resolves the lockfile with `uv lock --upgrade`, syncs the venv, bumps the matching `>=` pins in `backend/pyproject.toml` and `ruff-pre-commit` rev in `.pre-commit-config.yaml`, runs the backend unit tests, rebuilds the backend Docker image if requested, opens a PR with the change (watching CI for upgrade fallout), and reports what moved vs. what stayed pinned by transitive constraints. Use when the user says "upgrade backend libs", "bump backend deps", "update Python dependencies", or after they've manually run `uv lock --upgrade` and want the pyproject/config files reconciled.
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

Integration tests (`uv run pytest -m integration`) need the local stack up — run them too if it's already running, but don't start it just for this.

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

### 9. Lint the edited config files

```bash
cd ..  # back to repo root
pre-commit run --files backend/pyproject.toml .pre-commit-config.yaml backend/uv.lock
```

This runs `toml-sort` / yaml checks so the edits match the repo's formatting. Include `backend/uv.lock` so any reconciliation from step 4 gets validated too.

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
- **The Docker image bakes deps at build time** (see [backend/Dockerfile](backend/Dockerfile)) — boot does not re-resolve. So a fresh `uv.lock` on the host only reaches the container after `docker compose build`.
- **Frontend deps are a separate flow** — this skill is backend-only.
