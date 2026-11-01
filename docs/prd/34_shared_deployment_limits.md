---
type: PRD
title: "Shared-Deployment Limits & Single-VPS Cloud Stack"
description: "Env-flagged limits that are off by default (Google-only login, a server-side model lock, and per-user plus global run/spend quotas metered from every model call) and a Caddy + Cloudflare R2 production stack for one VPS, so a $100 API credit can serve a closed beta while local clones keep every capability."
tags: [infra, auth, llm, backend]
timestamp: '2026-10-31T23:18:36+07:00'
status: "shipped"
scope: "full stack (auth + model middleware + usage ledger + composer quota UI + deploy/vultr)"
version: v1
---

**Extends:** [Multi-User Authentication & Per-User Isolation](26_multi_user_auth.md), [Configurable LLM Models](15_configurable_llm_models.md), [Object Storage for Binary Artifacts](25_object_storage_artifacts.md), [Shell Execution Off by Default (SANDBOX_PROVIDER=none)](33_execute_off_by_default.md)

# Why

The first cloud deploy (one Vultr VPS in Singapore, Cloudflare R2 for artifacts, core app only) runs on a fixed $100 Anthropic API credit. [PRD 26](26_multi_user_auth.md) already gave isolation, but nothing bounded cost. Any signed-in user could pick a pricier model through [PRD 15](15_configurable_llm_models.md)'s override, start unlimited runs, and sign up with email + password. A run count alone does not bound spend either: a full prep (research, tailored resume, interview prep, battlecard) costs about $1–2.50 on Sonnet 5.5, while a follow-up costs about $0.03, so "50 messages a week" could still mean $100 from one user.

NextRole is also open source, so none of this may change what a fresh clone does. Every limit had to be an env flag that defaults to today's behaviour.

# What the user sees

On a deployment with the limits on:

- **Login:** only "Continue with Google". The email form, the "or" divider and "Create an account" are gone, and a Google error now shows inline.
- **Settings:** no Model section.
- **Composer footer:** "N runs left today" when run limits are set.
- **Over quota:** the composer locks, with a status line naming the window and the local time when a run can start again ("You've used all your runs for the last 24 hours. You can start a new run after Sun 10:59 PM.").
- **Server refusal:** a refusal the UI did not predict (a second tab, a race) shows as a toast with the server's reason.

Locally nothing changes: any model, both login methods, no footer hint, no lock.

Deliberately absent: dollar amounts in the UI, a usage page, admin overrides, and any per-user exemption.

# How — the key architectural choices

**Each limit is enforced where the client cannot bypass it.** Hiding the model field is cosmetic. `MODEL_OVERRIDE_ENABLED=false` makes `ModelOverrideMiddleware._read_config` drop both `configurable` keys, because anyone holding a session JWT can POST a run asking for any model. `AUTH_EMAIL_PASSWORD_ENABLED=false` feeds Better Auth's `emailAndPassword.enabled`, so the endpoints are disabled, not just unrendered. Quotas are checked in a `@auth.on.threads.create_run` handler. That handler is the most specific one for run creation, so it also has to do what `authorize_threads` did (stamp `owner`, return the owner filter). Every way to start a run passes through it: REST `/runs`, `/runs/wait`, and the v2 protocol's `run.start` command (`event_streaming/service.py` → `create_valid_run` → `Runs.put` → `handle_event("create_run")`).

**Metering is a LangChain callback bound to the compiled graph, not middleware.** deepagents does not pass custom middleware to the auto-added general-purpose subagent or the summarization calls, so a `wrap_model_call` meter would miss real spend. `with_usage_metering` calls `graph.with_config(callbacks=[UsageMeter()])`. `Pregel.with_config` returns a `Pregel`, which the server accepts, and callbacks reach every nested chat-model call. The owner is resolved in `on_chat_model_start` from the run config contextvar (`scope.current_identity()`). It cannot come from callback `metadata`, because LangGraph strips configurable keys containing `auth` from metadata. With no limits set, the graph is returned unchanged.

**One append-only ledger and rolling windows.** `usage_ledger` holds a `run` row per accepted run and an `llm` row per model call (tokens plus estimated cost at list prices). Rolling 24-hour and 7-day windows avoid a timezone setting, and "resets at" is the oldest row in the window plus the window length. Rows outlive deleted threads, so deleting history never refunds usage. The checks run under a per-owner `pg_advisory_xact_lock`, so a double-submit cannot slip past a run limit.

# Files of interest

| Concern | Path |
|---|---|
| Limits, pricing, verdict policy, ledger SQL, meter | `backend/agents/usage.py` (`QuotaSettings` ~57, `_PRICES` ~94, `TokenUsage.from_metadata` ~139, `evaluate` ~228, `check_and_record_run` ~374, `UsageMeter` ~466, `with_usage_metering` ~530) |
| Run-creation gate (429 + `Retry-After`) | `backend/agents/auth.py` (`authorize_create_run`, lines ~126–155) |
| Model lock | `backend/agents/career_agent/middleware.py` (`model_override_enabled` ~274, `_read_config` ~333) |
| Graphs bound to the meter | `backend/agents/career_agent/agents.py`, `backend/agents/analytics_agent/agents.py` (module tail) |
| `GET /usage` for the composer | `backend/agents/files_api.py` (`usage_status`, line ~358) |
| Composer quota state, toast parsing | `frontend/src/app/hooks/useUsage.ts`, `frontend/src/app/components/ChatInterface.tsx` (~203), `frontend/src/app/hooks/useChat.ts` (`sendMessage`) |
| Google-only login | `frontend/src/lib/auth/enabled.ts`, `frontend/src/lib/auth/server.ts`, `frontend/src/app/login/page.tsx` |
| Model fields hidden | `frontend/src/lib/config.ts` (`isModelOverrideEnabled`), `frontend/src/app/components/ConfigDialog.tsx` |
| Prod image (`prod` stage; `dev` stays the default) | `frontend/Dockerfile`, `frontend/next.config.ts` (`output: "standalone"`) |
| Production stack + manual | `deploy/vultr/docker-compose.yml`, `deploy/vultr/Caddyfile`, `deploy/vultr/.env.example`, `deploy/vultr/README.md` |
| Tests | `backend/tests/test_usage.py`, `backend/tests/test_usage_integration.py`, `backend/tests/career_agent/test_middleware_model_override.py`, `frontend/src/app/hooks/useUsage.test.ts`, `frontend/src/app/login/page.test.tsx` |

# Decisions worth remembering

- **Off by default; production turns the limits on, not the other way round.** The first plan read as "make it deployable"; the correction was that a clone must keep every capability. So each flag defaults to current behaviour (`true` / `0`), the root `docker-compose.yml` passes the `NEXT_PUBLIC_*` mirrors with those defaults, and only `deploy/vultr/.env.example` carries the restrictive values. Prefer adding a new limit the same way over adding a mode switch like `APP_ENV=production`.
- **Cap spend as well as runs, plus a global ceiling.** The defaults are 10 runs per 24 hours and 30 per 7 days per user, $1.50 per 24 hours and $4 per 7 days per user, $3 per 24 hours for everyone, and 1 run at a time. The runs cap is the rule users understand; the dollar caps catch heavy runs. `QUOTA_GLOBAL_USD_PER_DAY=3` is the real guarantee that $100 lasts at least about 33 days. Checks happen when a run starts and an in-flight run always finishes, so the worst overshoot is one run.
- **Quotas need multi-user mode.** Enforcement lives in the auth layer, so with `LANGGRAPH_AUTH` unset limits are never enforced, `/usage` reports `{enabled: false}`, and `with_usage_metering` logs a warning if `QUOTA_*` is set anyway.
- **Parse Anthropic's TTL cache-write breakdown.** Live verification showed `langchain_anthropic` reporting writes as `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` with `cache_creation: 0`. Reading only `cache_creation` billed writes as plain input, about 20% too low on first calls. 1-hour writes are priced at 2× input. Unknown models are priced like Sonnet 5.x, never as free.
- **The ledger creates itself instead of joining `storage/migrations/`.** Those migrations are the vendored server's schema. `CREATE TABLE IF NOT EXISTS` under an advisory lock, run only when a limit is set, keeps local databases untouched. Each operation opens one short-lived psycopg connection rather than a pool, because calls are rare and this stays correct across event loops.
- **Two refusal shapes reach the browser.** REST gives the SDK's `HTTPError` (status 429, body `{detail}`). The v2 `run.start` command catches the exception and returns HTTP 200 `{type: "error", message: "429: …"}`, which surfaces as a `ProtocolError`. `quotaRefusalMessage` handles both. The SDK also retries a REST 429 up to 5 times (429 is not in its no-retry list), which is one more reason the composer locks proactively from `/usage`.
- **Separate frontend build stages instead of runtime env.** `NEXT_PUBLIC_*` values are inlined by `next build`, so production passes them as build args, and changing one needs a frontend rebuild. `dev` is the last stage, so a plain `docker build` and the local compose (`target: dev`) behave as before. `auth-migrate` reuses the dev image to run `auth@1.7.2 migrate --yes`; `server.ts` imports `./enabled` relatively so the CLI's loader needs no path aliases.
- **TLS from a Cloudflare Origin certificate, with port 80 closed.** Caddy runs with `auto_https off` behind the Cloudflare proxy ("Full (strict)"), and the Vultr firewall allows 443 only from Cloudflare. Caddy also 404s the unauthenticated meta routes and `/noauth`. The compose file, not `.env`, sets `REQUIRE_AUTH`, `LANGGRAPH_AUTH`, `disable_mcp` / `disable_a2a` and `CORS_ALLOW_ORIGINS`, so these cannot be forgotten.

# Deferred (intentional non-goals for v1)

- **Invite / allowlist in the app.** The closed beta uses Google OAuth "Testing" status (up to 100 listed test users) as a zero-code allowlist. Add `AUTH_ALLOWED_EMAILS` when the app is published beyond Testing.
- **Per-user overrides and an admin view.** Limits are global env values. Raise them per user only when someone needs it.
- **Account deletion and a privacy notice.** Handled by hand for the beta; both are needed before Google production publishing.
- **Analytics on the VPS.** The analytics stack and agent are excluded from `deploy/vultr`; they should return on a separate box reading Postgres through `analytics_ro`.
- **Exact billing.** Costs are list-price estimates from provider-reported tokens; the Anthropic Console spend limit stays the hard backstop.

# How to verify end-to-end

1. `cd backend && uv run pytest` (unit tests), `uv run pytest tests/test_usage_integration.py -m integration` (ledger SQL against the compose Postgres), and `pnpm --dir frontend exec vitest run`.
2. With your normal local `.env`, `GET /usage` with a bearer token returns `{"enabled": false}`, and the Settings dialog still shows the Model fields.
3. Recreate the backend with a compose override setting `QUOTA_RUNS_PER_DAY=2` and `MODEL_OVERRIDE_ENABLED=false`, then sign up a throwaway local user and take a JWT from `/api/auth/token`. Two `runs/wait` calls succeed; the first asks for `anthropic:claude-opus-5-5` and is still answered by `claude-sonnet-5-5`. `usage_ledger` holds two `run` and two `llm` rows with cache tokens and costs. The third `POST /runs` returns 429 with `Retry-After`, and `POST /threads/{id}/commands` `run.start` returns `"429: …"`.
4. Sign in as that user: the composer is locked, and the status line shows the local reset time.
5. Build the `prod` frontend target with `NEXT_PUBLIC_AUTH_EMAIL_PASSWORD_ENABLED=false` and run it: `/login` shows the Google button with no password field (about 48 MB of RAM). `docker compose -f deploy/vultr/docker-compose.yml config --quiet` passes with a filled `.env`.
6. Remove the override (`docker compose up -d backend`) and delete the test ledger rows.
