# PRD: Frontend Test Suite + Required CI Check (v1)

**Status:** shipped · **Scope:** frontend + CI + `upgrade-frontend-deps` skill

## Why

The backend has had pytest and a required `backend-tests` check since early on; the frontend had literally zero test infrastructure — no runner, no `test` script. Every frontend change (including the dependency-upgrade waves the `upgrade-frontend-deps` skill automates) was verified only by type-check + lint + build + manual browser checks, which catch API drift but not behavior regressions in the parsing/routing logic the app has accumulated (source extraction, tool-error parsing, agent-file routing, path-traversal guards). This ships a 367-test Vitest suite, wires it in as a third required CI check, and adds tests to the skill's per-wave gate ("Gate G").

## What the user sees

Developer-facing only. `pnpm test` / `test:watch` / `test:coverage` in `frontend/`; test files colocated next to their source (`foo.test.ts(x)`); a `frontend-tests` check on every PR that **skip-passes on backend-only and docs-only changes**. The PR template's test checklist now names both stacks, and testing docs live in `frontend/CLAUDE.md#testing` (conventions), `CONTRIBUTING.md#testing` (workflow), and `frontend/README.md`. Deliberately absent: tests do **not** run in pre-commit (matches the backend convention — tests are CI's job, hooks stay fast) and there is no coverage threshold gate.

## How — the key architectural choices

**Vitest 4, not Jest.** The app depends on ESM-only packages (`react-markdown@10`, `remark-gfm@4`, deep-ESM `react-syntax-highlighter` imports) and is itself `"type": "module"`; Jest would need `transformIgnorePatterns` surgery and ESM flags. Vitest handles ESM natively, and the repo already ships Vitest 4 in `backend/server/api/js`.

**Two projects split by file extension.** `.test.ts` runs in node (pure modules, route handlers); `.test.tsx` runs in jsdom (rendering, browser APIs) with a shared setup file (jest-dom + Radix polyfills). Vitest 4 removed `environmentMatchGlobs`, so `test.projects` is the supported mechanism — and the extension-as-switch means no per-file `@vitest-environment` docblocks. A `.tsx` test without JSX is fine when jsdom is needed.

**Real temp-dir sandbox for the `/api/files/*` handlers, not fs mocks.** `_lib.ts` computes `REPO_ROOT` from `process.cwd()` **at import time**, so tests spy on `process.cwd()`, mock `@/app/config/agentFiles` with a fixture, and only then dynamically `await import("./route")`. Real filesystem resolution keeps the security assertions honest — the traversal/sibling-prefix cases test what `path.resolve` actually does, not what a mock was told to do.

**Required check via detect-job + job-level `if`.** `frontend-tests.yml` clones `backend-tests.yml`'s shape (paths-filter detect job feeding a job `if:`) — never `on:`-level path filtering, which leaves a required check stuck "Expected" (see PRD-less lesson recorded in the workflow headers and `.claude` memory from PR #32).

## Files of interest

| Concern | Path |
|---|---|
| Runner config (projects split, alias, coverage) | `frontend/vitest.config.ts` |
| jsdom polyfills + jest-dom | `frontend/vitest.setup.ts` |
| Sandbox pattern anchor (traversal guards) | `frontend/src/app/api/files/_lib.test.ts` |
| Heaviest mock surface (`useStream`, file CRUD matrix) | `frontend/src/app/hooks/useChat.test.tsx` |
| Extracted-for-testing parsers | `frontend/src/app/utils/toolErrors.ts`, `frontend/src/app/print/file/printPayload.ts` |
| Required check workflow | `.github/workflows/frontend-tests.yml` |
| Gate G now includes `pnpm test` | `.claude/skills/upgrade-frontend-deps/SKILL.md` (§2, §8) |

## Decisions worth remembering

- **Colocated tests, not a `tests/` mirror tree.** Deliberate divergence from the backend's convention (user's call): it's the frontend-ecosystem norm, imports stay short, and the filename extension doubles as the environment switch. The Next.js build ignores colocated non-route files, verified via `pnpm build`.
- **`globals: true`.** RTL's auto-cleanup only activates when a global `afterEach` exists; globals give it for free and jest-dom extends the global `expect` via one setup import. Cost is one tsconfig `types` entry (`vitest/globals`).
- **Testability refactor kept to three moves.** `parseToolError`/`previewValue` out of `ToolCallBox`, print-payload parsing out of the print route page (which also stopped `FileViewDialog` importing from a route module), and an exported `formatTime`. Everything else embedded in components is covered through rendered output — no export-for-tests sprawl.
- **`vitest` ↔ `@vitest/coverage-v8` are an exact-version lockstep pair** (coverage is an exact peer of the runner) — same failure shape as the `@langchain/react`/SDK pair; recorded as an `upgrade-frontend-deps` ground rule so `pnpm update` doesn't split them.
- **The new workflow reads Node from `frontend/.nvmrc` (24)** instead of hardcoding; `ci.yml` still pins 22 — left alone deliberately to keep the PR reviewable (follow-up chore).
- **Never enable global `restoreMocks`** — it silently undoes `beforeAll` spies (the sandbox's `process.cwd` spy) after the first test.
- **Tests follow code, not the plan.** Writing them surfaced real quirks, asserted as-is rather than "fixed": an unreachable state-routing branch in `useChat.setFiles`, two distinct `useThreads` fallback titles (`Untitled Thread` vs `Thread <id8>`), delete's `EISDIR` branch being Linux-only (darwin throws `EPERM`, so it's covered via a mocked `unlink`), and the composer attach button being dead code behind `COMPOSER_ATTACH_ENABLED = false`.

## Deferred (intentional non-goals for v1)

- **Playwright/browser E2E.** The `agent-browser` skill already covers visual + full-flow verification (and the upgrade skill's baseline-screenshot workflow); a second browser harness isn't worth the CI weight until component tests prove insufficient.
- **Coverage thresholds.** `pnpm test:coverage` reports (text + html); gating on a number invites threshold-gaming before the suite's shape has settled.
- **A `frontend-build` CI job.** CI still never runs `next build`; kept out of `frontend-tests` to keep the required check fast (~35s). Revisit if a build-only breakage ever slips through.
- **`page.tsx` / `layout.tsx` tests.** Whole-app integration (and `next/font` mocking) for near-zero logic — agent-browser territory.
- **Minor skipped branches** — clipboard copy (no `navigator.clipboard` in jsdom), shift-range file selection, docx error states.

## How to verify end-to-end

1. `cd frontend && pnpm test` — 33 files / 367 tests green in ~2s, reporter shows both `node` and `jsdom` project labels.
2. `pnpm test:coverage` — report renders; `coverage/` stays untracked (gitignored) and unlinted.
3. `pnpm type-check && pnpm lint && pnpm build` — clean; build's route list contains no `.test.` entries.
4. Open a PR touching only `frontend/**` — `frontend-tests` executes; `backend-tests` reports success via skip. A docs-only PR shows `frontend-tests` success-via-skip (not stuck "Expected").
5. Post-merge: `gh api repos/tam159/next-role/branches/main/protection/required_status_checks/contexts` lists `code-quality`, `backend-tests`, `frontend-tests`.
