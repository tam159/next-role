---
type: PRD
title: "Virtual-Path Translation in the execute Tool"
description: "VirtualPathShellBackend rewrites virtual /-prefixed paths in execute commands so the agent uses one path convention end-to-end."
tags: [backend, agent, files]
timestamp: '2026-05-26T15:45:20+07:00'
status: "shipped"
scope: "career_agent backend"
version: v1
---

**Extends:** [Tailored Resume — YAML + PDF via rendercv](07_tailored_resume_pdf.md)

# Why

`LocalShellBackend(root_dir=CAREER_AGENT_DIR, virtual_mode=True)` makes the filesystem tools (`read_file`, `write_file`, `edit_file`, `overwrite_file`, `list_files`) speak in **virtual paths** — `/tailored_resume/foo.yaml` resolves to `<CAREER_AGENT_DIR>/tailored_resume/foo.yaml`. The `execute` tool injected by deepagents' `FilesystemMiddleware` does no such translation: the command string is handed verbatim to `subprocess.run(..., cwd=root_dir, shell=True)`. So `rendercv render /tailored_resume/foo.yaml` is read as a real absolute path on container disk and fails with `FileNotFoundError`.

[PRD 07](07_tailored_resume_pdf.md) papered over this with two crutches: `prepare_render_settings` returning a real on-disk path so the LLM could paste it verbatim into `execute`, plus a long SKILL.md lecture instructing the LLM to switch path conventions. It worked, but the dual convention (virtual paths everywhere except inside `execute`) periodically confused the LLM into pasting the wrong path. The goal here is a single path convention end-to-end and the SKILL.md lecture gone.

# What the user sees

Nothing user-visible. Same Workspace outputs (`<jd>.yaml`, `<jd>.pdf`, `<jd>.typ`), same chat-level replies. The only observable change is that the agent's `execute("rendercv render …")` calls now carry virtual paths (`/tailored_resume/<r>/<j>.yaml`) instead of real container paths (`/deps/next-role/backend/app/career_agent/tailored_resume/<r>/<j>.yaml`); the rendered PDF lands at the same place either way.

# How — the key architectural choice

**Subclass `LocalShellBackend`, rewrite `/`-prefixed tokens in the command before delegating to the parent.** A new `VirtualPathShellBackend` overrides `execute()`: `shlex.split` the command, and for each token starting with `/`, resolve `<root_dir>/<token[1:]>` — if that path is under `root_dir` *and* either it or its parent already exists, rewrite the token to its on-disk absolute form. Otherwise leave it alone. Then `shlex.join` and call `super().execute(...)`. The middleware's auto-injected `execute` tool stays untouched because it calls `executable.execute(...)` polymorphically; swapping the backend class is the entire wiring change.

Why this shape rather than the obvious alternatives:

- **Subclass, not monkey-patch.** `agents.py` already monkey-patches deepagents prompt constants at module-load (`_apply_prompt_overrides()`). Patching `FilesystemMiddleware._create_execute_tool` similarly would work, but the side effect is process-global — any other deep agent in the same process is affected. A backend subclass scopes the change to the one `_backend = CompositeBackend(default=VirtualPathShellBackend(...))` line.
- **Subclass, not a custom `execute` in `tools=[…]`.** The middleware injects an `execute` tool whose name we'd have to shadow via `ToolNode`'s last-wins dict; the framework offers no flag to disable the auto-injection. Working though that ordering is implicit and the framework gives no guarantee. The backend-level swap is structural.
- **Existence-checked rewrite, not strip-leading-slash.** `cwd=root_dir` is already set; just dropping the leading slash on every `/`-prefixed token would resolve `/tailored_resume/foo.yaml` correctly but break legitimate `/tmp/x`, `/etc/passwd`, `/usr/bin/python` calls (they'd resolve under `root_dir`). The existence check naturally separates the two: a virtual path's target (or its parent dir) lives under `root_dir`; a real system path does not.
- **Not a prefix whitelist.** `{ /tailored_resume/, /processed/, /research/, … }` would be a second source of truth that must stay in sync with the actual layout. Existence is the layout — no separate registry.

# Files of interest

| Concern | Path |
|---|---|
| `VirtualPathShellBackend` subclass (translation + execute override) | `backend/app/career_agent/shell_backend.py` |
| Backend wiring swap | `backend/app/career_agent/agents.py` (`_backend = CompositeBackend(default=VirtualPathShellBackend(...))`) |
| `prepare_render_settings` returns verbatim execute command with virtual path | `backend/app/career_agent/tools.py` (`make_prepare_render_settings`, return line) |
| SKILL.md drops the "use on-disk path NOT backend path" lecture (steps 4 + Updates step 5) | `backend/app/career_agent/skills/resume-tailor/resume-tailor/SKILL.md` |
| Unit tests: translate / passthrough / `..`-escape / quoted args / end-to-end execute | `backend/tests/career_agent/test_shell_backend.py` |
| Existing prepare_render_settings tests retargeted to the new return string | `backend/tests/career_agent/test_tools.py` |

# Decisions worth remembering

- **Subclass lives in its own module, not inline in `agents.py`.** `agents.py` calls `create_deep_agent(...)` eagerly at module load, which needs OpenAI credentials. Tests that exercise the translator import from `shell_backend.py` directly and never trip that eager construction. Same reason `tools.py` exists separately.
- **The `settings:` block inside the YAML keeps real on-disk paths.** `prepare_render_settings` injects `output_folder: <abs>` and `typst_path: <abs>` into the YAML; these are read by rendercv as final write destinations, not as shell arguments — they never pass through the translator. Translating them would break rendercv (it'd treat `/tailored_resume/...` as a literal directory to create).
- **`shlex.split` / `shlex.join`, not regex.** A regex on `/[\w/.-]+` would mangle quoted arguments containing slashes (`--config="/a/b c"` → broken quoting). `shlex` round-trips quoting safely; unbalanced quotes fall through unchanged so the shell surfaces the real error.
- **Path-traversal (`/../../etc/passwd`) is left untouched, not rewritten.** A `..`-escape resolves outside `root_dir`; `Path.relative_to` raises `ValueError` and the token is returned as-is. The user gets the same behaviour they'd get from `subprocess.run` directly — no new attack surface, no surprising rewrite.
- **`prepare_render_settings`'s return is now the verbatim execute command.** `Prepared for rendering. Run: execute("rendercv render <yaml_path>")` — the LLM copies the literal string, no synthesis. Cheaper than asking the LLM to compose the command from a path. Matches the user's standing "don't make the LLM regenerate large tool args" rule (which generalises to "don't make the LLM compose strings a tool already knows").
- **No prompt change for `EXECUTION` (`prompts.py:172–179`).** That prompt doesn't mention path translation either before or after; the SKILL.md was the only authored place that taught the dual convention.

# Deferred (intentional non-goals for v1)

- **Translation inside flag values (`--output=/tailored_resume/foo.pdf`).** `shlex` keeps that as one token; the leading `/` test misses it. Not encountered in the rendercv command shape we ship. If a future command needs it, extend `_rewrite_token` to detect `key=/path` pairs or instruct callers to use space-separated form (`--output /tailored_resume/foo.pdf`).
- **Translation for `StoreBackend`-routed prefixes (`/memory/`, `/processed/`, etc.).** Those routes go to a `StoreBackend`, which has no shell. The translator only sees commands that hit the default `LocalShellBackend` (which is where `rendercv`, `weasyprint`, etc. live). If a future tool needs to shell against a Store-backed prefix, that prefix would need its own materialisation step first — not a translator problem.
- **Sandbox / HITL on `execute`.** [PRD 07](07_tailored_resume_pdf.md) already inherited `LocalShellBackend`'s unrestricted shell; this PRD doesn't change that posture. The deepagents docs flag HITL as the recommended safeguard; left for whenever we expose the agent beyond solo development.

# How to verify end-to-end

1. `cd backend && uv run pytest tests/career_agent/test_shell_backend.py` — 8 cases (existing path rewrite, parent-only rewrite, real-path passthrough, `..`-escape rejection, quoted args, flags, unbalanced quotes, end-to-end `execute` via virtual path). All pass.
2. `cd backend && uv run pytest tests/career_agent/` — full career_agent suite (66 pass + 2 skipped). Confirms the two `prepare_render_settings` tests now assert the virtual-path return contract.
3. `docker compose up -d`, open the frontend, trigger a tailored-resume flow. In the LangGraph trace, the `resume-tailor` subagent now emits `execute("rendercv render /tailored_resume/<r>/<j>.yaml")` (virtual path); the subprocess succeeds; the PDF lands at `<CAREER_AGENT_DIR>/tailored_resume/<r>/<j>.pdf` exactly as before.
4. Re-render the same YAML a second time — `prepare_render_settings` is still idempotent; `execute` with the virtual path still resolves correctly. Sanity-check that a hand-edit followed by Stage-6 update mode produces the same path-shape in the trace.
