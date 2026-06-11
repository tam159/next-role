# frontend/CLAUDE.md

Next.js 16 (App Router, Turbopack) + React 19 + TypeScript + Tailwind. Talks to the backend agents via `@langchain/react` (the `useStream` v2 runtime + scoped selector hooks) with `@langchain/langgraph-sdk` for the raw `Client` (threads/store APIs) and shared types.

## Tooling

- **Package manager**: `pnpm` only (lockfile is `pnpm-lock.yaml`, version pinned via `packageManager` in `package.json`). Don't run `npm install` or `yarn`.
- **Dev**: `pnpm dev` (Turbopack).
- **Build**: `pnpm build` (Turbopack).
- **Lint**: `pnpm lint:fix`.
- **Format**: `pnpm format` (Prettier).
- **Type check**: `pnpm type-check` (`tsc --noEmit`).
- **All-in-one**: `pnpm quality` runs lint + format + type-check.
- **Adding a dependency**: `pnpm --dir frontend add <pkg>` on the host, then `docker compose restart frontend`. The container's startup runs `pnpm install --frozen-lockfile` (see `docker-compose.yml`), so the lockfile change syncs into the container's `node_modules` volume on next boot — no image rebuild, no volume nuking.

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
  **Upgrade rule:** bump `@langchain/react` first, then (a) set the `@langchain/langgraph-sdk` pin
  to the exact version the new `@langchain/react` depends on (one copy in node_modules — `Client`
  instances cross the package boundary), and (b) keep `@langchain/core` inside its peer range
  (pnpm warns on violation). Prefer `@langchain/react` re-exports for stream types
  (`SubagentDiscoverySnapshot`, `AssembledToolCall`); only `Client` + schema types come from the
  SDK. Keep the SDK in sync with the backend's `langgraph` major.
