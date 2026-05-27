# PRD: Configurable LLM Models (v1)

**Status:** shipped · **Scope:** career_agent (backend) + Settings dialog (frontend)

## Why

The main agent and every declarative subagent were hardcoded — `_MODEL = "openai:gpt-5.4"` in `agents.py` and `model: openai:gpt-5.4-mini` per entry in `subagents.yaml`. Trying a different provider (Anthropic via Bedrock, Google, etc.) meant editing source and rebuilding the backend image. Cheap A/B experiments — "does Sonnet-4.6 produce a better tailored resume than gpt-5.4?" — were too expensive to attempt casually, so they didn't happen.

## What the user sees

A new **Models (Optional)** section in the Configuration dialog, divided from the deployment fields by a horizontal rule. Two free-text inputs:

- **Main agent** — placeholder `openai:gpt-5.4`.
- **Subagents** — placeholder `openai:gpt-5.4-mini` (one value applied to all declarative subagents).

Above them, one shared help line: `Format <provider>:<model> — e.g. anthropic:claude-sonnet-4.6. Leave blank to use the default. See all supported providers.` — the trailing link opens the `init_chat_model` reference docs in a new tab.

Blank field → the agent's bake-time default still wins. Settings persist in `localStorage` under the existing `deep-agent-config` key. A typo (`not-a-real-provider:foo`) does not crash the run — the backend logs a warning and falls back to the default.

## How — the key architectural choices

**`@wrap_model_call` middleware that reads `configurable.{main_agent_model,subagent_model}` and calls `request.override(model=init_chat_model(...))`.** Picked over LangGraph's typed `context_schema=` channel because the JS SDK 1.9.2 (`@langchain/langgraph-sdk`) doesn't expose a top-level `context` parameter on remote `stream.submit` calls — only `config.configurable`. Going with `context_schema` would have forced the frontend into an undocumented workaround. The middleware reads `RunnableConfig` via `langgraph.config.get_config()` (the `Runtime` injected into middleware deliberately does not include `config` — only `context`/`store`/etc.).

**Middleware threaded into the main agent AND every declarative subagent, not just the main agent.** This is the gotcha that bit the first cut: deepagents builds each declarative subagent via its own `create_agent(..., middleware=spec.get("middleware", []))` in `deepagents/middleware/subagents.py:650` — declarative subagents do **not** inherit the parent agent's middleware list. Until the fix, setting Subagents=… silently no-op'd. The override middleware is instantiated once and passed to both `create_deep_agent(middleware=…)` and `load_subagents(default_middleware=…)`.

**Main vs subagent routing via `metadata.lc_agent_name`.** Deepagents stamps that key onto every subagent runnable (`with_config({"metadata": {"lc_agent_name": name}})`). Absent in the runtime config → it's a main-agent call → use `main_agent_model`. Present → use `subagent_model`. No separate middleware classes needed.

## Files of interest

| Concern | Path |
|---|---|
| Override middleware | `backend/app/career_agent/middleware.py` (`ModelOverrideMiddleware`, `_resolve_model`) |
| Wiring main + subagent share | `backend/app/career_agent/agents.py` (`_model_override_middleware`, lines ~125–150) |
| Subagent middleware threading | `backend/app/career_agent/utils.py` (`load_subagents` `default_middleware=` param) |
| Settings UI section | `frontend/src/app/components/ConfigDialog.tsx` (`ModelsSectionHelp`, the **Models** block) |
| Persisted config shape | `frontend/src/lib/config.ts` (`mainAgentModel`, `subagentModel` on `StandaloneConfig`) |
| Per-submit config builder | `frontend/src/app/hooks/useChat.ts` (`buildSubmitConfig`, used by all five `stream.submit` sites) |

## Decisions worth remembering

- **`configurable` channel, not `context_schema`.** The LangGraph v1 docs recommend `context_schema=` for new code, but the JS SDK 1.9.2 doesn't carry a top-level `context` arg on `runs.stream` / `useStream().submit`. We use `config.configurable` — still the supported legacy path — and read it via `get_config()` inside middleware. Revisit once the JS SDK exposes `context` first-class.
- **Middleware on the main agent isn't inherited by declarative subagents.** Documented in this PRD because nothing in the deepagents docs warns about it, and the failure mode is silent (override accepted by the SDK, ignored by the subagent's own `create_agent`). The `default_middleware=` param on `load_subagents` mirrors the existing `default_tools=` shape so a future contributor sees the pattern.
- **One Subagents slot, not one per declarative subagent.** Considered exposing hiring-recon / resume-tailor / interview-coach individually. Rejected — the real workflow split is "main orchestrator (smart) vs grunts (cheap)", not per-subagent tuning. The middleware can branch on `lc_agent_name` later if needed.
- **Module-level `init_chat_model` cache, keyed by the override string.** A single run issues dozens of model calls; rebuilding the client each time would burn env-var reads and network plumbing. Cache lives for the worker's lifetime.
- **Free-text input + provider docs link, not a dropdown.** `init_chat_model`'s real provider list lives in `langchain.chat_models.base` and the docs deliberately don't mirror it exhaustively (it drifts). A hardcoded UI allowlist would lie within a release.
- **"Models" as a grouped section, not two inline rows.** First draft put each model input as a peer of the LangSmith API Key field with its own duplicated help line + link. Visually noisy and DRY-violating. Final: divider + section heading + one shared help line, then the two inputs with short labels.
- **Soft failure on bad strings.** `_resolve_model` returns `None` on any `init_chat_model` exception; the middleware passes the request through unchanged. A typo in Settings logs a warning and falls back — it does not brick the run.

## Deferred (intentional non-goals for v1)

- **Per-subagent model overrides.** Two slots cover the dominant use case; the middleware is ready to branch by `lc_agent_name` when someone needs it.
- **Migrating to `context_schema=`.** Will happen when `@langchain/langgraph-sdk` documents `context` on the remote SDK call surface.
- **Provider-credential UI.** Users still need `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / AWS creds in the backend `.env`. Exposing credential entry in the Settings dialog is both a security and a UX rabbit hole.
- **Cross-device sync.** Stored in `localStorage` like every other field on this dialog; not worth a backend persistence API for a personal tool.
- **Client-side validation of the `provider:model` string.** Backend handles bad strings gracefully; a regex would just drift from `init_chat_model`'s actual grammar.

## How to verify end-to-end

1. `docker compose up -d`; grab the frontend host port from `docker ps`.
2. Open the Settings dialog. Leave both Model fields blank, send a message, confirm normal completion. LangSmith trace (or container logs) shows the bake-time defaults on every model call.
3. Set **Main agent** to `bedrock_converse:global.anthropic.claude-sonnet-4-6`, save, start a fresh thread, send a message. Trace: main agent runs Anthropic; any subagent it spawns (e.g. `hiring-recon`) still runs `openai:gpt-5.4-mini`.
4. Set **Subagents** to a different model (e.g. `bedrock_converse:global.anthropic.claude-haiku-4-5-20251001-v1:0`), trigger a subagent. Subagent now runs Haiku; main agent still on Sonnet.
5. Set **Main agent** to `not-a-real-provider:foo`, send a message. Run completes (fallback to default); `docker compose logs backend | grep ModelOverrideMiddleware` shows the warning.
6. Refresh the browser, reopen Settings — both fields still populated from `localStorage`.
7. Backend unit tests: `cd backend && uv run pytest tests/career_agent/test_middleware_model_override.py tests/career_agent/test_utils.py` — 16 pass.
