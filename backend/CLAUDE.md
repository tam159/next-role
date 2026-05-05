# backend/CLAUDE.md

Python 3.13 backend. Built on LangChain / LangGraph / DeepAgents for the career-agent workflow.

## Tooling

- **Package manager**: `uv` only. Use `uv add <pkg>` / `uv add --dev <pkg>`; never `pip install`.
- **Run a script**: `uv run <cmd>` (e.g., `uv run python -m app`, `uv run pytest`).
- **Lint**: `uv run ruff check --fix` (or let pre-commit do it).
- **Format**: `uv run ruff format`.
- **Type check**: `uv run ty check` — this is `astral-sh/ty`, not `mypy` or `pyright`. Pre-commit gates on it.
- **Tests**: see [Testing](#testing) below.

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

## Testing

> **Current phase: unit tests + integration tests against the local DB.** LLM evals are deferred (slow + costly). When you create or modify code, write or update **unit tests** by default, and add **integration tests** when the code's value lives in real DB/Redis/HTTP behavior (e.g., pgvector queries, transaction semantics, connection pooling). Do **not** create `@pytest.mark.eval` tests — that marker exists for future use only.

- **Layout**: `backend/tests/` mirrors `backend/app/`. A source file `app/<pkg>/<module>.py` has its tests at `tests/<pkg>/test_<module>.py` (e.g., `app/career_agent/tools.py` → `tests/career_agent/test_tools.py`). **No `__init__.py` needed** — pytest runs in `--import-mode=importlib`, so test subdirectories are plain folders.
- **One source module → can have multiple test files** when concerns are unrelated: split as `test_<module>_<concern>.py` (e.g., `test_tools_parsing.py`, `test_tools_search.py`). Don't pile everything into one giant test file.
- **Run** (from `backend/` — `testpaths = ["tests"]` is relative):
  - Default (fast unit tests only): `cd backend && uv run pytest`
  - Single file: `cd backend && uv run pytest tests/career_agent/test_tools.py`
  - Single test: append `::test_function_name` to the file path
  - Integration tests: `cd backend && uv run pytest -m integration` (assumes the local stack is up)
  - Unit + integration together: `cd backend && uv run pytest -m 'not eval'`
- **Unit tests** (the default, untagged):
  - Add or update the matching test at the mirrored path when you create or modify code.
  - Must be fast and deterministic. Mock external dependencies (`unittest.mock.patch`).
  - **Don't assert on raw LLM output** — it's non-deterministic. Mock the model client and assert on the surrounding logic: what gets passed in, how the response is parsed, error handling. Real LLM evaluation belongs in an eval suite (deferred).
  - Run at least the matching test file to confirm green before reporting work as done. Run the full default suite for cross-cutting changes.
- **Integration tests** (tagged `@pytest.mark.integration`):
  - Use for code whose behavior depends on the live DB/Redis/HTTP — pgvector queries, real transactions, multi-statement flows. Mocking these would lie.
  - Connect to the local stack via `POSTGRES_URI` / `REDIS_URI` from `.env`. Stack-up handling is in the root [Local development](../CLAUDE.md#local-development) section.
  - Tests must clean up after themselves (use a transaction that rolls back, or delete inserted rows in a fixture teardown). Don't pollute the dev DB.
  - Skipped from the default run by `addopts = "... -m 'not integration and not eval'"` in `pyproject.toml`. Run them manually with `-m integration` when relevant; CI will run them later.
- **Async**: `asyncio_mode = "auto"` is set in `pyproject.toml`, so write `async def test_...` directly — no `@pytest.mark.asyncio` decorator needed.
- **Scoped fixtures**: a `conftest.py` in a subpackage (e.g., `tests/career_agent/conftest.py`) is only loaded for tests under it. Keep package-specific fixtures there instead of bloating `tests/conftest.py`. Don't create empty `conftest.py` upfront — wait until a fixture is shared.

### Deferred: LLM evals

`@pytest.mark.eval` is declared in `pyproject.toml` and excluded from the default run. It's for future LLM evaluation tests (slow, costly, non-deterministic). Until eval work begins, the marker should not appear in code.

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
