# NextRole Backend

The backend has two halves: the **agents** (the product) and the **agent server** (the
platform that runs them). Both live in this directory and ship as one Docker image that the
compose file runs as two services — `backend` (HTTP/SSE/WebSocket API + in-process graph
workers) and `core-server` (the gRPC data plane owning the database and run queue).

The server implements the LangGraph Server API, so any `@langchain/langgraph-sdk` client —
including the NextRole frontend — can talk to it, and the agents are also reachable over
[MCP](https://modelcontextprotocol.io) (`/mcp`) and A2A (`/a2a/{assistant_id}`) with no
extra code. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.

## Folder structure

```
backend/
├── agents/                  # First-party agents (full strict lint/type gates)
│   └── career_agent/        #   the career agent: graph, tools, prompts, skills
├── server/                  # The agent server platform
│   ├── api/                 #   ASGI app — routes, auth, streaming, graph
│   │                        #   loading, run workers, gRPC client
│   ├── runtime/             #   Edition router (selects the runtime backend)
│   ├── runtime_postgres/    #   Postgres runtime: pool, migrations, queue, store
│   ├── grpc_common/         #   gRPC contract: generated proto stubs (never
│   │                        #   edit proto/ by hand!) + proto↔python conversion
│   ├── core_server/         #   gRPC data plane (python -m server.core_server)
│   ├── openapi.json         #   Served API spec — must sit next to api/
│   └── logging.json         #   Uvicorn log config
├── storage/migrations/      # Consolidated SQL schema (000001_init), applied at boot
├── tests/                   # Unit tests (mirror agents/) + server smoke tests
├── Dockerfile               # One image for both services (python:3.13-slim + uv)
└── pyproject.toml           # Single uv project: agent + server dependencies
```

Everything under `server/` runs under deliberately relaxed lint/type gates; `agents/` and
`tests/` keep the repo's full strict bar. House rules for touching server code are in
[`CLAUDE.md`](CLAUDE.md).

## Running

The whole stack runs from the repo root:

```bash
docker compose up -d          # frontend · backend · core-server · postgres · redis
```

- API: `http://localhost:${LANGGRAPH_LOCAL_PORT}` — health at `/ok`, docs at `/docs`
- Backend code hot-reloads (uvicorn `--reload` over the bind mount); `core-server` needs
  `docker compose restart core-server` after edits to `core_server/` or `grpc_common/`
- Database schema is applied automatically at backend boot (`storage/migrations/`)

## Development

Tooling is `uv`-only (see [`CLAUDE.md`](CLAUDE.md) for the full conventions):

```bash
uv sync                      # install deps (from backend/)
uv run pytest                # unit tests (fast, default)
uv run pytest -m integration # server smoke tests — needs the stack up
uv run ruff check --fix && uv run ruff format
uv run ty check              # type check (astral-sh/ty)
```

Graphs are registered via the `LANGSERVE_GRAPHS` env in `docker-compose.yml` — a JSON map of
graph id → `path/to/module.py:variable`. Adding a new agent means a new package under
`agents/`, a compiled graph object, and one entry in that map.
