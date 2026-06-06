# PRD: Long-term user-preference memory (v1)

**Status:** shipped · **Scope:** Backend (career-agent memory)

## Why

The career agent is configured by files — `AGENTS.md` (procedure + brand voice) is always loaded as
memory (PRD 08). But that memory is *repo-owned* and identical for everyone; there was no per-user
**personalization**. A user who wanted salary ranges in research, AI skills emphasized in the resume,
predicted Q&A in interview prep, or a terse battlecard had to re-state it on every prep run. We wanted
Claude-Code-style auto memory: notice a durable preference once, persist it, and apply it on every
future run — as a low-latency *add-on*, not a feature that taxes every turn. Only the main agent owns
memory; subagents stay stateless and receive the relevant preferences in their task input.

## What the user sees

No new UI — it's behavioral. When the user states a standing preference ("from now on keep my
battlecards concise", "always include the company's salary range", or an explicit "remember…"), the
agent saves it, gives a one-line ack ("Noted — I'll keep battlecards concise from now on."), and then
honors it automatically on this run and every future one (research / tailored resume / interview prep /
battlecard). Preferences **persist across conversations** — a brand-new chat already knows them. They
live in a human-readable, user-editable file at `/memory/preferences.md` (visible in Workspace > Files),
grouped by stage heading.

Deliberately *not* saved: one-off this-run-only instructions ("for THIS resume, drop the Twitter link"),
anything derivable from the CV/JD, and pair-specific notes (those stay in `/processed/` and the intake
file). And deliberately *not built*: a per-fact memory store, a new tool/button, or an `ls`/`grep`
discovery flow — see below.

## How — the key architectural choices

- **One always-loaded file, not per-file + index.** The original plan mirrored Claude Code: a file per
  preference (`/memory/<slug>.md`) plus an always-loaded `MEMORY.md` index. Live testing killed it —
  gpt-5.4 would not maintain that shape by prompting alone: it wrote the preference into `AGENTS.md`,
  wrote nothing (just acknowledged), or — once the index was seeded — collapsed everything into the
  single index file and skipped the per-slug file. The model strongly gravitates to *one* file. So
  preferences live in a single `/memory/preferences.md`, sectioned by stage. Retrieval and application
  cost **0 tool calls** (it's in the system prompt every session); saving is **one `edit_file`**.
- **Loaded as a memory source, not discovered at runtime.** `memory=["AGENTS.md", "/memory/preferences.md"]`
  injects the file into the system prompt. The drafted alternative — `ls` → `grep` description → `read`
  on every turn — costs ~3 round-trips *each time* the agent wants preferences. deepagents loads memory
  sources once per thread, so always-loading is free per turn and is how Claude Code's own index works.
- **Seed the file from a `before_agent` hook, never the LLM.** The agent reliably *edits* an existing
  preferences file but cannot reliably *create* one on a clean slate (it fumbles toward `AGENTS.md` or
  starts the intake workflow). `EnsurePreferencesFileMiddleware` writes the scaffold in
  `before_agent`/`abefore_agent` — a single cheap store write (no model round-trip → no added latency),
  idempotent because the backend's `write` refuses to overwrite an existing file. This is what makes
  saving reliable: the model only ever has to edit.
- **Apply by folding preferences into subagent task descriptions.** Subagents have no memory; the main
  agent copies the relevant preference lines into each subagent's task input (which subagents already
  treat as user instructions, per PRD 06). The battlecard, which the main agent builds itself, applies
  them directly. `AGENTS.md` Stage 3/4/6 templates carry a `User preferences:` reminder at each
  delegation point.

## Files of interest

| Concern | Path |
|---|---|
| The memory contract — what to remember, when to save, how to apply | `backend/app/career_agent/prompts.py` (`MEMORY`, block 7) |
| Preferences file wired as a 2nd always-loaded memory source | `backend/app/career_agent/agents.py` (`create_deep_agent(memory=…)`) |
| Monkey-patch that makes deepagents use our `MEMORY` prompt | `backend/app/career_agent/agents.py` (`_apply_prompt_overrides`) |
| Seeds the `/memory/preferences.md` scaffold before the model runs | `backend/app/career_agent/middleware.py` (`EnsurePreferencesFileMiddleware`, `PREFERENCES_PATH`, `_PREFERENCES_SCAFFOLD`) |
| `User preferences:` reminders at the delegation points | `backend/app/career_agent/AGENTS.md` (Stage 3 / Stage 4 / Stage 6) |
| Guards: `MEMORY.format()`, override reaches middleware, seeder | `backend/tests/career_agent/test_prompts.py`, `test_middleware.py` |

## Decisions worth remembering

- **Single-file won because the model wouldn't cooperate with per-file, not because it's simpler.** The
  user explicitly chose per-file first (granularity/auditability); we switched only after four live
  attempts proved gpt-5.4 won't bootstrap or maintain it by prompt. If per-file is ever wanted, the
  reliable path is a `save_preference(slug, description, body)` helper tool that writes the file and the
  index in one Python step — moving the fragile two-write dance out of the LLM.
- **Saving had to be made imperative *and* exempt from "don't write files yet."** A soft "save
  preferences" prompt produced only a prose ack. The fix: "when a trigger fires you MUST record it in
  `/memory/preferences.md` in the SAME turn — replying in prose is a failure," plus an explicit override
  of the Stage-1 intake rule (that rule is about CV/JD artifacts; a preference needs no CV/JD), plus a
  blunt "never write preferences into AGENTS.md" because the agent's first instinct was to append there.
- **deepagents 0.6.3+ freezes the memory prompt into a kwdefault — the patch must reach it.**
  `setattr(_mem_mw, "MEMORY_SYSTEM_PROMPT", …)` worked on 0.6.1 (read as a bare module global) but
  silently no-op'd on 0.6.3+, which captures the prompt into `MemoryMiddleware.__init__.__kwdefaults__["system_prompt"]`
  (same trap as Todo/SubAgent middleware). A container-vs-lock version skew (image shipped 0.6.3 while
  `uv.lock` pinned 0.6.1) hid it in local tests. `_apply_prompt_overrides` now patches the kwdefault too,
  guarded so it's a no-op on versions without the param. `test_memory_prompt_override_reaches_the_middleware`
  guards both paths. (Skew later resolved by aligning everything to 0.6.8.)
- **Memory is a once-per-thread snapshot, on purpose.** `MemoryMiddleware.before_agent` loads sources
  once and caches them in `memory_contents` state; `modify_request` re-injects that cache every call but
  never re-reads. So a mid-thread save is *not* reflected in that thread's system prompt — the agent
  already has it in conversation context — and a new thread reloads it fresh. This stability is what keeps
  the system-prompt prefix cacheable (`add_cache_control=True`); a per-turn reload would bust the cache.
- **No delete primitive is needed.** deepagents exposes no delete tool/method; with single-file,
  "remove a preference" is just editing out its bullet — which sidesteps the orphaned-tombstone problem
  a per-file design would have hit.

## Deferred (intentional non-goals for v1)

- **Per-user namespacing.** The memory namespace is the constant `("career_agent","memory")` → a single
  global store. Correct for a solo-user product (preferences persist across every thread). Multi-tenant
  use would derive the namespace from a user id (the `StoreBackend` namespace factory already receives
  runtime/config).
- **Per-fact / index granularity.** Rejected for v1; revisit only if preferences grow large or numerous
  enough that one always-loaded file becomes a real token cost — then load an index and read bodies on
  demand, paired with a `save_preference` helper tool to keep the writes reliable.
- **End-to-end apply verification.** The save → cross-thread recall loop is verified; folding prefs into a
  full multi-subagent prep run was not exercised (a costly real run). Recall confirms prefs reach the
  orchestrator's context, and the AGENTS.md templates wire the hand-off.
- **A `delete_file` tool.** Unnecessary while single-file. Worth adding only alongside a per-file design.

## How to verify end-to-end

1. `cd backend && uv run pytest tests/career_agent/test_prompts.py tests/career_agent/test_middleware.py` — green.
2. `pre-commit run --files backend/app/career_agent/{agents,prompts,middleware}.py backend/app/career_agent/AGENTS.md` — ruff + `ty` pass.
3. `docker compose up -d`; grab the LangGraph host port from `docker ps`. Backend hot-reloads — confirm `graph_id=career_agent` re-imports cleanly in `docker logs next-role-backend-1`.
4. From a clean store, send any message ("hi") → `EnsurePreferencesFileMiddleware` creates `/memory/preferences.md` (check Workspace > Files, or the `next-role-postgres` store under namespace `career_agent/memory`).
5. State a standing preference ("from now on keep my battlecards very concise") → the agent makes **one** `edit_file` under the matching section, leaves `AGENTS.md` untouched, and acks in one line.
6. Open a **new thread** → ask what it already knows about your preferences → it recalls them with **0 tool calls** (they're in the loaded `<agent_memory>` block).
7. (Patch sanity) `docker exec next-role-backend-1 python -c "import deepagents.middleware.memory as m; from backend.app.career_agent import agents; print('It contains two things' in (m.MemoryMiddleware.__init__.__kwdefaults__ or {}).get('system_prompt',''))"` → `True` (the effective prompt is ours, not the deepagents default).
