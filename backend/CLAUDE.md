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

## Gotchas

- LLM provider keys (OpenAI / Anthropic / Google / AWS / Tavily) come from `.env` via `pydantic-settings` and `python-dotenv`. Don't hardcode.
- `langchain` is on the v1 line (`>=1.2`); APIs differ from older v0.x snippets you may see online.
- `init.sql` exists at the backend root — schema bootstrap for the local DB.
