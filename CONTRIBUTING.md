# Contributing to NextRole

Thanks for your interest in contributing! NextRole is a GenAI career assistant — a Python
(LangChain / LangGraph / DeepAgents) backend and a Next.js frontend, wired together with Docker
Compose. This guide covers how to get set up, the conventions we follow, and how to get a change
merged.

By participating you agree to keep things respectful and constructive. Be kind in issues and
reviews.

## Ways to contribute

- 🐛 **Report a bug** — open an issue with the **Bug report** template.
- 💡 **Propose a feature** — open an issue with the **Feature request** template, ideally before
  writing code, so we can align on scope.
- 📝 **Improve docs** — README, `CLAUDE.md` files, and design docs in `docs/prd/` all welcome fixes.
- 🔧 **Send a pull request** — see the workflow below.

New here? Issues labelled [`good first issue`](https://github.com/tam159/next-role/labels/good%20first%20issue)
are a gentle place to start.

## Development setup

You'll need [Docker](https://docs.docker.com/get-docker/) + Docker Compose, and — for running the
linters/tests on the host — [`uv`](https://docs.astral.sh/uv/) (backend) and
[`pnpm`](https://pnpm.io/) (frontend).

```bash
# 1. Fork on GitHub, then clone your fork
git clone https://github.com/<your-username>/next-role.git
cd next-role

# 2. Configure environment (contributors don't get the maintainer's .env)
cp .env.example .env          # fill in your own API keys — see the README's env table

# 3. Launch the full stack (frontend, backend, Postgres, Redis)
docker compose up -d

# 4. Find your host ports (they come from .env and vary per machine)
docker ps                     # read the 0.0.0.0:<host>->... mappings
#    Frontend UI      →  http://localhost:<FRONTEND_LOCAL_PORT>/
#    Backend API docs →  http://localhost:<LANGGRAPH_LOCAL_PORT>/docs

# 5. Install pre-commit hooks (runs the same checks CI runs)
pip install pre-commit        # or: uv tool install pre-commit / brew install pre-commit
pre-commit install
```

Both containers **hot-reload on source edits** — just save the file. You only need to restart or
rebuild when you change dependencies or `.env`; see the **Dev workflow** section of the
[README](README.md) for the exact rules.

Stack-specific tooling lives in the per-app guides — read these before diving in:

- Backend (Python, `uv`, tests, ruff/ty): [`backend/CLAUDE.md`](backend/CLAUDE.md)
- Frontend (Next.js, `pnpm`, eslint/prettier): [`frontend/CLAUDE.md`](frontend/CLAUDE.md)

## Pull request workflow

1. **Fork** the repo and create a topic branch off `main`:

   ```bash
   git checkout -b feat/short-description     # or fix/…, docs/…, chore/…
   ```

2. **Make your change.** Keep PRs focused — one logical change per PR is much easier to review.
3. **Add or update tests** (see [Testing](#testing)) and docs for any behavior you change.
4. **Run the quality gate locally** before pushing (see below) — this is exactly what CI enforces.
5. **Push** to your fork and **open a PR** against `tam159/next-role:main`. Fill in the PR template
   so reviewers have context.
6. **CI must pass.** Every PR runs three required checks:
   - `code-quality` — the full pre-commit gate (ruff, ty, eslint, prettier, gitleaks, file hygiene).
   - `backend-tests` — the default backend unit-test suite.
   - `frontend-tests` — the frontend Vitest unit + component suite.
7. Address review feedback by pushing more commits to the same branch. Squash/cleanup happens at
   merge time, so don't worry about a messy intermediate history.

`main` is protected: changes land via PR with green CI.

## Quality gate

Run everything you touched in one shot (matches the CI `code-quality` job):

```bash
pre-commit run --files $(git ls-files --modified --others --exclude-standard)
```

For a full-repo sweep: `pre-commit run --all-files`.

And run the backend tests (matches the CI `backend-tests` job):

```bash
cd backend && uv run pytest
```

And the frontend tests (matches the CI `frontend-tests` job):

```bash
cd frontend && pnpm test
```

If a hook auto-fixes a file (ruff format, prettier, end-of-file fixer), re-stage the change and
commit again.

## Testing

Both apps have unit-test suites that run as required CI checks; the backend adds local-DB
integration tests, and LLM evals are deferred.

### Backend (pytest)

Full details (layout, markers, async mode) are in [`backend/CLAUDE.md`](backend/CLAUDE.md#testing).
The essentials:

- Tests mirror the source tree: `app/<pkg>/<module>.py` → `tests/<pkg>/test_<module>.py`.
- **Add/update unit tests by default** when you change code. Mock external dependencies; never
  assert on raw LLM output (it's non-deterministic).
- Add **integration tests** (`@pytest.mark.integration`) when behavior depends on the live
  DB/Redis/HTTP. They're excluded from the default run; run them with `uv run pytest -m integration`
  against a running stack.
- Don't add `@pytest.mark.eval` tests — that marker is reserved for future LLM evals.

```bash
cd backend
uv run pytest                              # default: fast unit tests (what CI runs)
uv run pytest tests/career_agent/test_tools.py   # a single file
uv run pytest -m integration               # integration tests (needs the local stack up)
```

### Frontend (Vitest)

Full details (environments, mocking conventions) are in
[`frontend/CLAUDE.md`](frontend/CLAUDE.md#testing). The essentials:

- Tests are **colocated**: `src/**/<module>.test.ts(x)` sits next to the file it covers.
- The file extension picks the environment: `.test.ts` runs in node (pure modules, API route
  handlers), `.test.tsx` runs in jsdom (anything that renders or touches `window`).
- **Add/update unit tests by default** when you change code. Mock the LangGraph SDK/stream and
  `fetch` — tests never hit the network.

```bash
cd frontend
pnpm test                                  # full suite (what CI runs)
pnpm test:watch                            # watch mode
pnpm exec vitest run src/lib/config.test.ts   # a single file
pnpm test:coverage                         # v8 coverage report
```

## Coding conventions

- **Line length is 100** in both backend (ruff) and frontend (prettier) — keep them in sync.
- **Backend**: ruff selects `ALL` with targeted ignores, and `ty` gates types. Prefer a real fix
  over a suppression; if you must suppress, do it narrowly (`# noqa: <CODE>`), never bare.
- **Frontend**: double quotes, 2-space indent, Prettier owns Tailwind class ordering — don't fight
  it.
- **Package managers are pinned**: `uv` for backend, `pnpm` for frontend. Don't introduce
  `pip`/`npm`/`yarn`.
- **Never commit secrets or user uploads.** Secrets live in `.env` (gitignored); `gitleaks` runs on
  every commit and in CI. CVs/JDs are PII and must never be committed.

## Commit messages

We follow [Conventional Commits](https://www.conventionalcommits.org/): lowercase subject, no
trailing period.

```
feat: add ATS keyword pass to resume tailor
fix: handle empty JD URL in extract_jd
docs: clarify env setup in README
```

Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`. Match the style in `git log`.

## Reporting bugs & requesting features

Open an [issue](https://github.com/tam159/next-role/issues/new/choose) and pick the matching
template. For bugs, include reproduction steps, what you expected, and what happened (logs,
screenshots, LangSmith trace links all help).

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE), the same license that covers this project.
