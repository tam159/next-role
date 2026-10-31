---
type: PRD
title: "Shell Execution Off by Default (SANDBOX_PROVIDER=none)"
description: "A new default SANDBOX_PROVIDER=none removes the execute tool from both agents, rendering resumes as a fixed host subprocess and drawing box/violin/contour/histogram2d charts natively, so the cloud deploy needs no sandbox while local/e2b stay as opt-ins."
tags: [agent, backend, infra, pdf]
timestamp: '2026-10-31T09:18:00+07:00'
status: "shipped"
scope: "career + analytics agents' default backend, render pipeline, chart builder"
version: v1
---

**Extends:** [Sandboxed Shell Execution via the E2B Protocol](30_sandboxed_execute_e2b.md), [Human-in-the-Loop Approval for the execute Tool](29_execute_tool_hitl_approval.md), [Analytics Agent — natural-language questions over the warehouse](32_analytics_agent.md)

# Why

The first cloud deploy should stay simple, and a sandbox is an extra moving part: [PRD 30](30_sandboxed_execute_e2b.md)'s CubeSandbox needs a dedicated Linux host, and its alternative — `local` mode — runs model-authored bash on the backend host, which can read the bind-mounted repo including `.env`. A review of both agents showed neither actually needs `execute`. The career agent's prompts, skills, memory and `subagents.yaml` never ask for a shell command (the only mentions forbid it); its one real shell use was `render_resume_pdf` calling `backend.execute("rendercv render …")` from inside the tool. The analytics agent's three tools cover every routine path; `execute` served only its Python fallback, which was the only way to draw box, violin, contour and histogram2d charts.

So the shell became an opt-in: off for the cloud and for local dev, one env var away for when a future version wants it back.

# What the user sees

Nothing changes in a normal conversation — resumes still render to PDF, and charts still appear as cards — but no shell-approval card can ever appear, because no agent has a shell. The analytics agent now draws `box`, `violin`, `contour` and `histogram2d` straight from SQL with no approval. Visuals outside `create_chart`'s kinds (dual y-axes, annotations, subplots, trend overlays, error bars) are unavailable until an operator opts in.

Operators see `SANDBOX_PROVIDER=none` in `.env.example`; `local` restores the host shell, `e2b` the remote sandbox, both exactly as before (a container recreate, not a restart, picks it up).

# How — the key architectural choices

**Turn the shell off at the composite default, not tool by tool.** deepagents shows `execute` whenever the `CompositeBackend` default is a `SandboxBackendProtocol`. Under `none`, `make_default_backend()` returns a plain `FilesystemBackend(root_dir, virtual_mode=True)` — the parent class of `LocalShellBackend`, whose shell subclass only overrides `execute` — so file, skill and memory reads behave identically while the framework hides `execute` and its "Shell paths vs. virtual paths" prompt section for the main agent, every declarative subagent and the general-purpose one. Hiding the tool with `FilesystemMiddleware(tools=[…])` was rejected: it leaves a host shell reachable through `backend.execute()`, which is the exposure we wanted gone.

**One predicate, `execute_enabled()`, gates every shell-only surface.** `execute_interrupt_on()` returns None (nothing to approve), and the analytics agent derives `_PYTHON_FALLBACK` from it. With the fallback off, `run_sql` drops `save_as`, `create_chart` drops `figure_path` (and makes `sql` required), the file-tools prompt drops the scratch hand-off sentence, and the fallback recipe — moved into its own `analytics-python-fallback` skill — is not loaded. The parameters are removed from the schema, not just refused: on a `FilesystemBackend`, `open_scratch` would fall through to `SandboxScratch`, which calls `execute`, and a visible parameter invites the model to try.

**The tools that need a process run it themselves.** `_run_rendercv` keeps the `backend.execute()` path whenever the default is a shell (so `local`/`e2b` render exactly as before), and otherwise runs `[sys.executable, "-m", "rendercv", "render", <file>]` as a host subprocess with `default_shell_env()` (PATH only, no secrets) and a 60-second timeout. It is developer-authored and has fixed arguments, so it was never gated, the same as before.

# Files of interest

| Concern | Path |
|---|---|
| `none` provider, `execute_enabled()`, `make_default_backend()` | `backend/agents/career_agent/sandbox_backend.py` (lines ~344–385) |
| Approval gate off without a shell | `backend/agents/career_agent/execute_approval.py` (`execute_interrupt_on`) |
| Shell vs host-subprocess render | `backend/agents/career_agent/tools.py` (`_run_rendercv`, `_run_rendercv_on_host`, lines ~400–460) |
| Native distribution + grid kinds | `backend/agents/analytics_agent/charts.py` (`_distributions`, `_grid`, `FIGURE_KINDS`) |
| Fallback-aware tool factories | `backend/agents/analytics_agent/tools.py` (`make_run_sql`, `_create_chart_doc`, `make_create_chart`) |
| Flag wiring, conditional skill source | `backend/agents/analytics_agent/agents.py` (`_PYTHON_FALLBACK`, `_SKILLS`) |
| Fallback recipe, loaded only with a shell | `backend/agents/analytics_agent/skills/analytics-python-fallback/python-fallback/SKILL.md` |
| Test opt-in to the shell (env + module reload) | `backend/tests/conftest.py` (`with_shell`) |
| Host render tests | `backend/tests/career_agent/test_tools_render_resume_host.py` |

# Decisions worth remembering

- **Extend `SANDBOX_PROVIDER` with `none` instead of adding a boolean.** One setting answers both "is there a shell" and "where does it run"; a separate `AGENT_EXECUTE_ENABLED` would have left `SANDBOX_PROVIDER` meaningless while it was off. It applies to both agents — no per-agent toggle exists or was needed.
- **Draw the four chart kinds natively instead of dropping them.** Turning off the fallback would otherwise have taken away box/violin/contour/histogram2d. Box and violin plot raw observations (optional `x` category, optional `series` split, grouped modes), histogram2d bins two columns, and contour reuses the heatmap pivot while keeping raw axis values so numeric axes stay continuous. The frontend needed no change: it loads `plotly.js-cartesian-dist-min`, which bundles those traces, and the envelope `kind` is a free string. `PUBLISH_ONLY_KINDS` is gone.
- **Local dev matches the cloud.** `.env.example` ships `none`, so the stack that is tested locally is the stack that is deployed; `local` remains a one-line opt-in for working on the fallback.
- **Keep the opt-in code dormant rather than deleting it.** `E2BSandboxBackend`, the `e2b` and `pandas` dependencies, the approval policy, `scratch.py` and the frontend `ToolApprovalInterrupt` are all unchanged, so turning the shell back on is a config change, not a revert.
- **Tests opt in by reloading.** Agents build their backend and tools at import, so the `with_shell` fixture sets `SANDBOX_PROVIDER=local`, reloads the agent module, and reloads it back on teardown (with a dummy `OPENAI_API_KEY` for the rebuild). The HITL suite uses it autouse; the prompt snapshots pin `execute` absent by default and present under the opt-in.

# Deferred (intentional non-goals for v1)

- **Readiness of the e2b opt-in itself.** Untested, inferred from the code: under `e2b`, memory and skill files load through the default backend, meaning inside the sandbox, and the template image installs only `rendercv` (no pandas or plotly). Fix both before turning `e2b` on.
- **Native dual axes, annotations and subplots.** These need new `ChartSpec` options; without demand they stay behind the shell opt-in.
- **Pruning the opt-in dependencies.** `e2b` and `pandas` could become optional extras, but that would complicate the image while the opt-in path is still meant to stay available.

# How to verify end-to-end

1. `cd backend && uv run pytest` — 614 passed; pre-commit on the touched files is clean.
2. With `SANDBOX_PROVIDER` unset or `none`, the hot-reloaded backend resolves `FilesystemBackend` defaults for both agents and `execute_interrupt_on()` is None (checked with `docker exec` into the backend).
3. Career agent in the UI as a throwaway demo user: upload a demo CV + JD, ask for a tailored resume only. The PDF, `.typ` and YAML appear under `/tailored_resume/` with no approval card. Thread history (main plus the `tools:` namespace) shows `parse_document`, `task`, `read_file`, `write_file` and `render_resume_pdf`, and no `execute`.
4. Analytics agent in-process (`env -u LANGGRAPH_AUTH` inside the backend container, since a demo user fails the allowlist): "run duration by status as a box plot" and "2D density of duration against step count" each finish in about 14 seconds via `create_chart(kind="box" / "histogram2d", sql=…)`, with no `save_as`, `figure_path` or `execute`.
5. Set `SANDBOX_PROVIDER=local`, run `docker compose up -d backend`, and confirm `execute`, its approval card, `save_as` / `figure_path` and the `python-fallback` skill all come back.
