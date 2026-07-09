---
name: quality
description: Run this repository's quality and validation workflow. Use when the user asks for quality checks, pre-commit, linting, formatting, type checking, test validation, or before reporting code changes as done.
---

# Quality

Run validation the way `CLAUDE.md` defines it for this repo.

## Default Changed-File Check

After code edits, validate all modified and untracked files once at the end:

```bash
pre-commit run --files $(git ls-files --modified --others --exclude-standard)
```

If hooks auto-fix a file, re-read that file before summarizing the result.

## Full Sweep

When the user asks for a full quality pass or broad repository validation, run:

```bash
pre-commit run --all-files
```

## Targeted Checks

- Backend fast tests: `cd backend && uv run pytest`
- Backend single test file: `cd backend && uv run pytest tests/path/test_file.py`
- Backend integration tests: `cd backend && uv run pytest -m integration`
- Frontend full test suite: `cd frontend && pnpm test`
- Frontend type check: `cd frontend && pnpm type-check`
- Frontend all-in-one check: `cd frontend && pnpm quality`

Use targeted checks before the changed-file pre-commit run when they give faster, more relevant feedback.

## Local Stack

If integration tests or visual checks need services and `docker ps` shows the stack is not running, remind the user to run `docker compose up -d`. Do not silently start the stack yourself.
