# frontend/CLAUDE.md

Next.js 16 (App Router, Turbopack) + React 19 + TypeScript + Tailwind. Talks to the backend agents via `@langchain/react` (the `useStream` v2 runtime + scoped selector hooks) with `@langchain/langgraph-sdk` for the raw `Client` (threads/store APIs) and shared types.

## Design system

`DESIGN.md` (in this folder) is the human-facing spec for the UI: color tokens (warm paper + espresso, user-selectable accent — emerald default), typography (Newsreader / Schibsted Grotesk / JetBrains Mono), spacing/radius/shadow scales, and per-component specs with do's & don'ts. **Read it before changing styling, colors, theming, or adding/altering components.** Keep it in sync when the design system changes.

Tokens live in `src/app/globals.css` (CSS variables: app `--color-*`/`--brand-accent*` layer + Radix HSL layer) and `tailwind.config.mjs`. Gotcha: Tailwind `bg-primary`/`text-primary` map to the **paper** tokens, not the brand — use `bg-brand-accent`/`text-brand-accent`/`text-on-accent` or the Button `primary` variant for brand-colored elements.

## Tooling

- **Package manager**: `pnpm` only (lockfile is `pnpm-lock.yaml`, version pinned via `packageManager` in `package.json`). Don't run `npm install` or `yarn`.
- **Dev**: `pnpm dev` (Turbopack).
- **Build**: `pnpm build` (Turbopack).
- **Lint**: `pnpm lint:fix`.
- **Format**: `pnpm format` (Prettier).
- **Type check**: `pnpm type-check` (`tsc --noEmit`).
- **All-in-one**: `pnpm quality` runs lint + format + type-check.
- **Adding a dependency**: `pnpm --dir frontend add <pkg>` on the host, then `docker compose restart frontend`. The container's startup runs `pnpm install --frozen-lockfile` (see `docker-compose.yml`), so the lockfile change syncs into the container's `node_modules` volume on next boot — no image rebuild, no volume nuking.

## Testing

Vitest 4 + React Testing Library. Tests are **colocated** (`<module>.test.ts(x)` next to its
source) and run in CI's required `frontend-tests` check — deliberately not in pre-commit.

- **Run**: `pnpm test` (full suite), `pnpm test:watch`, `pnpm test:coverage` (v8),
  `pnpm exec vitest run <path>` (one file).
- **Environment by extension** (`vitest.config.ts` projects): `.test.ts` → node (pure modules,
  API route handlers, fetch wrappers); `.test.tsx` → jsdom (anything that renders or touches
  `window`/storage — a `.tsx` test without JSX is fine when jsdom is needed).
- **Globals are on** (`describe`/`it`/`expect`/`vi` need no imports; tsconfig has
  `vitest/globals`); RTL auto-cleanup is active.
- **`vitest.setup.ts` owns the jsdom polyfills** (ResizeObserver, matchMedia, pointer capture,
  scrollIntoView/scrollTo, PointerEvent) plus jest-dom matchers. Extend it rather than shimming
  per-test.
- **Mocking conventions** — never hit the network or the real backend:
  - `@langchain/react`: `vi.mock` with the `importOriginal` spread, stubbing only `useStream` /
    the scoped selector hooks the component uses.
  - SDK `Client`: mock the class where it's constructed (`ClientProvider`, `useThreads`).
    `app/lib/agentFiles.ts` receives the client as a parameter (type-only import) — pass a plain
    stub object, no module mock.
  - `nuqs`: `NuqsTestingAdapter` for components, `vi.mock("nuqs")` for hooks. `swr`: never mock —
    wrap in a fresh-cache `<SWRConfig value={{ provider: () => new Map() }}>`. `fetch`:
    `vi.stubGlobal("fetch", ...)` + `vi.unstubAllGlobals()` in afterEach. `sonner`: mock `toast`.
- **Import-time state**: `src/lib/config.ts` reads env vars and `src/app/api/files/_lib.ts`
  computes `REPO_ROOT` from `process.cwd()` at module load — stub first (`vi.stubEnv` /
  `vi.spyOn(process, "cwd")`), then dynamically `await import(...)`. Never enable global
  `restoreMocks` (it silently kills `beforeAll` spies).
- **Route-handler tests** use a real temp-dir sandbox, not fs mocks: mock
  `@/app/config/agentFiles` with a fixture config, point `process.cwd()` at `<tmp>/frontend`,
  create fixtures with `node:fs`, then dynamically import the route
  (see `src/app/api/files/*/route.test.ts`).
- **Upgrade rule**: `vitest` + `@vitest/coverage-v8` are an exact-version lockstep pair — bump
  together (see the `upgrade-frontend-deps` skill).

## Style

- **Line length: 100** (Prettier `printWidth`). Matches the backend; keep them in sync.
- **Double quotes** (Prettier `singleQuote: false`), 2-space indent, `trailingComma: "es5"`.
- `prettier-plugin-tailwindcss` reorders class names — don't fight it manually.
- ESLint allows `@typescript-eslint/no-explicit-any`; underscore-prefixed unused vars are fine.

## Stack notes

- UI primitives are Radix (`@radix-ui/*`) + Tailwind, not a single component library.
- State: `swr` for data fetching, `nuqs` for URL state, `zod` for runtime validation.
- LangGraph integration uses `@langchain/react` + `@langchain/langgraph-sdk` + `@langchain/core`.
  All three direct deps are required: app code imports `Client`/`Assistant`/`Thread` from the SDK
  (not re-exported by `@langchain/react`) and message classes from core (a _peer_ dep of
  `@langchain/react` — peers are never bundled; the app owns the single `BaseMessage` identity).
  **Upgrade rule:** `@langchain/react` + `@langchain/langgraph-sdk` are a lockstep pair (same
  monorepo, like `react`/`react-dom`) — bump `@langchain/react` first, then pin the SDK
  **exactly** (no `^`) to the version the new `@langchain/react` depends on:
  `pnpm --dir frontend add @langchain/langgraph-sdk@<ver> --save-exact`. The invariant is **one
  installed copy of the SDK**, shared with `@langchain/react` (`Client` instances cross the
  package boundary). The SDK is a _regular_ dep of `@langchain/react`, so a version conflict does
  NOT warn at install time — pnpm silently installs two copies; only _peer_ conflicts
  (`@langchain/core`) warn. The exact pin prevents self-inflicted drift (`pnpm update` can't
  float it), and `scripts/check-langchain-sdk-sync.mjs` (pre-commit hook
  `frontend-langchain-sdk-sync` + `pnpm quality`) catches the forgotten-sync case on
  `@langchain/react` bumps, printing the exact fix command. Prefer `@langchain/react` re-exports for stream types
  (`SubagentDiscoverySnapshot`, `AssembledToolCall`); only `Client` + schema types come from the
  SDK. Keep the SDK in sync with the backend's `langgraph` major.
