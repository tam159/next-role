# backend/CLAUDE.md

Python 3.13 backend. Built on LangChain / LangGraph / DeepAgents for the career-agent workflow.

## Tooling

- **Package manager**: `uv` only. Use `uv add <pkg>` / `uv add --dev <pkg>`; never `pip install`.
- **Run a script**: `uv run <cmd>` (e.g., `uv run python -m app`, `uv run pytest`).
- **Lint**: `uv run ruff check --fix` (or let pre-commit do it).
- **Format**: `uv run ruff format`.
- **Type check**: `uv run ty check` — this is `astral-sh/ty`, not `mypy` or `pyright`. Pre-commit gates on it.
- **Tests**: `uv run pytest`. Async tests are enabled via `pytest-asyncio`.

## Style

- **Line length: 100** (ruff). Matches the frontend; keep them in sync.
- **Ruff config selects `ALL`** with targeted ignores in `pyproject.toml`. Read the ignore list before assuming a rule is off — most are on. Notably enforced: docstrings (`D`), security (`S`), complexity (`C901`, max 10), naming (`N`), type-annotations (`ANN`).
- Underscore-prefixed names are allowed unused (`dummy-variable-rgx`).
- Per-file: `__init__.py` allows `F401` (re-exports); notebooks allow `D100`.

### Last-resort suppressions

`ruff` (with `select = ["ALL"]`) and `ty` are strict — sometimes a rule is wrong for the situation. Try a real fix first. If it still won't pass after a couple of honest attempts, suppress narrowly on the offending line:

- **Ruff**: `# noqa: <CODE>` with the specific code from the error (e.g., `# noqa: SLF001` for private-member access). Never bare `# noqa`.
- **Ty (type check)**: `# type: ignore # noqa: PGH003` — the `# noqa: PGH003` keeps ruff from complaining about the blanket type-ignore.

Suppress one line at a time. Don't blanket-disable a rule in `pyproject.toml` to make a single error go away.

## Local database

- **Postgres 18 + pgvector** via `docker compose up postgres`. Connection: `POSTGRES_URI` in `.env`; local port is `${POSTGRES_LOCAL_PORT}` (default `5449`). Driver is `psycopg` (psycopg3).
- **Schema is owned by `langchain/langgraph-api:3.13`** (the backend's base image). It runs its own migrations on container startup — don't write or expect Alembic/SQLModel migrations of your own. `backend/init.sql` only enables the `vector` extension at first volume creation.
- **To understand the schema, query the live DB** via the `next-role-postgres` MCP (`@bytebase/dbhub`). Default schema is `public`. Prefer it over reading source: list tables → describe the ones relevant to the task. Don't shell into `psql`.

## Library docs

For tasks involving the **LangChain ecosystem** (LangChain, LangGraph, LangSmith) or the **LlamaIndex ecosystem** (LlamaIndex, LlamaCloud, LlamaParse, LlamaExtract, LlamaSplit, LlamaClassify), use the dedicated MCP servers in addition to Context7:

- `mcp__docs-langchain__*` — LangChain / LangGraph / LangSmith
- `mcp__llama-index-docs__*` — LlamaIndex family

## Gotchas

- LLM provider keys (OpenAI / Anthropic / Google / AWS / Tavily) come from `.env` via `pydantic-settings` and `python-dotenv`. Don't hardcode.
- `langchain` is on the v1 line (`>=1.2`); APIs differ from older v0.x snippets you may see online.
