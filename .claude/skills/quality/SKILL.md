---
name: quality
description: Run the full quality gate across backend and frontend (pre-commit run --all-files). Use before opening a PR, after a large refactor, or when you want to confirm the whole repo is clean.
---

Run the full quality gate from the repo root:

```bash
pre-commit run --all-files
```

This executes every configured hook on every tracked file:

- `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`
- `gitleaks` (secret scanning)
- Backend: `ruff check --fix`, `ruff format`, `uv run ty check`
- Frontend: `eslint --fix`, `prettier --write`, `pnpm --dir frontend type-check`

## Workflow

1. From the repo root, run `pre-commit run --all-files`.
2. If hooks **modify files** (ruff/prettier auto-fix, EOL fixer): re-stage them and re-run until the run is clean.
3. If hooks **fail with errors** (type errors, lint errors that aren't auto-fixable, secrets detected): report the errors to the user. Do not edit unrelated files in an attempt to make them pass — fix only what's broken.
4. Report the final status: green (everything passed) or red (list of remaining failures).

## When NOT to use

For files you just edited, prefer the narrower `pre-commit run --files <paths>` — it's much faster and validates exactly what you changed.
