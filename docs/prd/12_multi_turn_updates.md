---
type: PRD
title: "Multi-Turn Updates to Career-Agent Artifacts"
description: "After the one-shot pipeline, users can ask for in-place updates to any artifact — routed to the owning subagent in update mode with edit-by-default."
tags: [agent, workflow]
timestamp: '2026-05-26T11:45:46+07:00'
status: "shipped"
scope: "career_agent only"
version: v1
---

**Extends:** [Career-Agent Workflow Orchestration (v2)](08_agent_workflow.md), [Interview Battlecard PDF (v1)](11_interview_battlecard_pdf.md)

# Why

The v2 workflow takes the user from intake to a finished interview kit (research report, tailored resume PDF, interview prep doc, battlecard PDF) in one autonomous pass. After that pass, users want to iterate without restarting: "add a 4th round to the battlecard", "add React to my resume skills", "drop the Twitter link", "expand the research on team org structure", "add common questions to round 2 of my prep doc".

Before this change, the prompts and skills assumed a one-shot pipeline. Every subagent's system_prompt was stamped *"Single-shot — do not call `write_todos`. Your final reply MUST be exactly one line: `Wrote <X> to: <path>`"*; every skill described a from-scratch authoring workflow; `CAREER_AGENT.md` had no follow-up path. Only the battlecard SKILL.md had a single sentence about user-edits-then-re-render. The agent would either refuse update requests or quietly recreate artifacts from scratch, losing user intent and ignoring prior tailoring.

# What the user sees

After all five stages are done, the user can ask in chat for any in-place change to any artifact, and the agent applies it without re-running upstream stages:

- **Battlecard** — "add a Tech deep-dive round, 60 min". The main agent re-reads the JSON, edits it, re-renders the PDF.
- **Research report** — "add a subsection on the team's org structure under Maya Chen". Main agent spawns `hiring-recon` in update mode; the report is edited in place; every other section is untouched.
- **Tailored resume** — "drop the Twitter link" or "add React 19 as the leading skill in the AI/ML category". `resume-tailor` is spawned in update mode; the YAML is edited, then `prepare_render_settings` + `rendercv render` refresh the PDF.
- **Interview prep doc** — "add 3 common behavioral questions with model answers under Round 2". `interview-coach` is spawned in update mode; the doc is edited.

The user-visible signal that an update happened (vs. a fresh create) is the reply verb: `Wrote <artifact> to: <path>` for first-time creation, `Updated <artifact> at: <path>` for in-place edits. The main agent mirrors the verb when summarising back to the user.

Skill-level preservation defaults that previously read as absolute (*"Never drop a skill"*, *"Never drop a URL"*, *"No extra JSON keys"*, *"Single output file"*) now yield to explicit user requests on a per-item basis. Truth/fabrication rules (don't invent metrics, titles, experience, company facts) stay absolute.

No frontend changes — the entire surface is conversational.

# How — the key architectural choices

**Route by file owner, not by user-intent classifier.** Each artifact already has a clear owner from the v2 workflow: the battlecard JSON is the main agent's (no subagent exists for it); the research report, tailored resume, and interview prep doc each have a dedicated subagent that already encapsulates the skill needed to author them. Updates route to the same owner. The main agent does not try to classify "is this trivial enough to do myself" — it owns battlecard edits because there's no subagent to delegate to, and it delegates everything else. Considered a generic "update_file" tool the main agent calls directly for all artifacts; rejected because each subagent already carries the domain skill (resume YAML validation, rendercv re-render, STAR-story preservation, falsifiable-fact discipline) and bypassing them would either duplicate that skill in the main prompt or silently lose it.

**Subagents stay single-shot, but the contract distinguishes create vs update mode through phrasing in the task input.** Each subagent's `system_prompt:` now says *"The caller's task may be to CREATE or UPDATE — read the task description to tell. In update mode, read output_path first and preserve everything except the parts the caller named."* The main agent's job is to phrase the spawn-task with verbs like *"Update the existing X at …"* vs *"Create …"*. Considered adding a structured `mode: create | update` field to the task input; rejected because the deepagents `task` tool takes a free-form description and adding structure means a custom wrapper. Phrasing-as-contract is brittle on paper but in practice trivial — the SKILL.md and the system_prompt both reinforce the verb, and the one-line reply contract (`Wrote …` vs `Updated …`) makes the mode observable in the trace.

**Edit-by-default, overwrite-by-exception.** `FilesystemMiddleware.edit_file` (deepagents) does string-anchor replacement and is already available to every agent and subagent. The new Updates sections in each SKILL.md say *"use `edit_file` for targeted insertions/replacements; use `overwrite_file` only when restructuring most of the file."* This keeps the diff small and reduces the risk of the LLM regenerating unrelated content. The battlecard skill specifically calls out that JSON-schema invariants (round order, types) must survive the edit.

**Always read before editing, even when the artifact was generated this turn.** The battlecard update step starts with `read_file` even though the JSON content was in the LLM's context when it was created. Chat history may have been compacted; the user may have hand-edited via Workspace > Files between turns; `edit_file`'s string-anchor match fails silently if the cached content drifted. The cost of an extra `read_file` is trivial against the cost of a corrupted edit. Subagents always read first because they spawn in fresh context with no prior knowledge of the file.

**User requests override skill preservation defaults; truth rules stay absolute.** Each SKILL.md splits its "Hard rules" / "Unacceptable" block into two sub-blocks: *Absolute* (no fabrication — applies to resume-tailor's "don't claim untrue titles" and "don't change metrics", hiring-recon's "Unknown — no public signal", interview-coach's "every STAR story traces to the candidate's resume", battlecard's "company_facts must be falsifiable") vs *Default* (preservation rules the user can override per-item — "don't drop a skill", "don't drop a URL", "no extra JSON keys", "single output file", length caps, single self-introduction). Considered keeping all rules absolute and asking the user to confirm any override; rejected because it makes the agent feel uncooperative — the user's stated preference about *their own resume* should win without a confirmation dance.

# Files of interest

| Concern | Path |
|---|---|
| Routing summary in main system prompt | `backend/app/career_agent/prompts.py` (`SYSTEM_PROMPT`, the new follow-up paragraph) |
| Stage 6 procedure: per-artifact update flows + task-input templates | `backend/agents/career_agent/CAREER_AGENT.md` (`## Stage 6 — Updates and follow-ups`) |
| Subagent system_prompts: create vs update mode, user-first clause, two-verb reply contract | `backend/app/career_agent/subagents.yaml` |
| Battlecard update flow (main agent owns it) + Hard-rules split | `backend/app/career_agent/skills/career-agent/interview-battlecard/SKILL.md` (`## Updates`, `## Hard rules`) |
| Research update flow + Rules split | `backend/app/career_agent/skills/hiring-recon/hiring-recon/SKILL.md` (`## Updates`, `## Rules`) |
| Resume update flow + Unacceptable split + per-rule "user can override" exceptions | `backend/app/career_agent/skills/resume-tailor/resume-tailor/SKILL.md` (`## Updates`, "Skills preservation rule", "URL preservation rule", "Truth-vs-tailoring guardrail") |
| Interview-prep update flow + Rules split | `backend/app/career_agent/skills/interview-coach/interview-coach/SKILL.md` (`## Updates`, `## Rules`) |
| README pointer to Stage 6 | `backend/app/career_agent/README.md` (`## Multi-turn updates`) |

# Decisions worth remembering

- **Reply verb distinguishes create vs update.** Subagents reply `Wrote <artifact> to: <path>` on creation, `Updated <artifact> at: <path>` on in-place edit — note the verb *and* the preposition (`to:` → `at:`). The main agent's chat history sees only task input + this one line; the verb is how it knows what to tell the user. Considered collapsing back to one wording for simplicity; rejected because the user-facing summary then loses signal about whether existing work was preserved or replaced.
- **The main agent does not run `write_todos` for single-file updates.** The `SYSTEM_PROMPT` already excluded "easy follow-ups about work already produced" from the TODO checklist; Stage 6 reinforces this. A single-artifact update is a one- or two-tool-call task; the TODO middleware's overhead would dominate.
- **No cascading re-renders.** A research update could in principle warrant a battlecard refresh (new facts, new salary signal), and a resume tailoring change could warrant new STAR stories in the prep doc. Stage 6 explicitly says: do not auto-cascade — ask the user once if a downstream artifact looks stale, and let them confirm. The user owns the battlecard as the day-of artifact; silent regeneration of their hand-edits would be hostile.
- **No new tools, no wiring changes.** `edit_file` was already exposed via deepagents' `FilesystemMiddleware` and documented in `prompts.FILESYSTEM`. `agents.py`, `tools.py`, and the backend route table are untouched. The entire feature is prompt + skill work.
- **Per-rule exceptions over a blanket "user can override anything" clause.** Each preservation rule in the resume-tailor skill (skills, URLs, the bigger "Unacceptable" list) carries its own *Exception: if the user explicitly asks to drop one, drop only what they named* sentence, and the unacceptable list itself is split into "Absolute" (truth) and "By default" (preservation) sub-blocks. Considered one global "user requests override" line at the top; rejected because the granularity matters — *dropping a Twitter link* is fine on request, *claiming an untrue title* is never fine even if the user asks.
- **Updates section appended to each SKILL.md, not woven into the existing workflow.** Each `## Updates` subsection sits between the create-mode procedure and the final `## Rules` block. Two reasons: skim-friendly for a future reader (the create flow is the dominant path on first read), and additive so the existing create flow stays exactly as v2 documented it — no regression risk on the first-run path.
- **README has one paragraph, not a duplicate procedure.** The README's new `## Multi-turn updates` block is six lines and points at `CAREER_AGENT.md` Stage 6 for detail. Duplicating Stage 6's task-input templates in the README would create a second source of truth that drifts.

# Deferred (intentional non-goals for v1)

- **Cascading update prompts.** Stage 6 says "ask the user once whether to refresh the battlecard" after an upstream update, but the agent has no structured detector for *when* a refresh is warranted. Today it relies on the LLM's judgment; if this turns out to miss obvious cascades (renamed top skill, new salary signal), add explicit triggers in Stage 6.
- **Diff preview before applying.** The agent edits the file directly. A "here's what I'm about to change — confirm?" preview was considered for high-blast-radius edits (full-file overwrites, schema-shape changes) but rejected for v1 — the user can always revert via Workspace > Files or ask the agent to undo in the next turn.
- **Versioned snapshots / undo history.** No history beyond what the user manually keeps. If we start seeing accidental overwrites, a per-edit snapshot under `/workspace/snapshots/` is the natural follow-up.
- **Update mode for `parse_document` / `extract_jd`.** Re-running with the same `output_path` / `save_as` already overwrites cleanly (Stage 2 procedure), so no new code path is needed. Documented as-is in Stage 6's "Processed resume/JD/intake updates" line.
- **Structured `mode:` field in the task spawn.** Phrasing-as-contract is enough today; revisit if subagents start misclassifying create vs update.
- **A battlecard subagent.** The battlecard stays main-agent-owned for both create (Stage 5) and update (Stage 6). The skill is short, the JSON is small, and spawning a subagent for what's effectively a single `edit_file` + `render_battlecard_pdf` would add latency without isolating useful context.

# How to verify end-to-end

1. `docker compose up -d`. Open a thread that already has a full Stages 1→5 run for one resume × JD pair (all artifacts visible in Workspace > Files: processed CV/JD, intake, research, tailored resume YAML+PDF, interview prep doc, battlecard JSON+PDF).
2. **Battlecard update (main agent owns it).** Send: *"Add a 4th round — Tech deep-dive, 60 min."* In the LangGraph trace, confirm the main agent: (a) calls `read_file("/interview_battlecard/<r>/<j>.json", limit=1000)`, (b) calls `edit_file` (or `overwrite_file` if the round insertion forces a restructure) on the same path, (c) calls `render_battlecard_pdf("/interview_battlecard/<r>/<j>.json")`. The PDF in Workspace > Files refreshes; rounds 1–3 are unchanged.
3. **Research update (`hiring-recon`).** Send: *"Add a subsection on the team's org structure under Maya Chen in the research report."* Confirm a single `task` call to `hiring-recon` with task input containing *"Update the existing research report at /research/<r>/<j>.md"* + the named change. Subagent's one-line reply is `Updated research report at: /research/<r>/<j>.md`. The `## Hiring team` section grows; `## Company snapshot`, `## Match analysis`, etc. are byte-identical to before.
4. **Resume update with user-override (`resume-tailor`).** Send: *"Drop the Twitter / X link from my resume."* Confirm `task` spawns `resume-tailor` in update mode. Subagent's trace shows it `read_file`s the YAML, removes only the Twitter `social_networks` (or `custom_connections`) entry, re-runs `prepare_render_settings`, then `execute("rendercv render …")`. Reply: `Updated tailored resume PDF at: /tailored_resume/<r>/<j>.pdf`. The PDF re-renders without the link; every other skill, bullet, and URL survives.
5. **Interview-prep update (`interview-coach`).** Send: *"Add 3 common behavioral questions with short model answers under Round 2 of my prep doc."* Confirm `task` to `interview-coach` in update mode; reply is `Updated interview prep doc at: /interview_coach/<r>/<j>.md`. Round 2 grows; the self-introduction at the top and other rounds are unchanged.
6. **No regression on first-run flow.** In a fresh thread with a fresh CV+JD pair, run all 5 stages end-to-end as in [PRD 08](08_agent_workflow.md)'s verification — every stage emits exactly its original `Wrote …` reply, every output lands at its original path, no Stage-6 paths are touched.
