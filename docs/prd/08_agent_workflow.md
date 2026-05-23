# PRD: Career-Agent Workflow Orchestration (v2)

**Status:** shipped · **Scope:** career_agent only · **Extends:** [Document Processing (v1)](02_document_processing.md), [JD-from-URL Extraction (v1)](03_jd_url_extraction.md), [Tailored Resume PDF (v1)](07_tailored_resume_pdf.md), [Subagents with Skills (v1)](06_subagents_with_skills.md)

## Why

v1 of the workflow ([PRD 04 — Stages 3–5](04_initialize_subagents.md)) landed the original 5-stage spine: three subagents (`hiring-recon`, `resume-tailor`, `interview-coach`) plus the `interview-battlecard` skill, with each subagent's full workflow inlined as a `system_prompt:` block in `subagents.yaml` and the tailored resume emitted as plain markdown. v2 folds in two follow-on refactors that landed since: [Subagents with Skills (PRD 06)](06_subagents_with_skills.md) moved each subagent's workflow into a per-consumer `SKILL.md`, and [Tailored Resume PDF (PRD 07)](07_tailored_resume_pdf.md) wired `resume-tailor` to `rendercv` so the user gets a downloadable PDF instead of markdown. This PRD documents the consolidated state of the workflow as it currently runs end-to-end; it supersedes the per-iteration PRDs as the canonical reference for how the 5 stages fit together today.

Goal: a single chat thread takes the user from "here's my CV and the JD" to a finished interview kit (research report, tailored PDF resume, interview prep doc, day-of battlecard) without the agent stalling or fabricating intermediate files.

## What the user sees

A new session opens with one short message asking for all four intake inputs: CV upload, JD (file / URL / pasted), prep timeline, and any extra context. The agent does not parse anything until at least the CV and JD are provided. The CV and JD can be attached from the **chat composer** (paperclip icon) or from **Workspace > Files** (Upload button); both surfaces POST to `/api/files/upload` and land bytes at `/upload/<filename>` via the shared `.:/deps/next-role` volume mount.

Once both files are processed (Stage 2), the agent persists the prep timeline + role-side notes to `/processed/<resume-slug>-<jd-slug>-intake.md`, and appends any candidate-side notes ("skills not on my CV", "side project I shipped last month") to `/processed/<resume-slug>.md` under a dated `## Additional context` heading. From there the agent runs the rest of the arc autonomously:

- **Stage 3** — delegates to the `hiring-recon` subagent → `/research/<resume>/<jd>.md`.
- **Stage 4** — in one turn, spawns `resume-tailor` and `interview-coach` in parallel. `resume-tailor` writes the YAML at `/tailored_resume/<resume>/<jd>.yaml` and triggers `rendercv` to produce the `.typ` (under `/render_intermediate/`) and `.pdf` siblings. `interview-coach` writes the structured prep doc to `/interview_coach/<resume>/<jd>.md`.
- **Stage 5** — loads the `interview-battlecard` skill, reads the tailored resume + interview-coach prep + research report, and writes the day-of one-pager to `/interview_battlecard/<resume>/<jd>.md`.

The agent maintains a `write_todos` checklist of remaining stages from turn one, even though the global TODO middleware says to skip the tool for short objectives. This workflow is explicitly the exception.

## How — the key architectural choices

**Two-file split: overview in `SYSTEM_PROMPT`, procedure in `AGENTS.md`.** `SYSTEM_PROMPT` is short and names the 5 stages plus the `write_todos` invariant. `AGENTS.md` is the per-stage procedure manual, loaded into every turn via `memory=["AGENTS.md"]` in `agents.py` and rendered through the `MEMORY` block's `{agent_memory}` placeholder. The system prompt names each downstream stage's subagent / skill and the canonical output path inline so the LLM can plan against the spine without first reading `AGENTS.md`.

**Per-consumer skill grouping under `skills/`.** Each agent or subagent gets its own outer folder of skills (`skills/career-agent/`, `skills/hiring-recon/`, `skills/resume-tailor/`, `skills/interview-coach/`), with the actual skill name as the inner folder. Each consumer's declaration (e.g. `skills=["skills/career-agent/"]` on the main agent, `skills: skills/hiring-recon/` in `subagents.yaml`) points only at its own outer folder, so `SkillsMiddleware` loads exactly the skills that consumer needs — no cross-pollination. Considered a flat `skills/` with frontmatter-based gating; rejected because it would make the per-subagent skill list implicit and hard to audit.

**All-upfront intake (Stage 1), persisted at end of Stage 2.** The agent asks all four intake questions in one message before any parsing. Persistence happens at the end of Stage 2 (not Stage 1) because the intake filename `/processed/<resume-slug>-<jd-slug>-intake.md` depends on slugs minted during Stage 2 parsing. Stage 1 answers are held in conversation context and written once both slugs exist — established in PRD 04 and unchanged here.

**Per resume×JD intake file.** `/processed/<resume-slug>-<jd-slug>-intake.md` matches the per-pair scoping of downstream stages — the same JD can apply to multiple candidates over time (e.g., an AI-leaning vs an infra-leaning resume variant), and Stages 3–5 are all keyed by the `<resume>/<jd>` pair, so intake follows. The path also serves as the natural cache key — re-running the workflow for the same pair finds the existing intake. (Pair-keyed naming was already in place at PRD 04; called out here because it's load-bearing for the Stage 4/5 layout.)

**Stage 4 runs `resume-tailor` and `interview-coach` in parallel.** Both subagents consume the same inputs (resume, JD, intake, research) and produce independent outputs (tailored PDF vs. interview prep doc). The orchestrator spawns them in a single turn via two `task` tool calls in parallel, then synthesizes once both return. Considered serial (tailor first, then coach reads the tailored resume); rejected because the coach's value comes from understanding the *original* candidate against the JD, not the polished version, and serializing would double the latency for no quality gain.

**Tailored resume as YAML, rendered to PDF.** `resume-tailor` writes a `rendercv` YAML at `/tailored_resume/<resume>/<jd>.yaml` as the source of truth, then calls `prepare_render_settings` which kicks off `rendercv render` to emit a `.typ` intermediate (under `/render_intermediate/`, NOT in the UI allowlist) and a final `.pdf` sibling. The YAML is user-editable; the `.typ` and `.pdf` are regenerated on demand. Considered having the LLM write markdown and converting downstream; rejected because `rendercv`'s schema is the contract that produces a professional PDF, and asking the LLM to emit it directly avoids a lossy conversion step.

**Backend storage routes split: StoreBackend for markdown, default LocalShellBackend for binaries.** `CompositeBackend(routes=…)` in `agents.py` routes `/processed/`, `/research/`, `/interview_coach/`, `/memory/`, `/workspace/`, `/large_tool_results/` to `StoreBackend` (Postgres-backed, fast cross-session reads, queryable by namespace). `/tailored_resume/`, `/render_intermediate/`, `/upload/`, and `/interview_battlecard/` fall through to the default `LocalShellBackend(root_dir=CAREER_AGENT_DIR, virtual_mode=True)` so they land on disk — required for `rendercv` (writes real files Typst can read) and for the eventual battlecard PDF.

**Subagents inherit `execute` from the main backend; declarative tool registration in `subagents.yaml`.** Each subagent in `subagents.yaml` declares its `tools:` (opt-in pool: `web_search`, `web_extract`, `prepare_render_settings`) and `skills:` paths. `load_subagents()` wires them up against a shared `_SUBAGENT_TOOLS` registry plus `_SUBAGENT_DEFAULT_TOOLS` (`list_files`, `overwrite_file`) so every subagent gets baseline filesystem access without re-declaring it. The system prompt for each subagent is a one-liner: role + pointer to its skill — the workflow lives in `SKILL.md`, not the prompt.

## Files of interest

| Concern | Path |
|---|---|
| 5-stage workflow + `write_todos` invariant | `backend/app/career_agent/prompts.py` (`SYSTEM_PROMPT`) |
| Per-stage procedure manual (loaded via `memory=["AGENTS.md"]`) | `backend/app/career_agent/AGENTS.md` |
| Agent wiring — `memory`, `skills`, `tools`, `subagents`, `backend` routes | `backend/app/career_agent/agents.py` |
| Subagent definitions (`hiring-recon`, `resume-tailor`, `interview-coach`) | `backend/app/career_agent/subagents.yaml` |
| Stage 3 skill | `backend/app/career_agent/skills/hiring-recon/hiring-recon/SKILL.md` |
| Stage 4 skills | `backend/app/career_agent/skills/resume-tailor/resume-tailor/SKILL.md`, `backend/app/career_agent/skills/interview-coach/interview-coach/SKILL.md` |
| Stage 5 skill (main agent) | `backend/app/career_agent/skills/career-agent/interview-battlecard/SKILL.md` |
| File upload route | `frontend/src/app/api/files/upload/route.ts`, allowlist in `frontend/src/app/config/agentFiles.ts` |
| `rendercv` driver | `prepare_render_settings` tool in `backend/app/career_agent/tools.py` |

## Decisions worth remembering

- **Override the default `write_todos` guidance, in-prompt.** Career-agent turns look "simple" individually (parse this file; spawn that subagent) but are stages of a 5-step arc, so `SYSTEM_PROMPT` explicitly tells the LLM to call `write_todos` from turn one. The middleware's general "skip TODOs for simple objectives" prompt is not edited — the override stays scoped to this agent.
- **Don't ask the user twice.** If prep timeline / extra context arrive in a later turn (because they skipped the upfront ask), the same Stage 2 persistence step runs at that time — slugs already exist, and `AGENTS.md` spells out the late-arrival path. The agent does not loop on the ask if the user declined.
- **Skill folders kebab-case, backend routes snake_case.** `skills/interview-coach/`, `skills/resume-tailor/`, `skills/career-agent/interview-battlecard/` all keep kebab-case (matches the existing skill folder convention and the `name:` slug in each `SKILL.md` frontmatter). Backend routes `/tailored_resume/`, `/interview_coach/`, `/interview_battlecard/`, `/render_intermediate/` use snake_case — the auto-memory rule requires snake_case for multi-word directory names matching `CompositeBackend` routes. Don't unify these.
- **Slug suffix convention.** CV slugs end in `-resume`, JD slugs end in `-jd`. The missing-inputs check in Stage 2 uses this to detect when both artifacts exist in `/processed/` before Stages 3-5 run. Nested paths (`/research/<resume>/<jd>.md`, etc.) drop the `.md` extension on the directory segment but keep the suffix inside it (`/research/tam-nguyen-senior-ai-engineer-resume/aws-ai-solution-engineer-jd.md`).
- **`/render_intermediate/` is hidden from the UI.** It holds `rendercv`-generated `.typ` files that are an implementation detail of the PDF pipeline. The frontend allowlist (`AGENT_FILE_SOURCES.career_agent`) deliberately omits it; users see the YAML source of truth and the rendered PDF, nothing in between.
- **Subagent prompts stay minimal.** Each entry in `subagents.yaml` has a one-liner role + a pointer to its `SKILL.md` and a strict single-line return contract (e.g. `Wrote research report to: <output_path>`). Task details arrive in the user message at spawn time; workflow rules live in the skill, not the system prompt.

## Deferred (intentional non-goals)

- **PDF rendering for the interview battlecard.** Stage 5 still emits `.md` only. `/interview_battlecard/` already routes to `LocalShellBackend` (not `StoreBackend`) so the eventual binary lands on disk; the renderer itself is the missing piece.
- **Cross-session resume / JD reuse.** Re-running the workflow with the same CV + JD pair regenerates everything. A "you already have research for this pair — reuse?" branch is an obvious follow-up but not in scope.
- **LLM-driven retry / regeneration of individual stages.** If `resume-tailor` produces a weak PDF, the user can edit the YAML and re-run `rendercv`, but there's no in-chat "regenerate just Stage 4 with this feedback" affordance. The orchestrator restarts from the earliest stage that needs new inputs.

## How to verify end-to-end

1. `docker compose up -d`.
2. Open the frontend, start a new chat, and send a message that triggers the workflow (e.g. "Help me prep for an interview").
3. **Stage 1 ask.** The agent replies with one short message asking for CV, JD, prep timeline, and extra context — no file operations yet.
4. **`write_todos` shows up.** In the LangGraph trace, confirm `write_todos` is called early with all 5 stages listed.
5. **Stage 2 parse + persist.** Upload a CV from the paperclip, paste a JD URL, give "1 week" as the timeline plus "I shipped a RAG side-project last month not on my CV". Confirm:
   - `/upload/<filename>` contains the raw CV bytes.
   - `parse_document` / `extract_jd` write `/processed/<resume-slug>.md` and `/processed/<jd-slug>.md`.
   - `/processed/<resume-slug>-<jd-slug>-intake.md` is written with the prep timeline.
   - `/processed/<resume-slug>.md` has an appended `## Additional context` block with the RAG note — not the intake file.
6. **Stage 3.** A `task` call spawns `hiring-recon`. Output appears at `/research/<resume>/<jd>.md`. The subagent's reply is a single line: `Wrote research report to: /research/<resume>/<jd>.md`.
7. **Stage 4 (parallel).** Two `task` calls in the same turn spawn `resume-tailor` and `interview-coach`. Outputs:
   - `/tailored_resume/<resume>/<jd>.yaml` (LLM-written YAML)
   - `/render_intermediate/<resume>/<jd>.typ` (Typst intermediate)
   - `/tailored_resume/<resume>/<jd>.pdf` (rendered PDF — open it and confirm it's a real, well-formatted resume)
   - `/interview_coach/<resume>/<jd>.md` (structured prep doc with self-introduction + per-round STAR stories)
8. **Stage 5.** The main agent loads the `interview-battlecard` skill, reads the three prior outputs, and writes `/interview_battlecard/<resume>/<jd>.md`.
9. **Workspace > Files.** All artifacts except `/render_intermediate/` are visible. Confirm the tailored PDF is downloadable.
