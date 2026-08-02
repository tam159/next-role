---
type: PRD
title: "deepagents 0.7 Behavioral Migration"
description: "Move prompt customization from monkey patches to deepagents 0.7's supported parameters, restore the opt-in write_todos, fix the silent per-turn preferences wipe, and align write-or-replace semantics — retiring overwrite_file."
tags: [backend, agent, workflow, memory]
timestamp: '2026-08-02T21:33:48+07:00'
status: "shipped"
scope: "Backend career agent (prompt architecture, middleware, backends, prompts, tests) + one frontend icon-map line"
version: v1
---

**Extends:** [Career-Agent Workflow Orchestration](08_agent_workflow.md), [Long-term user-preference memory](17_preference_memory.md), [Object Storage for Binary Artifacts](25_object_storage_artifacts.md)

# Why

PR #66 bumped `deepagents` 0.6.12 → 0.7.1 and CI stayed green — but 0.7 redefined the harness under us. The authored base prompt now assembles from an empty string, the filesystem/execution prompt constants were deleted, `TodoListMiddleware` left the default stack, and `write()` flipped from refuse-on-exists to write-or-replace. A LangSmith prompt diff plus a code audit showed the fallout: 9 of the 11 monkey patches in `_apply_prompt_overrides` were silently dead (Voice, How-you-work, the execute guardrail, and the filesystem notes had vanished from the live prompt), the system prompt instructed the model to call a `write_todos` tool that no longer existed (killing the frontend Plan panel's `todos` channel), and — worst — `EnsurePreferencesFileMiddleware`'s idempotency rested on 0.6's write refusal, so every agent turn was wiping `/memory/preferences.md` back to the empty scaffold. This migration makes the agent 0.7-native instead of re-patching it into a 0.6 shape.

# What the user sees

Mostly the absence of regressions: the Plan panel populates again on a prep run, saved preferences survive subsequent turns, and replies carry the coach Voice. Two real behavior changes: `write_file` now overwrites uniformly on every route (object-store paths used to error with "already exists"), and the custom `overwrite_file` tool is gone — the built-in subsumed it. One new capability: the agent has 0.7's `delete` tool (recursive, irreversible), prompt-gated to fire only when the user explicitly asks to remove something. Deliberately *not* restored: the Following Conventions / Filesystem Tools / Large Tool Results / Execute Tool prompt sections — 0.7 moved that guidance into the tool schemas, and re-adding it would forfeit the upgrade's ~65% input-token cut for prose the model already sees.

# How — the key architectural choices

- **Supported parameters instead of monkey patches — with one deliberate exception.** 0.7 merges `middleware=` entries by `.name`: an instance matching a default replaces it in place. So the custom memory prompt rides `MemoryMiddleware(system_prompt=…)` and the filesystem prose rides `FilesystemMiddleware(system_prompt=FILE_TOOLS, custom_tool_descriptions={"execute": stock + guardrail})`, both passed as override instances; todos ride `TodoListMiddleware(system_prompt=…)` from `langchain.agents.middleware`; Voice/How-you-work merged into `SYSTEM_PROMPT` (caller prompt is always first, and the base it used to append to is now empty). The exception is `SubAgentMiddleware`: `create_deep_agent` constructs it internally around already-compiled subagent graphs and never forwards a `system_prompt` (default `None` → no section), so the `## task` section keeps the one surviving kwdefaults patch (`_apply_task_prompt_override`) — building our own instance would mean re-implementing subagent compilation.
- **The Memory override needs its default to exist.** `memory=_MEMORY_SOURCES` stays on the call even though the override instance carries the same list: the parameter is what makes `create_deep_agent` construct the default `MemoryMiddleware` at the cache-friendly *tail* of the stack, which the named instance then replaces in place. Drop it and the override lands mid-stack, breaking the prompt-cache boundary. The instance's `sources` must equal that list — it is what actually loads.
- **Fix the preferences wipe with an existence probe, not a smarter write.** `EnsurePreferencesFileMiddleware` now does `read(PREFERENCES_PATH)` and seeds the scaffold only when `error is not None` — one cheap store read per run, one write ever. Alternatives (store-level put-if-absent, moving seeding to deploy time) would have coupled the middleware to a specific backend or lost the self-healing property [PRD 17](17_preference_memory.md) wanted. Regression tests run against a real `CompositeBackend`/`StoreBackend`/`InMemoryStore` stack precisely because the old stub-based tests modeled the 0.6 contract and could never have caught this.
- **An assembled-prompt snapshot test is the bump tripwire.** `agents.py` grew a `build_career_agent(model=…, store=…)` factory so `test_prompts.py` can build the *real* graph with a recording fake chat model, invoke once offline, and pin the exact system prompt sections and bound tool set. Override-by-name fails silently if upstream ever renames a class — this test fails loudly instead, before a LangSmith trace has to reveal it.

# Files of interest

| Concern | Path |
|---|---|
| Factory, override instances, the one remaining kwdefaults patch | `backend/agents/career_agent/agents.py` (`build_career_agent`, `_apply_task_prompt_override`) |
| Reorganized prompts: merged Voice, `FILE_TOOLS`, `EXECUTE_GUARDRAIL`; deleted `BASE`/`SKILLS`/`FILESYSTEM`/`EXECUTION` | `backend/agents/career_agent/prompts.py` |
| Existence-probe fix for preferences seeding | `backend/agents/career_agent/middleware.py` (`EnsurePreferencesFileMiddleware`) |
| Write-or-replace `write`, recursive `delete` | `backend/agents/career_agent/object_backend.py` |
| `_upsert` + `make_overwrite_file` removal; plain `backend.write` persistence | `backend/agents/career_agent/tools.py` (`parse_document`, `extract_jd`) |
| "do not call write_todos" clauses stripped | `backend/agents/career_agent/subagents.yaml` |
| Prompt-wiring tests incl. the assembled-prompt snapshot | `backend/tests/career_agent/test_prompts.py` (`test_assembled_prompt_and_tools_snapshot`) |
| Wipe regression tests on a real store stack | `backend/tests/career_agent/test_middleware.py` |
| `overwrite_file` → `write_file` in the model-facing procedure docs | `backend/agents/career_agent/CAREER_AGENT.md`, `skills/*/*/SKILL.md` |
| Icon-map regex drops `overwrite_file` | `frontend/src/app/components/ToolCallBox.tsx` (line ~39) |

# Decisions worth remembering

- **Lean restore over 0.6 parity** (user choice). Only prose with behavioral value came back: Voice/How-you-work, the todo section, a 3-bullet `FILE_TOOLS` note, and the "do NOT use `execute` to create or edit files" guardrail — the last appended to the stock `EXECUTE_TOOL_DESCRIPTION` via `custom_tool_descriptions` because it protects `CompositeBackend` route integrity (shell redirection writes to sandbox disk, silently bypassing the store/object-store routes). The custom `SKILLS` copy was deleted outright after a diff proved it identical to stock 0.7 text.
- **Keep the new `delete` tool** (user choice, against the audit's suppress-by-allowlist recommendation). Consistency demanded implementing `ObjectStoreBackend.delete` (exact key, else recursive prefix, mirroring `StoreBackend.delete`) — without it three routes would error where six succeed. The prompt gates use on explicit user request; no workflow stage calls it.
- **Retire `overwrite_file` completely instead of keeping it as an alias** (user choice). ~30 references rewritten across prompts, `CAREER_AGENT.md`, four `SKILL.md`s, tests, and the frontend regex. The tool existed only because 0.6's `write` refused overwrite ([PRD 02](02_document_processing.md)); with `write_file` now write-or-replace, an alias would cost a duplicate tool schema every turn. The original don't-make-the-LLM-regenerate-`old_string` rationale survives — procedures now point at `write_file` directly.
- **`ObjectStoreBackend.write` aligns to the framework contract rather than keeping the refusal.** Keeping 0.6 semantics on three routes would have made `write_file` error where its own 0.7 description promises overwrite — a model-facing lie pinned by tests. The tests locking the old "already exists" literal were rewritten, not appeased.
- **Fix dead patches, don't rewrite history.** The stale invariants this migration dissolved live in earlier PRDs (02, 04, 17, 25); they got short *"(Superseded by the deepagents 0.7 migration…)"* notes at the claim sites and inline fixes in acceptance steps, rather than silent rewrites — the PRDs are point-in-time records.
- **`HarnessProfile` rejected as the customization vehicle.** It is keyed per-model (this agent's model is runtime-overridable via `ModelOverrideMiddleware`, see [PRD 15](15_configurable_llm_models.md)), and its `base_system_prompt` overlay also replaces declarative subagents' authored prompts — the same clobbering concern that shaped the original patch design.

# Deferred (intentional non-goals for v1)

- **Dropping the custom `## task` section.** 0.7's `task` tool description already carries the subagent list and usage notes, so the section is partially redundant — but it kept working through the upgrade and removing it is a token-savings experiment, not a migration necessity. Revisiting it would also retire the last kwdefaults patch.
- **`delete`-driven workflows.** No stage or skill uses deletion; the tool exists for explicit user requests ("remove that old prep run"). Wire it into a skill only when a real cleanup flow appears.
- **Formal supersession of the annotated PRDs.** Their features still stand; only specific mechanism claims died. If a future rewrite replaces one wholesale, use the `16`/`18` supersession pattern then.

# How to verify end-to-end

1. `cd backend && uv run pytest` — 189 green, including `test_prompts.py::test_assembled_prompt_and_tools_snapshot` (pins Voice/todo/task/memory sections present, Following-Conventions/Large-Tool-Results/Execute sections absent, `write_todos` + `delete` bound, `overwrite_file` not, guardrail in the execute description) and the two `test_middleware.py` preserve-existing-content regressions (they fail on pre-migration code).
2. `docker compose up -d` (backend hot-reloads on save; if the Plan panel misses the `todos` channel after a schema change, `docker compose restart langgraph`).
3. Fresh prep run: the model calls `write_todos` turn one and the Plan panel populates; the LangSmith trace's system prompt opens with the career spine and ends with Voice/How-you-work ahead of the middleware sections.
4. Say "always include salary ranges in research" → `/memory/preferences.md` gains the bullet; send another message; the preference is still there (the pre-fix code wiped it here).
5. Ask for a battlecard JSON restructure → the agent uses `write_file` on `/interview_battlecard/...` and it succeeds on the second write (object-store route now overwrites).
6. Ask to delete a stale artifact by name → one `delete` call, gone from Workspace > Files; unprompted deletion never appears in traces.
