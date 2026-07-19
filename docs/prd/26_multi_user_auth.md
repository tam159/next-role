---
type: PRD
title: "Multi-User Authentication & Per-User Isolation"
description: "Opt-in accounts (Google + email/password) with JWT-verified, owner-scoped threads, files, and memory — zero-login mode stays byte-identical."
tags: [backend, frontend, auth, storage]
timestamp: '2026-07-11T15:52:20+07:00'
status: "shipped"
scope: "full stack (frontend auth + vendored server authn/authz + agent storage scoping)"
version: v1
---

**Extends:** [25_object_storage_artifacts](25_object_storage_artifacts.md), [21_own_agent_server](21_own_agent_server.md)

# Why

NextRole was built for local, single-user, trusted use — one global data layout, no accounts. Cloud deployment (AWS / Vercel / …) needs real users, each seeing only their own threads, files, and memory. The object-storage PRD deliberately left a `users/default/` key seam for exactly this. This PRD realizes it end to end: accounts (Google + email/password), a JWT the backend verifies on every request, owner-scoped threads/runs/crons enforced in SQL, and per-user store namespaces + object keys — all **opt-in**, so `docker compose up` stays zero-login.

# What the user sees

Nothing changes by default — with `AUTH_ENABLED` unset the app is exactly as before, no login screen. Flip it on and the app gates behind a `/login` page (Continue-with-Google when configured, plus email/password sign-up/sign-in); a user chip with a sign-out popover joins the top bar. Each user now has a private world: their thread list shows only their threads, uploads and rendered PDFs are theirs alone (two users can both upload `cv.pdf` without colliding), and saved memory/preferences don't bleed across accounts. An unowned resource is **invisible, not forbidden** — hitting someone else's thread id returns `404`, not `403`.

# How — the key architectural choices

**Activate the vendored server's dormant custom-auth framework — don't build a parallel auth layer.** The self-hosted agent server ([PRD 21](21_own_agent_server.md)) already ships the complete LangGraph custom-auth machinery (`@auth.authenticate`, `@auth.on.*` dispatch, filter→proto conversion, the `AuthContext` ContextVar, `merge_auth` injecting the user into the run config) — it was just wired to `noop`. We wrote the handlers in `backend/agents/auth.py` and set `LANGGRAPH_AUTH`; the platform plumbing came for free. Identity is a Better Auth JWT verified against the frontend's JWKS.

**The load-bearing fix: core-server ignored the authorization filters.** The ops layer *computed* auth filters and shipped them on request protos, but the native `core_server` servicers never read `request.filters` (`grep filters core_server/` → zero hits) — so turning auth on would authenticate users yet still serve everyone every row. `server/core_server/_filters.py` closes it: `AuthFilter` protos → **parameterized** JSONB predicates (values bound via `Jsonb`, never interpolated), wired into every read/write of threads/runs/crons/assistants. Two sites carried the real blast radius: `ThreadsServicerImpl.Stream` (gate ownership *before* subscribing, or any authenticated user streams anyone's live token-by-token events by id — both v1 `/stream` and v2 `/stream/events` reach it) and `RunsServicerImpl.Create` (enforce `thread_filters`/`assistant_filters` or a run injects into a thread you don't own).

**Better Auth, self-hosted in the Next.js app — not a managed IdP or Auth.js.** Auth.js/NextAuth entered maintenance mode in Sept 2025 (team folded into Better Auth; new projects pointed there). A managed IdP (Clerk/Auth0) means a vendor, per-MAU cost, and a weak offline local-dev story. Better Auth owns its tables in *our* Postgres, does Google + email/password out of the box, and its JWT plugin (`/api/auth/token` + `/api/auth/jwks`) is a clean bridge to Python verification — so the architecture stays IdP-agnostic (swap the JWKS issuer later) while working fully offline.

**Dual-mode by absence, and the two storage tiers scope differently on purpose.** With `LANGGRAPH_AUTH` unset every filter clause collapses to `""` and every namespace/key falls back to its single-user form — byte-for-byte the pre-auth SQL and layout, a hard invariant. Because the backends are import-time singletons (no per-run factory), scoping resolves identity at **call time** from the run config (`backend/agents/career_agent/scope.py`). The KV store *prepends* the identity only when present (`(id, "career_agent", area)` vs the original 2-tuple — no `default` segment, so existing rows stay put); the object store *replaces* `default` in the already-present `users/<scope>/` segment. The frontend keeps sending logical `["career_agent", …]` namespaces and the `@auth.on.store` rewrite prepends identity server-side — so the FE needed no per-user store logic at all.

# Files of interest

| Concern | Path |
|---|---|
| AuthN + authZ handlers (JWKS verify, owner-stamp, store rewrite, default-deny) | `backend/agents/auth.py` |
| `AuthFilter` proto → parameterized SQL predicate helper | `backend/server/core_server/_filters.py` |
| Filter enforcement (incl. `Stream` gate, `Create` filters) | `backend/server/core_server/servicers/{threads,runs,crons,assistants}.py` |
| Call-time identity + per-tier namespace/key scoping | `backend/agents/career_agent/scope.py` |
| Object-key builders parameterized by scope | `backend/agents/career_agent/object_storage.py` |
| Files API: request-user scope + 401 guard | `backend/agents/files_api.py` (`_scope`, `_authenticated`) |
| `REQUIRE_AUTH` boot guard | `backend/server/api/config/__init__.py` |
| Better Auth server/client, env gates, route, token plumbing | `frontend/src/lib/auth/{server,client,enabled,token}.ts`, `frontend/src/app/api/auth/[...all]/route.ts` |
| Login page, session gate, user menu | `frontend/src/app/login/page.tsx`, `frontend/src/app/components/auth/{SessionGate,UserMenu}.tsx` |
| Bearer injection on the SDK clients + raw file fetches | `frontend/src/providers/ClientProvider.tsx`, `frontend/src/app/hooks/useThreads.ts`, `frontend/src/app/lib/{agentFiles,uploadFiles}.ts` |
| URL-override pin in auth mode | `frontend/src/lib/config.ts` (`getConfig`) |
| Compose + env wiring; cloud hardening runbook | `docker-compose.yml`, `.env.example` |

# Decisions worth remembering

- **404, never 403, for unowned resources.** Filters narrow the `WHERE` clause, so a resource you don't own is indistinguishable from a missing one — no existence oracle. `403` is reserved for handler-level denies (assistant writes); `401` for a missing/invalid token.
- **Pin the JWT algorithm to EdDSA; read `authorization`, not `request`.** Accepting the token header's `alg` would let a caller downgrade verification. And `@auth.authenticate` takes the `authorization` param (not `request: Request`) because the custom-auth backend raises on a `request` param for WebSocket scopes — the same handler must serve HTTP and WS.
- **The `@auth.on.store` rewrite makes the frontend oblivious.** Because the FE reconstructs store paths from `item.key` (not the returned namespace) and the server prepends identity on both read and write, per-user KV isolation needed **zero** frontend store changes — a much smaller blast radius than threading identity through `agentFiles.ts`.
- **Browsers can't set an `Authorization` header on a WebSocket handshake.** So streaming rides the SSE POST transport (which carries the bearer); the WS route stays for non-browser clients. Verified the `@langchain/react` v2 default resolves to SSE.
- **Start fresh on existing local data (user's call).** No adoption/migration script. The pre-auth global rows stay under `career_agent.*` / `users/default/…` — reachable in zero-login mode, invisible once you log in — and new per-user rows sit alongside under `<id>.career_agent.*` / `users/<id>/…`. Confirmed the legacy row is untouched after enabling auth.
- **Pin `deploymentUrl`/`assistantId` to env in auth mode.** `getConfig()` ignores a stored `deploymentUrl` override when auth is on: otherwise injected page script could point the SDK client (and its bearer) at an attacker origin. Model/UI prefs stay browser-local.
- **Close the Studio backdoors explicitly.** `disable_studio_auth: true` in `LANGGRAPH_AUTH`, and never `LANGSMITH_LANGGRAPH_API_VARIANT=local_dev` in production — both otherwise grant an unauthenticated Studio user.
- **`_filters.py` is the one server-package exception to "no mirrored unit tests."** It's pure and injection-safety-critical, so it's unit-tested (`tests/server/test_filters.py`); this required adding `.` to pytest `pythonpath` so `server.*` imports resolve in tests.

# Deferred (intentional non-goals for v1)

- **Shell-execution sandboxing.** Multi-user mode isolates *data*, but `VirtualPathShellBackend` still runs renders via `subprocess` on the host. This — not auth — is the gate before opening signup to *untrusted* users; it's the roadmap's remote-sandbox step.
- **Per-resource authz for MCP (`/mcp`) and A2A (`/a2a`).** They're authentication-gated only today; disable them in a shared deployment until scoping is wired into those handlers.
- **Data adoption tooling.** Deliberately skipped per the start-fresh decision; a one-shot script (stamp owner metadata, re-key store prefixes, copy `users/default/*` objects) is the path if a real user's existing history ever needs to move.
- **Managed-bucket provisioning, presigned delivery** (carried from [PRD 25](25_object_storage_artifacts.md)), **org/teams RBAC, admin UI, API keys for MCP clients, per-user quotas/rate limits (LLM cost!).** Product/infra surface beyond single-user-becomes-multi-user.

# How to verify end-to-end

1. **Zero-login unchanged**: with `AUTH_ENABLED=false` and no `LANGGRAPH_AUTH`, `docker compose up -d`; app loads with no login redirect, `GET /api/auth/get-session` → 404, unauthenticated `POST /threads/search` and `GET /files/list` → 200.
2. **Enable auth**: set `AUTH_ENABLED=true` + `BETTER_AUTH_SECRET`, run the Better Auth migration (`pnpm --dir frontend dlx @better-auth/cli migrate --config src/lib/auth/server.ts`), set `LANGGRAPH_AUTH`, restart. `/` redirects to `/login`; sign up → app; `/api/auth/jwks` serves an Ed25519 key; decode `/api/auth/token` → `sub`/`iss`/`aud`/15-min `exp`.
3. **Two-user isolation** (tokens A, B): A creates a thread; B → `404` on that thread's Get/Search/Patch/Delete/state/history, run Get/Delete, run-Create-into-A's-thread, and `/stream` (404 event) / `/stream/events` (consumer dies, no heartbeat); B's `threads/search` returns 0, A's returns 1. Assistants: both read (1 visible), B create/patch → `403`. Files: unauth `401`; A and B both upload `cv.pdf`, each reads only their own bytes, keys are `users/<A>/…` vs `users/<B>/…` (SeaweedFS filer). Store: both `PUT` `["career_agent","memory"]/preferences.md`; a `store`-table query shows `<A>.career_agent.memory` vs `<B>.career_agent.memory`, plus the untouched legacy `career_agent.memory`.
4. **Boot guard**: `REQUIRE_AUTH=true` with `LANGGRAPH_AUTH` unset → backend refuses to start.
5. **Tests**: `cd backend && uv run pytest` (incl. `tests/server/test_filters.py`, `tests/career_agent/test_scope.py`, `tests/test_files_api_auth.py`); `pnpm --dir frontend test` (auth components, token plumbing, config pin).
