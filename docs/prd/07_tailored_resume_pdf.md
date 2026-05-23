# PRD: Tailored Resume — YAML + PDF via rendercv

**Status:** shipped · **Scope:** career_agent / `resume-tailor` subagent

## Why

Stage 4.1 of the agent workflow previously had `resume-tailor` write a plain markdown file (`/tailored_resume/<resume>/<jd>.md`). Users want the actual deliverable they hand to a recruiter — a typeset PDF — not raw markdown they have to convert themselves. `rendercv[full]>=2.8` is already a backend dep, so we wire it in rather than reinvent layout.

We also want the YAML to be a **first-class, editable source of truth**: if the user tweaks a bullet, the agent can re-render the PDF without re-tailoring.

## What the user sees

For each `<resume>` × `<jd>` pair, the Workspace > Files view now shows two artifacts under `/tailored_resume/<resume>/`:

- `<jd>.yaml` — the rendercv source. Hand-editable; the canonical record of what was tailored. Top of the file is a `# changes:` comment block summarising the reorder / keyword / bullet edits the agent made.
- `<jd>.pdf` — the rendered PDF (theme selected per resume — `engineeringclassic` if engineering signals detected, else `classic`).

The `<jd>.typ` intermediate file is written to `/render_intermediate/<resume>/<jd>.typ` on real disk, **not** in the UI allowlist — invisible to users, available for debugging.

After the agent finishes, the subagent replies one line: `Wrote tailored resume PDF to: <pdf_path>`. To regenerate after the user edits the YAML, the main agent (which also has `prepare_render_settings`) re-runs `prepare_render_settings(yaml_path)` then `execute("rendercv render <abs_path>")` — no re-tailoring.

## How — the key architectural choices

**Three-step subagent flow.** (1) LLM writes the YAML (`cv:`, `design:`, `locale:` only — no `settings:`). (2) LLM calls `prepare_render_settings(yaml_path)` — a new custom tool that deterministically appends a canonical `settings:` block (pinning output paths, skipping md/html/png) and returns the on-disk absolute path to use. (3) LLM calls `execute("rendercv render <abs_path>")` from `LocalShellBackend`'s inherited shell tool. Split chosen because (a) the LLM shouldn't reason about render settings, (b) we don't want it constructing long CLI override argv, and (c) the YAML must stay user-editable so a wrapper-around-rendercv tool would hide the settings.

**Tool wiring refactor.** Subagents were silently inheriting the main agent's full toolset (e.g. `interview-coach` getting `parse_document` and `extract_jd`). Fixed by moving the tool pool into `agents.py`, threading it into `load_subagents(tools=, default_tools=)`, and **always** writing an explicit `tools=[…]` onto every subagent spec (empty list when none declared). `default_tools` carries `list_files` + `overwrite_file` to every subagent so they don't have to re-declare basics.

**Heavy SKILL.md prompt-engineering.** rendercv's pydantic models are strict; the LLM accumulated a long tail of YAML / type / enum errors across runs. Each one is now pinned in two places: a prescriptive rule (template comment + pitfalls section) and a recovery hint in the step-4 common-errors list. Covered: unquoted colons (mid + trailing), numeric strings (`label: 2022`), E.164 phones, `cv:` field allowlist (`headline` not `label`), 17-value `social_networks.network` enum + `custom_connections` fallback, `custom_connections.url` HttpUrl rule, nested-list bullets, NormalEntry-per-skill-category structure with a worked side-by-side RIGHT/WRONG YAML example, and "don't drop skills / URLs from the source".

**Filesystem-tool guidance moved up.** Added to `prompts.py` Block 5b: *"Do NOT use `execute` to create or edit files"* — applies to every agent that has shell access, not just resume-tailor.

## Files of interest

| Concern | Path |
|---|---|
| New `prepare_render_settings` tool factory | `backend/app/career_agent/tools.py` |
| Tool pool + `default_tools` + main-agent wiring | `backend/app/career_agent/agents.py` |
| `load_subagents(tools=, default_tools=)` | `backend/app/career_agent/utils.py` |
| `resume-tailor` subagent declaration | `backend/app/career_agent/subagents.yaml` |
| Full rewrite (rendercv workflow + pitfalls + common errors) | `backend/app/career_agent/skills/resume-tailor/resume-tailor/SKILL.md` |
| `EXECUTION` prompt: "don't write files via execute" | `backend/app/career_agent/prompts.py` |
| Stage-4 description + new yaml/pdf paths | `backend/app/career_agent/AGENTS.md` |
| File-structure block + flow description | `backend/app/career_agent/README.md` |
| `/render_intermediate/` gitignore | `backend/.gitignore` |
| Unit tests (settings injection, idempotency, path validation, mkdir) | `backend/tests/career_agent/test_tools.py` |
| Updated `load_subagents` tests (`tools=`, `default_tools=`) | `backend/tests/career_agent/test_utils.py` |

## Decisions worth remembering

- **YAML is the source of truth, PDF is the deliverable, .typ is hidden.** The split avoids cluttering the UI with an intermediate file users don't review, while preserving it on disk for debugging.
- **Flat sibling layout** (`<jd>.yaml`, `<jd>.pdf`) under `/tailored_resume/<resume>/`, matching the prior `<jd>.md` convention. Nested per-JD folders rejected as unnecessary.
- **Settings injected by tool, not LLM.** The LLM emits only `cv:` / `design:` / `locale:`; `prepare_render_settings` owns `settings:` exclusively. Idempotent so re-renders work.
- **All built-in rendercv themes allowed** (`classic`, `classic_legacy`, `sb2nov`, `moderncv`, `engineeringclassic`, `engineeringresumes`); auto-detection picks engineering vs. classic but the user can override.
- **Changes log lives as a top-of-YAML `# changes:` comment**, not a separate file. Survives re-renders, doesn't render in the PDF.
- **Subagents inherit `execute` from `LocalShellBackend`** automatically — but **do NOT** inherit custom tools. Mixed inheritance is enforced by the always-explicit `tools=[…]` in `load_subagents`.
- **PII discipline:** SKILL.md uses fictional placeholders (`+15551234567`, `Jane Doe`, `your-username`); real user content lives only under gitignored output dirs.
