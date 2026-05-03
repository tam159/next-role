## CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

NextRole is a GenAI career assistant. See `README.md` for product overview.

## Layout

Monorepo with two top-level apps. Each has its own `CLAUDE.md` with stack-specific guidance:

- `backend/` — Python 3.13, `uv`, FastAPI-style agents (LangChain / LangGraph / DeepAgents). See `@backend/CLAUDE.md`.
- `frontend/` — Next.js 16, React 19, TypeScript, Tailwind, `pnpm`. See `@frontend/CLAUDE.md`.
- `docker-compose.yml` runs the full local stack.

## Local development

The local stack runs in Docker via `docker compose up -d`. To find host ports for any running service:

```bash
docker ps
```

Read the `0.0.0.0:<host>->...` mappings — host ports come from `.env` (`FRONTEND_LOCAL_PORT`, `LANGGRAPH_LOCAL_PORT`, `POSTGRES_LOCAL_PORT`, `REDIS_LOCAL_PORT`) and vary per machine, so don't assume defaults. Once you know the port, hit endpoints directly:

- `http://localhost:<LANGGRAPH_LOCAL_PORT>/docs` — backend API docs (LangGraph)
- `http://localhost:<FRONTEND_LOCAL_PORT>/` — frontend UI

Use the `agent-browser` skill for visual verification, or `curl` for API checks.

### Hot reload vs. restart vs. rebuild

Both containers hot-reload on source edits — frontend via `pnpm dev` (Turbopack), backend via `LANGSMITH_LANGGRAPH_DESKTOP: "true"` in `docker-compose.yml`. **Don't restart for plain code changes**; save the file and the running container picks it up.

Restart (`docker compose restart <service>`) when:

- Adding a frontend dependency (`pnpm --dir frontend add ...` — boot self-heals `node_modules` from the new lockfile).
- Changing `.env` values (env vars are read at container start).
- Editing `docker-compose.yml` (use `docker compose up -d` to apply the diff).

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

## Commits

Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`. Lowercase subject, no trailing period. Match the style in `git log`.

## Shared conventions

- **100-char line length** in both backend (`ruff`) and frontend (`prettier`). Don't change one without the other.
- **Package managers are pinned**: `uv` for backend, `pnpm` for frontend. Don't introduce `pip` or `npm`/`yarn`.
- Secrets live in `.env` (gitignored intent — see `.env.example`). `gitleaks` runs on every commit.
