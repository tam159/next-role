## CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

NextRole is a GenAI career assistant. See `README.md` for product overview.

## Layout

Monorepo with two top-level apps. Each has its own `CLAUDE.md` with stack-specific guidance:

- `backend/` — Python 3.13, `uv`, FastAPI-style agents (LangChain / LangGraph / DeepAgents). See `@backend/CLAUDE.md`.
- `frontend/` — Next.js 16, React 19, TypeScript, Tailwind, `pnpm`. See `@frontend/CLAUDE.md`.
- `docker-compose.yml` runs the full local stack.

## `.ua/` — generated codebase graph (never read)

`.ua/` holds a generated [Understand-Anything](https://github.com/Egonex-AI/Understand-Anything) knowledge graph that humans browse in a dashboard (README → "Explore the codebase graph"). Never read, grep, glob, or index anything under `.ua/` — it is megabytes of generated JSON that wastes context and goes stale. Answer from the real source code and the checked-in `*.md` docs instead. Claude Code additionally hard-blocks it via the `Read(/.ua/**)` deny rule in `.claude/settings.json`, and Cursor via `.cursorignore`; for tools with no enforced ignore mechanism (e.g. Codex, which loads this file through `project_doc_fallback_filenames`), this instruction is the only guard.

## Local development

The local stack runs in Docker via `docker compose up -d`. To find host ports for any running service:

```bash
docker ps
```

Read the `0.0.0.0:<host>->...` mappings — host ports come from `.env` (`FRONTEND_LOCAL_PORT`, `LANGGRAPH_LOCAL_PORT`, `POSTGRES_LOCAL_PORT`, `REDIS_LOCAL_PORT`, `OBJECT_STORE_LOCAL_PORT`) and vary per machine, so don't assume defaults. Once you know the port, hit endpoints directly:

- `http://localhost:<LANGGRAPH_LOCAL_PORT>/docs` — backend API docs (LangGraph)
- `http://localhost:<LANGGRAPH_LOCAL_PORT>/files/list?prefixes=/upload/` — artifact files API (object storage)
- `http://localhost:<FRONTEND_LOCAL_PORT>/` — frontend UI
- `http://localhost:<OBJECT_STORE_UI_LOCAL_PORT>/buckets/next-role-artifacts/` — SeaweedFS filer UI (browse bucket objects)

Use the `agent-browser` skill for visual verification, or `curl` for API checks.

If a service connection fails or `docker ps` shows the stack isn't up, **remind the user to run `docker compose up -d`** instead of running it yourself. They may have stopped it intentionally, and silently restarting shared infrastructure can mask real bugs.

### Hot reload vs. restart vs. rebuild

Frontend and backend hot-reload on source edits — frontend via `pnpm dev` (Turbopack), backend via uvicorn `--reload` in the compose `command` (watches everything under `backend/`, agents and server packages alike). **Don't restart for plain code changes**; save the file and the running container picks it up.

Restart (`docker compose restart <service>`) when:

- Adding a frontend dependency (`pnpm --dir frontend add ...` — boot self-heals `node_modules` from the new lockfile).
- Changing `.env` values (env vars are read at container start).
- Editing `docker-compose.yml` (use `docker compose up -d` to apply the diff).
- Editing `backend/server/core_server/` or `backend/server/grpc_common/` — **core-server has no hot reload**; run `docker compose restart core-server`.

Rebuild (`docker compose up -d --build <service>`) when:

- Adding a backend Python dependency (`uv add ...`) — deps are installed at image build time, not boot.
- Editing any `Dockerfile`.

## After editing files

Pre-commit is already installed (`pre-commit install` was run). Edit freely across many files in one task, then **once at the end** validate everything you touched in a single command:

```bash
pre-commit run --files $(git ls-files --modified --others --exclude-standard)
```

This picks up both modified-tracked files and new (untracked, non-ignored) files, and runs ruff (backend), prettier + eslint (frontend), and the relevant type checker on just those paths. Don't run pre-commit after every individual edit — batch it.

If a hook auto-fixes a file (ruff format, prettier, EOL fixer), re-read the file before reporting the change to the user, since the on-disk content may differ from what was just written.

For a full repo sweep, use the `/quality` skill (runs `pre-commit run --all-files`).

## Working agreement — commits & PRs

- **Never commit, push, or open a PR unless I explicitly tell you to.** Make code changes in the working tree and leave them local so I can test first. When I'm ready I'll say "commit" / "open the PR".
- **One PR per feature/requirement**, even a large one — do not split a single feature across multiple PRs. Structure the work as **separate commits per logical phase**. Example (the multi-user feature): one PR with commits for *Identity foundation* → *Backend authn + authz* → *Per-user storage* → *Hardening + docs*.

## Commits

Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`. Lowercase subject, no trailing period. Match the style in `git log`.

## Shared conventions

- **100-char line length** in both backend (`ruff`) and frontend (`prettier`). Don't change one without the other.
- **Package managers are pinned**: `uv` for backend, `pnpm` for frontend. Don't introduce `pip` or `npm`/`yarn`.
- Secrets live in `.env` (gitignored intent — see `.env.example`). `gitleaks` runs on every commit.
