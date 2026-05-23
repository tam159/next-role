# PRD: Subagents-with-Skills Refactor (v1)

**Status:** shipped · **Scope:** career_agent only

## Why

The three career-agent subagents (`hiring-recon`, `resume-tailor`, `interview-coach`) carried their entire workflow as an inline `system_prompt:` block in `subagents.yaml` — ~100, ~90, ~100 lines respectively. That made the prompts un-shareable: if a second agent ever needed the same workflow (a future "junior-resume-tailor", a shared interview-prep flow used by another product surface), the only option was copy-paste-and-drift. The main agent's `interview-battlecard` workflow already lived as a `SKILL.md` and benefitted from progressive disclosure (one-line manifest in system prompt, full body read on demand). Goal: bring the subagents to the same shape so workflows become reusable units of agent configuration instead of YAML scalars locked to one caller.

## What the user sees

No user-visible change — same flow, same outputs, same delegation tree. Internally the LangGraph trace now shows each subagent's first tool call as a `read_file` on its own `SKILL.md`, after which it executes the workflow it just loaded. Round-trip cost: one extra tool call per delegated stage, in exchange for prompts that any other agent can adopt by listing the same `skills/` path.

## How — the key architectural choices

**Per-consumer skill grouping under `skills/`, not flat.** Picked `skills/<consumer>/<skill-name>/SKILL.md` (main agent under `skills/career-agent/`, each subagent under `skills/<subagent-name>/`) over a flat `skills/` where every consumer sees every skill. deepagents' `SkillsMiddleware` injects the manifest (name + description) of every skill at the source path into the system prompt every turn — flat layout means each subagent's prompt would carry three irrelevant skill manifests, plus the temptation to invoke a skill that isn't its job. The grouping costs one extra directory level; the LLM ergonomics and per-turn token budget are worth it. `sources` in deepagents has to be a parent directory of skill folders (`backend.ls(source)` scans for subdirs), so a true flat layout is the only alternative — the nested `skills/hiring-recon/hiring-recon/SKILL.md` shape is the price of selective inclusion.

**Skill ≠ subagent. Workflow goes in `SKILL.md`; subagent↔caller interface stays in `system_prompt:`.** What lives in the SKILL: inputs section, tool guidance, decision rules, output format. What lives in the subagent YAML: role identity, a one-line pointer to read the skill, the single-shot rule (`do not call write_todos`), and the exact one-line final-reply contract back to the main agent. The split is principled: workflow is reusable across agents, but a different caller invoking the same skill won't need the same single-shot policy or the same "Wrote X to: <path>" handshake. Each subagent's system prompt collapsed from ~100 lines to ~7.

**Subagents get their own `SkillsMiddleware`; nothing inherits from the parent.** deepagents' `SubAgent` TypedDict (`deepagents/middleware/subagents.py:88-89`) accepts an optional `skills: list[str]`, and `create_deep_agent` builds a dedicated `SkillsMiddleware` per subagent (`deepagents/graph.py:557-559`). The main agent's `skills=["skills/career-agent/"]` does **not** propagate. We added a one-line passthrough in `load_subagents` and let deepagents do the rest — no path resolution on our side, no skill loading code to maintain.

## Files of interest

| Concern | Path |
|---|---|
| Skills passthrough in subagent loader | `backend/app/career_agent/utils.py` (`load_subagents`) |
| Subagent specs (now short) + `skills:` field | `backend/app/career_agent/subagents.yaml` |
| Main agent's source path | `backend/app/career_agent/agents.py` (`skills=["skills/career-agent/"]`) |
| Filesystem-tool prompt — `mkdir` warning for `write_file` / `overwrite_file` | `backend/app/career_agent/prompts.py` (`FILESYSTEM` block) |
| Moved skill | `skills/career-agent/interview-battlecard/SKILL.md` |
| New skills | `skills/{hiring-recon,resume-tailor,interview-coach}/<same>/SKILL.md` |
| Unit tests | `backend/tests/career_agent/test_utils.py` |

## Decisions worth remembering

- **Per-consumer grouping over flat.** Already covered above as the central architectural choice — recorded here because the temptation will recur when the next reusable workflow lands. If a skill ends up genuinely shared across two consumers, declare both paths in the `skills:` lists rather than collapsing to a flat layout.
- **`career-agent/` over `main/` as the grouping-folder name.** First pass used `main/`. User correction: the main agent has a name (`career-agent`) — use it. Future agents in this repo will sit under sibling folders matching their own names, not a generic "main".
- **One-line subagent prompts, but interface rules stay.** First pass moved the single-shot rule and the final-reply contract into SKILL.md alongside the workflow. User correction: those govern the subagent↔caller handshake, not the workflow, so they belong in the YAML next to the role line. Tracking this in `feedback_subagent_prompt_minimal.md` (auto-memory) so future migrations don't repeat the misclassification.
- **`mkdir` instruction in the FILESYSTEM prompt, not just docstrings.** Caught during dogfooding: the main agent ran `execute("mkdir -p /interview_battlecard/<resume>")` before `write_file`. `FilesystemBackend.write()` (`deepagents/backends/filesystem.py:434`) already does `parent.mkdir(parents=True, exist_ok=True)`, and `StoreBackend` is a namespaced KV with no directory concept — the `mkdir` was always a no-op. Added "parent directories are created automatically, do NOT run `mkdir`" to both `write_file` and `overwrite_file` in the patched `FILESYSTEM` system-prompt block (`prompts.py`). System-prompt note beats docstring-only because the prompt is in-context every turn; docstrings are read once at registration.
- **`overwrite_file` lives in the FILESYSTEM block too, even though it's a custom tool.** Mild coupling between our app code and a deepagents constant we're already patching, in exchange for grouping all seven filesystem tools the agent actually has in one place — same listing the LLM scans when picking a tool.

## Deferred (intentional non-goals for v1)

- **Generalising the loader.** `load_subagents` still hard-codes the `available_tools` dict (`web_search`, `web_extract`). When a second tool family arrives, generalise to a registry — but not pre-emptively.
- **Cross-subagent skill sharing.** If `hiring-recon` and `interview-coach` ever need the same sub-workflow (e.g. a shared "STAR formatting" skill), the right move is to put it under its own grouping folder and add the path to both subagents' `skills:` lists. Not built yet because no two subagents share a sub-workflow today.

## How to verify end-to-end

1. `cd backend && uv run pytest tests/career_agent/test_utils.py` — four tests green (skills passthrough, omission, existing-field mapping, unknown-tool failure).
2. `docker compose up -d`, send a chat asking to process a CV + JD and run the full flow.
3. Open the LangGraph trace for one of the subagent spawns. Confirm:
   - The subagent's system prompt's "Available Skills" manifest lists **only** its own skill (e.g. only `hiring-recon` for the hiring-recon subagent, no `interview-battlecard`).
   - The subagent's first tool call is `read_file("/skills/<consumer>/<skill>/SKILL.md", limit=1000)`.
   - The expected output file lands at the path the main agent passed in the task description.
4. Open the main agent's trace. Confirm its skill manifest lists only `interview-battlecard` — no leakage of the three subagent skills.
5. Confirm **no** `execute("mkdir -p …")` calls appear before any `write_file` / `overwrite_file` in the trace.
