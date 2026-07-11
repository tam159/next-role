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
- **Import-time state**: `src/lib/config.ts` reads env vars at module load — stub first
  (`vi.stubEnv`), `vi.resetModules()`, then dynamically `await import(...)`. Never enable global
  `restoreMocks` (it silently kills `beforeAll` spies).
- **File bytes never pass through Next.js**: artifact upload/read/list/write/delete go to the
  backend files API (`${deploymentUrl}/files/*`, see `app/lib/agentFiles.ts` `filesApiUrl`).
  There are no Next API routes for files — test the client libs by stubbing `fetch` against
  `/files/*` URLs (see `agentFiles.test.ts`, `uploadFiles.test.ts`); server-side validation
  lives in `backend/agents/files_api.py` and is tested in `backend/tests/test_files_api.py`.
- **Upgrade rule**: `vitest` + `@vitest/coverage-v8` are an exact-version lockstep pair — bump
  together (see the `upgrade-frontend-deps` skill).

## Style

- **Line length: 100** (Prettier `printWidth`). Matches the backend; keep them in sync.
- **Double quotes** (Prettier `singleQuote: false`), 2-space indent, `trailingComma: "es5"`.
- `prettier-plugin-tailwindcss` reorders class names — don't fight it manually.
- ESLint allows `@typescript-eslint/no-explicit-any`; underscore-prefixed unused vars are fine.

## Authentication (multi-user, opt-in)

Off by default (`NEXT_PUBLIC_AUTH_ENABLED` unset → today's zero-login single-user app). When on, [Better Auth](https://better-auth.com) runs inside this app (Google OAuth + email/password + JWT plugin), tables in the shared Postgres.

- **Gate on `isAuthEnabled()`** (`src/lib/auth/enabled.ts`) everywhere auth-specific code runs — every auth component/module must render/behave as a no-op when it returns false, so the zero-login path is byte-for-byte unchanged. Same for `isGoogleAuthEnabled()` (the Google button).
- **Files**: `src/lib/auth/server.ts` (server config, server-only — lazily imported by the route so zero-login never needs a DB pool/secret), `client.ts` (`authClient`), `token.ts` (bearer plumbing), `src/app/api/auth/[...all]/route.ts` (handler, 404s when disabled), `src/app/login/page.tsx`, `src/app/components/auth/{SessionGate,UserMenu}.tsx`.
- **Backend calls carry the JWT** via `authOnRequest` (SDK `onRequest` hook on both `Client` sites) and `authedFetch` (the 5 raw `/files/*` calls). Both are **no-ops without a token** — `authedFetch` passes the request through untouched, so tests that assert exact `fetch(...)` arity still pass. The token is cached and refreshed on expiry/401 (`getBearerToken`/`clearBearerToken`).
- **Contract for the backend**: `GET /api/auth/token` mints an EdDSA JWT (`sub` = user id, 15-min TTL); `/api/auth/jwks` publishes keys. The backend (`backend/agents/auth.py`) verifies against JWKS.
- **Config override is pinned in auth mode**: `getConfig()` (`src/lib/config.ts`) ignores a stored `deploymentUrl`/`assistantId` when auth is on (XSS bearer-exfil guard) — model/UI prefs stay local.
- **Store namespaces need no per-user handling here**: the FE keeps sending logical `["career_agent", …]`; the backend's `@auth.on.store` rewrite prepends identity. Don't add the user segment on the FE.
- **Testing auth code**: `vi.stubEnv("NEXT_PUBLIC_AUTH_ENABLED", ...)` per test; mock `@/lib/auth/client`. `token.ts` holds module-level cache state, so re-import it fresh (`vi.resetModules`) after stubbing env (see `token.test.ts`).

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
