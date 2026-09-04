---
name: quality
description: Run the repo's quality gate — pre-commit on the files you changed by default, or the full `pre-commit run --all-files` sweep when asked for a full pass, before opening a PR, or after a large refactor. Use when the user asks for quality checks, pre-commit, linting, formatting, type checking, or wants to confirm the whole repo is clean.
---

# Quality

Run validation the way `CLAUDE.md` defines it for this repo. Two modes — pick by scope, not habit.

## Changed-file check (default after edits)

Validate everything you touched once at the end of a task — modified tracked files plus new untracked, non-ignored files — in a single run from the repo root:

```bash
pre-commit run --files $(git ls-files --modified --others --exclude-standard)
```

Don't run it after every individual edit; batch. If `pre-commit` isn't on this shell's PATH, it is a backend dev dependency: `backend/.venv/bin/pre-commit run --files ...`.

## Full sweep (`/quality`, "full quality pass")

When invoked explicitly, when asked for a full quality pass, before opening a PR, or after a large refactor:

```bash
pre-commit run --all-files
```

This executes every configured hook on every tracked file:

- `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`
- `gitleaks` (secret scanning)
- Backend + analytics: `ruff check --fix`, `ruff format`, and `uv run ty check` per project
- Analytics dbt SQL: `sqlfluff fix` + `sqlfluff lint`
- Frontend: `eslint --fix`, `prettier --write`, `pnpm --dir frontend type-check`, and `scripts/check-langchain-sdk-sync.mjs` (single installed copy of `@langchain/langgraph-sdk`)

## Workflow (both modes)

1. Run the command from the repo root.
2. If hooks **modify files** (ruff/prettier auto-fix, EOL fixer): re-read those files before reporting — the on-disk content differs from what you wrote — then re-stage and re-run until the run is clean.
3. If hooks **fail with errors** (type errors, lint errors that aren't auto-fixable, secrets detected): report the errors. Do not edit unrelated files to make them pass — fix only what's broken.
4. Report the final status: green (everything passed) or red (the remaining failures).

## Targeted checks

Pre-commit runs linters and type checkers, not tests. Run the relevant suite before the pre-commit step when it gives faster, more relevant feedback:

- Backend unit tests: `cd backend && uv run pytest` (one file: `uv run pytest tests/path/test_file.py`)
- Backend integration tests: `cd backend && uv run pytest -m integration` (needs the local stack)
- Frontend tests: `cd frontend && pnpm test`
- Frontend type check only: `cd frontend && pnpm type-check`; lint + format + type-check together: `pnpm quality`

## Local stack

If integration tests or visual checks need services and `docker ps` shows the stack is not running, remind the user to run `docker compose up -d`. Do not start the stack yourself — they may have stopped it intentionally.
