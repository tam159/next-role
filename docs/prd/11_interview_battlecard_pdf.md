---
type: PRD
title: "Interview Battlecard — JSON + PDF via weasyprint"
description: "Stage 5 emits a hand-editable battlecard JSON plus an A4-landscape PDF rendered in-process with jinja2 + weasyprint."
tags: [agent, pdf]
timestamp: '2026-05-24T22:42:23+07:00'
status: "shipped"
scope: "career_agent / main agent, Stage 5"
version: v1
---

**Extends:** [agent_workflow](08_agent_workflow.md), mirrors [tailored_resume_pdf](07_tailored_resume_pdf.md)

# Why

Stage 5 of the agent workflow previously emitted a markdown battlecard at `/interview_battlecard/<resume>/<jd>.md`. Plain markdown is the wrong artifact to walk into an interview with — candidates don't open it the night before because it doesn't *feel* like prep. We want a visually attractive, downloadable PDF that makes the candidate actually review it, plus a JSON sibling they can hand-edit in Workspace > Files and re-render.

This mirrors the tailored-resume pattern (YAML source of truth + rendercv-generated PDF) but for battlecards (JSON source of truth + weasyprint-generated PDF). `weasyprint>=68.1` was already added to `backend/pyproject.toml` ahead of this work.

# What the user sees

For each `<resume>` × `<jd>` pair, Workspace > Files now shows two artifacts under `/interview_battlecard/<resume>/`:

- `<jd>.json` — the source of truth. LLM-written, hand-editable. Strict shape: `document_title` + `rounds[]` with five sections per round (`introduction`, `stories_ready`, `company_facts`, `questions`, `watch_outs`).
- `<jd>.pdf` — A4 landscape, one page per round, five colored section cards (intro yellow, stories lavender, facts peach, questions pink, watch-outs blue), FiraSans throughout.

The `.md` from v0 is gone — replaced, not augmented. Existing `.md` files in the repo are stale outputs; the agent regenerates per run.

After rendering, the agent emits one short handoff line: `Battlecard saved — N rounds, JSON + PDF under /interview_battlecard/<resume>/<jd>.{json,pdf}`. To re-render after the user edits the JSON, the user asks the agent to re-run `render_battlecard_pdf` on the same path — idempotent.

# How — the key architectural choices

**One-step in-process render, not the rendercv-style two-step CLI handshake.** Tailored resumes use `prepare_render_settings` + `execute("rendercv render …")` because rendercv is a CLI binary. weasyprint is a pure-Python library, so the new tool reads the JSON via the backend, runs jinja2 + weasyprint inline, and writes the PDF — one tool call, no shell, no settings-block injection. The LLM doesn't reason about render config at all.

**Template assets bundled in-tree as a self-contained unit.** `backend/app/career_agent/templates/battlecard/` holds the `.html.j2`, `.css`, and five FiraSans `.ttf` files (~2.2 MB). The CSS references fonts via relative `url(FiraSans-*.ttf)`; weasyprint resolves them through `base_url=<template_dir>`. Co-locating means a single `base_url` works and the template directory is portable. System fonts were rejected for reproducibility — fonts must match across dev host, container, and future deploys.

**Lazy weasyprint import, after path validation.** weasyprint's `cffi` pulls Pango/GLib at import time. The user's macOS host doesn't have those native libs (the Docker container does). Moving `from weasyprint import HTML` *inside* the tool function — and *below* the path-prefix / extension validation — keeps `tools.py` import-safe everywhere and lets the validation-only unit tests run on the host without system deps.

**Backend-relative return string.** The tool returns `Rendered PDF to /interview_battlecard/<r>/<j>.pdf`, not the on-disk path (which would be `/deps/next-role/backend/app/career_agent/interview_battlecard/…` inside the container). The container mount prefix is noise to the LLM and confuses follow-up tool calls that expect backend-absolute paths.

# Files of interest

| Concern | Path |
|---|---|
| New tool factory | `backend/app/career_agent/tools.py` (`make_render_battlecard_pdf`) |
| Tool wired into main agent only (not subagents) | `backend/app/career_agent/agents.py` |
| Template, CSS, five FiraSans fonts | `backend/app/career_agent/templates/battlecard/` |
| Skill rewrite: JSON shape contract + two-step flow | `backend/app/career_agent/skills/career-agent/interview-battlecard/SKILL.md` |
| Flow step 5 + File Structure block updated | `backend/app/career_agent/README.md` |
| Unit tests (validation + render; render-only tests skip on no-deps hosts) | `backend/tests/career_agent/test_tools_battlecard_pdf.py` |

# Decisions worth remembering

- **`/interview_battlecard/` was already routed to `LocalShellBackend`.** No new route in `CompositeBackend`. The README v0 claim that it "uses FilesystemBackend so the eventual binary output lands on disk" was implicit fall-through to the default — kept as-is; just deleted the "future phase" note.
- **JSON schema lives in SKILL.md, not pydantic.** Jinja2 silently tolerates missing optional fields; pydantic validation would catch LLM drift but adds a wrap around what is currently a deterministic LLM-emit problem. Revisit if rendering errors start cropping up; for v1 the SKILL.md spec is the contract.
- **Drop the v0 markdown output entirely.** Earlier consideration: keep `.md` alongside `.json` + `.pdf` for grep-ability. Rejected — three artifacts is noise, and the JSON+PDF pair already covers "edit" + "review" use cases. Existing `.md` files are left in place (regenerated per run, not migrated).
- **Tool returns backend-relative path, not on-disk.** Caught during review — `Path` object's `__str__` was the on-disk absolute. Switched to `json_path.removesuffix(".json") + ".pdf"` so the LLM gets a usable path back.
- **Render-only tests gated by `pytest.mark.skipif`.** `_weasyprint_importable()` tries `import weasyprint` and catches `OSError`. Four validation tests always run; two render tests skip on Mac unless Pango is installed, but pass cleanly inside the backend container in CI.
- **Not exposed to subagents.** Stage 5 is main-agent-only per CAREER_AGENT.md. Deliberately omitted from `_SUBAGENT_TOOLS` so no future declarative subagent silently picks it up.
- **Empty-content check removed from the tool.** `FilesystemBackend.read` returns line-numbered content (e.g. `["1\t   ", "2\t  "]`), so `content.strip()` on a whitespace-only file is non-empty. The JSON decode error message is just as informative for that case; the redundant check is gone.

# Deferred (intentional non-goals for v1)

- **Pydantic schema for the JSON.** Add once LLM drift becomes observable; not needed today.
- **UI "regenerate from edited JSON" button.** User asks the agent to re-render after editing in Workspace > Files. Trigger for revisit: if users start editing JSON frequently enough that the round-trip through chat feels heavy.
- **Multiple templates / theme picker.** Single template only for v1. The `.html.j2` + `.css` are designed to be swappable as a unit if/when more themes are wanted.
- **PDF rendering for the resume battlecard JSON via the same template.** Out of scope — resumes already have rendercv.
- **LangSmith evals against the rendered PDF.** Tracked under the deferred `@pytest.mark.eval` marker; rendering quality is binary today (does it open, does it have the right rounds).
- **Migration of existing `.md` battlecards.** They're outputs; agent regenerates on next Stage-5 run for that resume × JD.

# How to verify end-to-end

1. `docker compose up -d` (the user's standard local-dev stack — weasyprint's Pango/GLib system libs are baked into `next-role-backend`).
2. From `backend/`: `docker exec next-role-backend-1 bash -lc 'cd /deps/next-role/backend && uv run pytest tests/career_agent/test_tools_battlecard_pdf.py -v'` — 6/6 pass in the container. On the macOS host, `uv run pytest tests/career_agent/test_tools_battlecard_pdf.py` shows 4 passed, 2 skipped.
3. In the frontend, kick off Stage 5 for an existing resume × JD pair that already has a tailored resume + interview-coach prep doc.
4. Watch the agent call `overwrite_file("/interview_battlecard/<r>/<j>.json", …)` then `render_battlecard_pdf("/interview_battlecard/<r>/<j>.json")`. The tool replies `Rendered PDF to /interview_battlecard/<r>/<j>.pdf` — backend-relative.
5. In Workspace > Files, both `<jd>.json` and `<jd>.pdf` appear under `/interview_battlecard/<resume>/`. Click the PDF — A4 landscape, one page per round, five colored section cards, FiraSans.
6. Edit the JSON in Workspace > Files (e.g. drop a watch-out), ask the agent to re-render → PDF refreshes in place; JSON is unchanged otherwise.
