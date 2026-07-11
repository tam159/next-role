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

## Server packages

Everything under `server/` (`api`, `runtime`, `runtime_postgres`, `grpc_common`, `core_server`) is the **agent server** — the platform that serves the LangGraph Server API and runs the agents. Design, topology, and maintenance notes live in [`ARCHITECTURE.md`](ARCHITECTURE.md). House rules:

- **Infrastructure, not product**: fix bugs surgically; don't refactor casually — this code is stable plumbing that the whole product sits on, and churn here has outsized blast radius.
- **Relaxed quality gates by design**: a scoped `per-file-ignores` block in `pyproject.toml` turns off the noisy stylistic rule families for these dirs; ty excludes them (`[tool.ty.src]`). `agents/` and `tests/` keep the full strict bar — don't let server-package leniency leak there.
- **`server/grpc_common/proto/` is generated** — never hand-edit, lint, or format it (excluded in the top-level `[tool.ruff] exclude` AND the pre-commit hooks' `exclude:` — both are required; see `ARCHITECTURE.md` §10). Regenerate only with `grpcio-tools==1.80.0`.
- **Import gotcha**: `server.api.config` requires `REDIS_URI` (and `DATABASE_URI`/`POSTGRES_URI`) at import time — any script or test importing server modules needs those env vars set, even if unused.
- **No mirrored unit tests** for these dirs — the correctness bar is the e2e contract (`tests/server/test_smoke.py` integration tests + the frontend round-trip), not internals.

## Testing

> **Current phase: unit tests + integration tests against the local DB.** LLM evals are deferred (slow + costly). When you create or modify code, write or update **unit tests** by default, and add **integration tests** when the code's value lives in real DB/Redis/HTTP behavior (e.g., pgvector queries, transaction semantics, connection pooling). Do **not** create `@pytest.mark.eval` tests — that marker exists for future use only.

- **Layout**: `backend/tests/` mirrors `backend/agents/`. A source file `agents/<pkg>/<module>.py` has its tests at `tests/<pkg>/test_<module>.py` (e.g., `agents/career_agent/tools.py` → `tests/career_agent/test_tools.py`). **No `__init__.py` needed** — pytest runs in `--import-mode=importlib`, so test subdirectories are plain folders.
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
- **Schema is owned by `backend/storage/migrations/`** (versioned SQL), applied by the backend at container startup under a Redis lock — don't write or expect Alembic/SQLModel migrations of your own. `backend/init.sql` only enables the `vector` extension at first volume creation.
- **To understand the schema, query the live DB** via the `next-role-postgres` MCP (`@bytebase/dbhub`). Default schema is `public`. Prefer it over reading source: list tables → describe the ones relevant to the task. Don't shell into `psql`.

## Local object storage

- **SeaweedFS (S3-compatible)** via docker compose (`object-store` service, S3 API on `${OBJECT_STORE_LOCAL_PORT}`, filer UI on `${OBJECT_STORE_UI_LOCAL_PORT}`). Binary artifact prefixes (`/upload/`, `/tailored_resume/`, `/interview_battlecard/`) live here as objects under `users/default/career_agent/<area>/<relpath>` — the mapping is `agents/career_agent/object_storage.py`, shared by the agent's `ObjectStoreBackend` routes and the files HTTP API (`agents/files_api.py`, mounted via `LANGGRAPH_HTTP`).
- **Config**: `OBJECT_STORE_*` in `.env` — never reuse `AWS_*` (those are live Bedrock creds). In the cloud, point `OBJECT_STORE_*` at S3 / GCS / Azure (`obstore` speaks all three).
- **Testing**: unit tests run against `obstore.store.MemoryStore` (no emulator needed); `@pytest.mark.integration` tests hit the compose SeaweedFS via the host-side endpoint from `.env`.
- rendercv renders in a throwaway `TemporaryDirectory` (see `render_resume_pdf` in `tools.py`) — the `.pdf` and `.typ` outputs are published to `/tailored_resume/` in the object store; no artifact ever lives in the repo tree.

## Authentication & per-user scoping (multi-user, opt-in)

Off by default (`LANGGRAPH_AUTH` unset → the vendored server's noop auth, single-user). When set, the server activates its custom-auth framework and enforces per-user isolation. See `.env.example` for the enable steps and the full three-PR history in `git log` (phases 1–4).

- **AuthN + authZ handlers**: `backend/agents/auth.py` (`auth = Auth()`). `@auth.authenticate` verifies the frontend's Better Auth bearer JWT against its JWKS — **EdDSA pinned** (never trust the token `alg`), `iss`/`aud` checked; uses the `authorization` param (not `request`, which breaks WebSocket scopes). `@auth.on.*` stamp `metadata.owner` + return `{"owner": id}` (threads/runs/crons), deny assistant writes, rewrite store namespaces to the identity, and a global default-deny fails closed.
- **The enforcement gap this closed**: the vendored ops layer ships auth filters on request protos, but the native `core_server` servicers used to ignore them. `server/core_server/_filters.py` translates `AuthFilter` protos → **parameterized** JSONB predicates (values always bound via `Jsonb`, never interpolated; keys are trusted server-authored literals); it's wired into threads/runs/crons/assistants Get/Search/Count/Patch/Delete/Copy. When you touch a servicer query, keep the filter clause — unowned rows must stay `NOT_FOUND`. **Highest-risk sites**: `ThreadsServicerImpl.Stream` (ownership pre-check before subscribing) and `RunsServicerImpl.Create` (`thread_filters`/`assistant_filters`).
- **Per-user storage** (`backend/agents/career_agent/scope.py`): resolves identity at call time from the run config (`configurable.langgraph_auth_user`). `kv_namespace(area)` prepends the identity **only when present** (single-user keeps the original 2-tuple — no `default` segment); `object_scope(identity)` maps to `users/<id|default>/career_agent`. The object-key builders in `object_storage.py` take a `scope`; the agent backend auto-resolves it, the files API passes `request.user.identity`.
- **Dual-mode invariant**: with `LANGGRAPH_AUTH` unset, every filter clause collapses to `""` and every namespace/key falls back to its single-user form — the SQL and storage layout must stay byte-for-byte the pre-auth behavior. Test both modes.
- **Boot guard**: `REQUIRE_AUTH=true` + no `LANGGRAPH_AUTH` raises at config import (cloud safety net).
- **Tests**: `server/core_server/_filters.py` needs `server.*` importable — pytest `pythonpath = ["..", "."]`. Server packages otherwise keep the e2e-contract bar (no mirrored unit tests); `_filters.py` is the exception (pure, injection-safety-critical → unit-tested in `tests/server/test_filters.py`). Scope + files-API-auth tests live under `tests/career_agent/test_scope.py` and `tests/test_files_api_auth.py`.

## Library docs

For tasks involving the **LangChain ecosystem** (LangChain, LangGraph, LangSmith) or the **LlamaIndex ecosystem** (LlamaIndex, LlamaCloud, LlamaParse, LlamaExtract, LlamaSplit, LlamaClassify), use the dedicated MCP servers in addition to Context7:

- `mcp__docs-langchain__*` — LangChain / LangGraph / LangSmith
- `mcp__llama-index-docs__*` — LlamaIndex family

## Gotchas

- LLM provider keys (OpenAI / Anthropic / Google / AWS / Tavily) come from `.env` via `pydantic-settings` and `python-dotenv`. Don't hardcode.
- `langchain` is on the v1 line (`>=1.2`); APIs differ from older v0.x snippets you may see online.
