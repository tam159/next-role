# PRD: Career-agent Stages 3–5 — recon, tailor, coach, battlecard

**Status:** shipped · **Scope:** career_agent only

## Why

The 5-stage career-agent workflow (intake → process → research → customize/prep → cheat sheet) had Stages 1–2 implemented in `CAREER_AGENT.md`; Stages 3–5 were `_Not yet implemented — placeholder._`. The main agent could parse a CV + JD and had nothing to do next. Also, after parsing it was reading only the first 100 lines of each processed file (the login-wall verification skim), which left it without enough substance to write good subagent task descriptions for delegation.

## What ships

Three subagents, one skill, and the per-stage procedure to orchestrate them:

- **`hiring-recon`** (`openai:gpt-5.4-mini`, `web_search` + `web_extract`) — gathers company snapshot, financial/hiring signals, reputation, hiring team, and role market context (with a salary range bracketed by candidate/JD location), then produces a match analysis. Single output: `/research/<resume>/<jd>.md`.
- **`resume-tailor`** (`openai:gpt-5.4-mini`, `prepare_render_settings`) — rewrites the resume tailored to one JD using the recon report, then renders it to PDF via `rendercv`. Carries the truth-vs-tailoring guardrail and the three reorder/swap/keyword strategies. Outputs: `/tailored_resume/<resume>/<jd>.yaml` (rendercv source, hand-editable) + `/tailored_resume/<resume>/<jd>.pdf` (rendered PDF) + `/render_intermediate/<resume>/<jd>.typ` (intermediate, hidden from UI). See PRD 07.
- **`interview-coach`** (`openai:gpt-5.4-mini`, filesystem only) — structured prep doc with a top-level Self-introduction (60s + 30s, reused across rounds), optional `## Triage` when the timeline is short, and per-round STAR + questions-to-ask + watch-outs. Output: `/interview_coach/<resume>/<jd>.md`.
- **`interview-battlecard` skill** — Stage-5 workflow that the main agent applies directly (no subagent). Day-of one-pager with exactly four sections per round (Stories ready / Company facts to drop / Questions to ask / Watch-outs), pages separated by `\n---\n`. Output: `/interview_battlecard/<resume>/<jd>.md` (FilesystemBackend).

Each subagent's workflow lives in its own `skills/<subagent>/<subagent>/SKILL.md` (see PRD 06); the YAML system_prompt is a ~7-line pointer to the skill plus the single-shot rule and the exact one-line final-reply contract back to the main agent. The `interview-battlecard` skill sits under `skills/career-agent/interview-battlecard/` (the main agent's consumer grouping).

`CAREER_AGENT.md` Stages 3–5 now carry concrete delegation procedures with full task-input templates. Stage 2 gained a "Load full context before delegating" step (read both processed files in full with `limit=1000`). The CompositeBackend route `/interview_prep/` was renamed to `/interview_coach/`; all other routes unchanged.

## How — the key choices

**Nested `<resume>/<jd>.md`, not flat.** Picked over `<resume>-<jd>.md` because the eventual binary artifacts (PDF resumes, PDF battlecards) want to sit next to their markdown sources in the same per-resume folder, and the user already had this layout drafted in the README.

**StoreBackend vs FilesystemBackend per artifact.** `/research/` and `/interview_coach/` are StoreBackend (KV-backed). `/tailored_resume/`, `/interview_battlecard/`, `/upload/`, and `/render_intermediate/` are FilesystemBackend — needed for the eventual binary outputs (PDFs need real files; the YAML/typ render pipeline already exists for `/tailored_resume/`). All route prefixes now use a leading slash after the FilesystemBackend leading-slash refactor.

**Subagents avoid `write_todos` via the system prompt, not framework config.** deepagents injects `TodoListMiddleware` into every subagent with no per-subagent off-switch in `SubAgent`. The harness-profile `excluded_middleware` mechanism exists but would also drop the tool from the main agent. Instead each subagent's prompt opens with `Do not call write_todos — your task is single-shot.` Zero framework risk.

**Intake filename keyed by resume×JD pair, not just JD.** `/processed/<resume>-<jd>-intake.md` future-proofs the case where the same JD is targeted with two CV variants (e.g. an "AI-leaning" vs "infra-leaning" resume) and the user wants different intake notes per pair.

**hiring-recon includes match analysis.** Considered splitting (pure recon → match analysis derived later in resume-tailor). Kept combined because the analyst already has the resume + JD in context, and downstream subagents start from a richer baseline instead of re-deriving the same gap matrix twice.

**Stage 4 spawns both subagents in parallel.** CAREER_AGENT.md's Stage 4 instructs the main agent to spawn `resume-tailor` and `interview-coach` "in parallel so they can run concurrently" and lists the five shared paths each subagent receives in its task input. The main agent doesn't need to read the recon report itself — it just forwards `research_path` to both subagents.

## Files of interest

| Concern | Path |
|---|---|
| Subagent specs (thin pointers to skills) | `backend/app/career_agent/subagents.yaml` |
| Subagent loader (tool whitelist + `skills:` passthrough) | `backend/app/career_agent/utils.py` (`load_subagents`) |
| Tool pool + StoreBackend route rename | `backend/app/career_agent/agents.py` (`_SUBAGENT_TOOLS`, `_SUBAGENT_DEFAULT_TOOLS`; `/interview_prep/` → `/interview_coach/`) |
| Stages 3–5 procedures + Stage 2 full-context read | `backend/agents/career_agent/CAREER_AGENT.md` |
| 5-stage summary in main agent system prompt | `backend/app/career_agent/prompts.py` (`SYSTEM_PROMPT`) |
| Subagent workflows | `backend/app/career_agent/skills/{hiring-recon,resume-tailor,interview-coach}/<same>/SKILL.md` |
| Battlecard skill (main agent) | `backend/app/career_agent/skills/career-agent/interview-battlecard/SKILL.md` |
| Flow + File Structure resync | `backend/app/career_agent/README.md` |

## Decisions worth remembering

- **Names matter; rename cascades.** First pass used `researcher` / `custom-resume` / `interview-prep` / `interview-cheat-sheet`. User correction: too generic. Final names — `hiring-recon` / `resume-tailor` / `interview-coach` / `interview-battlecard` — were chosen for thematic coherence (recon → tailor → coach → battlecard reads as a single kit). The rename cascaded into directory names, the `/interview_coach/` backend route, the README, and the SYSTEM_PROMPT. Budget for that whenever naming changes.
- **Full-file read before delegating.** The 100-line read in Stage 2's login-wall check was being mis-applied as the agent's only context for Stage 3 delegation. Fixed by adding an explicit "Load full context" step at the end of Stage 2 with `limit=1000`.
- **Subagents take exact paths in the task input.** Subagents run in a fresh context. The main agent's job is to construct a task description that contains every path the subagent needs — the subagent does not hunt for files. Stated as the standard pattern in CAREER_AGENT.md Stages 3 and 4.

## Followups that shipped

- **Workflows as shareable units** → **PRD 06.** Subagent workflows moved out of inline `system_prompt:` into per-consumer `skills/<subagent>/<subagent>/SKILL.md`. The YAML system_prompt collapsed to a ~7-line pointer + single-shot rule + final-reply contract.
- **PDF rendering for the tailored resume** → **PRD 07.** `resume-tailor` now writes a rendercv YAML and renders to `.pdf` (with a hidden `.typ` intermediate under `/render_intermediate/`). The battlecard is still `.md` — `/interview_battlecard/` stays on FilesystemBackend so its eventual PDF output has somewhere to land.
- **Consolidated post-refactor snapshot** → **PRD 08.** [Career-Agent Workflow Orchestration (v2)](08_agent_workflow.md) folds this PRD plus the two follow-ups above into a single canonical reference for how the 5-stage workflow currently runs end-to-end.

## How to verify end-to-end

1. `pre-commit run --files $(git ls-files --modified --others --exclude-standard)` — clean.
2. Loader smoke test (tool pool now owned by `agents.py`, so pass it in):
   ```
   PYTHONPATH=/Users/may/tech/next-role backend/.venv/bin/python -c "
   from backend.app.career_agent.agents import _SUBAGENT_TOOLS, _SUBAGENT_DEFAULT_TOOLS
   from backend.app.career_agent.utils import load_subagents
   from pathlib import Path
   subs = load_subagents(Path('backend/app/career_agent/subagents.yaml'), tools=_SUBAGENT_TOOLS, default_tools=_SUBAGENT_DEFAULT_TOOLS)
   print([(s['name'], s.get('skills')) for s in subs])"
   ```
   prints three entries: `('hiring-recon', ['skills/hiring-recon/'])`, `('resume-tailor', ['skills/resume-tailor/'])`, `('interview-coach', ['skills/interview-coach/'])`.
3. `docker compose up -d`, upload a CV + JD URL, walk all 5 stages and confirm:
   - `/research/<r>/<j>.md` has the seven mandated sections including the **Salary range** bullet with a location qualifier.
   - `/tailored_resume/<r>/<j>.yaml` opens with a `# changes:` comment block, and `/tailored_resume/<r>/<j>.pdf` exists next to it.
   - `/interview_coach/<r>/<j>.md` opens with `## Self-introduction` (60s + 30s) above the rounds.
   - `/interview_battlecard/<r>/<j>.md` has N pages separated by `\n---\n`, four sections each.
   - LangSmith trace shows each subagent's first tool call is `read_file` on its `SKILL.md`, and no subagent calls `write_todos`.
