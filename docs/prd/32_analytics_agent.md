---
type: PRD
title: "Analytics Agent — natural-language questions over the warehouse"
description: "A second deepagents graph, picked from a top-bar switcher, that merges dbt/Cube/ClickHouse metadata into one dictionary, runs read-only SQL as a limit-bound warehouse user, and renders interactive Plotly charts that persist with the thread."
tags: [agent, backend, frontend, llm]
timestamp: '2026-09-05T18:40:00+07:00'
status: "shipped"
scope: "backend/agents/analytics_agent + frontend agent switcher & chart rendering"
version: v1
---

**Extends:** [Analytics Platform Phase 0 — warehouse, pipeline, semantic layer, BI](31_analytics_platform_phase0.md)

# Why

Phase 0 built the warehouse and left it without a consumer. dbt described every mart column
*explicitly for a future agent*, Cube defined the metrics with synonyms and caveats, and none of it
was reachable except by opening Superset and building a chart by hand. Questions the blueprint
listed as answerable on day one — who is active, how often runs fail, what the LLM costs per model —
each took a manual detour through a BI tool.

This phase adds the consumer: a second agent users pick instead of the Career Agent, which reads the
warehouse's own documentation before writing SQL, and draws the answer.

# What the user sees

The top-bar pill that used to be a read-only roster is now an **agent picker**. Choosing *Analytics
Agent* swaps the empty state, the composer placeholder and the starter prompts, scopes the thread
list to that agent, and puts `?agent=analytics_agent` in the URL. Choosing an agent starts a fresh
thread, because a conversation belongs to the agent that created it.

Asking a question produces the number with its time window and the caveat that changes how to read
it, plus a **chart card** when a shape carries the answer — a real Plotly figure with hover, an SQL
disclosure, and the row count. Charts persist: reopening the thread months later re-renders them
from storage. `table` and `kpi` answers are drawn with the app's own components rather than Plotly.

Access is an explicit allowlist. The agent reads every user's activity, so with auth on the caller's
id or email must appear in `ANALYTICS_AGENT_ALLOWED_USERS`; others see the entry greyed out with
"Administrators only". Deliberately absent: no per-user row scoping (this is an operator tool), no
Cube query path (Cube is the metric dictionary, not the query engine), and no mermaid — charts are
data, not pictures.

# How — the key architectural choices

**Skill-heavy with three purpose-built tools, not a tool per operation.** `describe_data`,
`run_sql`, `create_chart`, plus the deepagents built-ins. ClickHouse's own agent skills take the
same shape, and the FlexSQL work (2026) shows exploration, inspection and execution must stay
available *throughout* reasoning rather than as a fixed pipeline. The one wrapper that earns its
keep is metadata: ClickHouse column comments were empty, so meaning lived only in dbt YAML and Cube
`meta`. `describe_data` merges all three because none is complete alone — dbt has the prose, Cube
has the formulas and caveats, ClickHouse has types, row counts and sort keys.

**The security boundary is a warehouse user, not the tool.** `analytics_agent` holds SELECT on the
marts and staging databases only, under a `readonly=2` profile whose limits carry
`<constraints><max>` so a model-written `SETTINGS` clause can lower them but never raise them. The
tool-side statement check exists to turn obvious mistakes into readable text, not to enforce
anything. ClickHouse access-filters `system.*`, so `describe_data` physically cannot see bronze.

**Charts are stored artifacts, not stream events.** `create_chart` builds a Plotly figure server-side
and writes an envelope — figure plus SQL, mapping and row count — to
`/charts/<thread>/<slug>.plotly.json` in object storage. The card is rebuilt from the tool call in
message history, so a reopened thread renders every chart with no extra machinery. Envelopes carry
no colours: the viewer's theme is applied at render, so one file reads correctly in light and dark.

**Agent knowledge is placed by how often it is needed.** Progressive disclosure is right for
optional knowledge and wrong for knowledge every request needs. The ClickHouse rules that cause
errors live in the always-loaded memory file *and* are appended to `describe_data`'s output (read
immediately before the first query) *and* are attached to the matching ClickHouse error codes. The
skill carries workflow; `references/` carries the long tail.

# Files of interest

| Concern | Path |
|---|---|
| Graph assembly, routes, middleware stack | `backend/agents/analytics_agent/agents.py` |
| The three tools | `backend/agents/analytics_agent/tools.py` |
| Read-only SQL: statement guard, limits, error hints | `backend/agents/analytics_agent/warehouse.py` (`_ERROR_HINTS`, `_illegal_aggregation_hint`) |
| Merged data dictionary (dbt + Cube + ClickHouse) | `backend/agents/analytics_agent/metadata.py` (`SQL_REMINDER`, `build_snapshot`) |
| Chart specs, figures, stored envelope | `backend/agents/analytics_agent/charts.py` (`PUBLISH_ONLY_KINDS`, `build_envelope`) |
| Per-thread scratch for the Python fallback | `backend/agents/analytics_agent/scratch.py` |
| Always-loaded warehouse rules and caveats | `backend/agents/analytics_agent/ANALYTICS_AGENT.md` |
| Discovery loop, dialect crib, question recipes | `backend/agents/analytics_agent/skills/analytics-agent/` |
| Admin allowlist + pre-model refusal | `backend/agents/analytics_agent/access.py`, `middleware.py` |
| Read-only warehouse user | `analytics/clickhouse/users.d/analytics-agent.xml` |
| Artifact area → owning agent registry | `backend/agents/career_agent/object_storage.py` (`AREA_ROOTS`) |
| Shell operator + path-capture fixes | `backend/agents/career_agent/shell_backend.py` (`_translate`, `_rewrite_token`) |
| Scratch paths exempt from approval | `backend/agents/career_agent/execute_approval.py` (`_is_allowed_path`) |
| Agent availability for the picker | `backend/agents/files_api.py` (`/agents/available`) |
| Agent registry + switcher | `frontend/src/app/config/agents.ts`, `components/TopBar.tsx` |
| Chart card, theme, native table/KPI | `frontend/src/app/components/charts/` |
| Envelope contract mirrored for the frontend | `frontend/src/app/lib/charts.ts` |

# Decisions worth remembering

- **Direct read-only SQL instead of the blueprint's Cube-only path.** The blueprint routed the agent through Cube's REST API with a per-user JWT. Cube Core's security context is not wired here, and this agent is operator-facing — it answers across all users by design, gated by an allowlist rather than scoped per caller. Cube remains the metric dictionary the agent reads; it is not the query engine. `analytics/README.md` was corrected rather than left contradicting the code.
- **The general-purpose subagent stays enabled.** deepagents 0.7 auto-adds one, and the only off switch is a process-global harness-profile registry keyed by model spec — flipping it would strip the Career Agent's too. The owner's call was to govern this in the system prompt, memory and skills rather than build tool-filtering machinery to prevent behaviour the prompt layer already handles. The assembled-prompt snapshot test pins it.
- **`shlex.join` was silently breaking every shell operator.** `VirtualPathShellBackend._translate` tokenized and rejoined, which quotes each token: `a && b` reached the shell as `a '&&' b`, printed the operator as a literal, and exited 0. The Career Agent never hit it (rendercv commands have no operators); the analytics fallback did, and one run burned 21 `execute` calls with the model correctly diagnosing that it needed `sh -c` wrapping. `_translate` now substitutes virtual paths in place and returns the command untouched when there is nothing to rewrite. A second bug in the same function captured any top-level absolute path — `/tmp` has the root as its parent, and the root always exists.
- **ClickHouse error 184 has two causes with opposite fixes.** `sum(x) AS x` shadows the column so the next aggregate nests it; the same code appears as "found in WHERE" when the shadowed name is used there. A static hint told the model to move the condition to `HAVING`, which *runs* and silently filters the aggregate instead of each row — a wrong answer, not an error. The hint is now derived from the message: if the alias also appears inside the aggregate ClickHouse names, it says rename; otherwise it says move to `HAVING`.
- **Rules are stated generically, never with one column as the example.** A model got `sum(est_cost_usd) AS total_cost_usd` right — matching the illustration it had seen — then broke the identical rule on `input_tokens` and `output_tokens` in the same query. The guidance now uses `sum(x) AS x` and says to check *every* aliased aggregate, since the error names only the first.
- **`table` and `kpi` skip Plotly.** They render with the app's own components, which keeps the browser on Plotly's cartesian bundle (1.5 MB, lazily loaded) and makes the most common "just show me the number" answer match the rest of the UI. The bundle's trace list is also why `create_chart` accepts `box`/`violin`/`contour`/`histogram2d` only via `figure_path`, and why sankey/funnel/waterfall are out of reach.
- **Charts render once.** `ChartCard` draws every `create_chart` call, and the markdown `img` override draws any `.plotly.json` reference. When a model restated the chart in prose, both fired. `MarkdownContent` now takes `embedCharts`, passed only by the report viewer; in chat a chart reference becomes a file link, matching how every other path behaves in replies.
- **`describe_data` over `information_schema`.** The warehouse user cannot read `information_schema`, which a model reaches for naturally. Granting it was rejected: it returns column names without the meanings and caveats that decide whether a number is right. The access-denied hint names the working path instead.
- **The allowlist accepts emails because the JWT already carries one.** `auth.py` was discarding every claim but `sub` and `name`. Carrying `email` through makes the allowlist usable with readable identifiers instead of opaque ids; `identity` remains the only claim that drives scoping.

# Deferred (intentional non-goals for v1)

- **Per-user row scoping** (ClickHouse row policies, a Cube `queryRewrite` security context). Would let the agent be offered to every user rather than an allowlist; revisit when non-operators need it.
- **A Cube `/v1/load` query tool.** Cube is read for metric definitions only. Worth adding if metric drift between hand-written SQL and Cube's definitions becomes a real cost.
- **Trace types outside Plotly's cartesian bundle** — sankey, funnel, waterfall, treemap, 3D. The agent would store one and the card would render empty; switching bundles costs 3 MB.
- **A dashboard surface** beyond the Workspace file list and markdown reports in `/reports/`.
- **Live LLM evals.** Golden questions ship as a documented manual script in the package README; `backend/CLAUDE.md` still bars the `eval` marker.
- **Moving shared plumbing to `agents/shared/`.** The analytics agent imports scope, storage, middleware and the shell backend from `career_agent/`; a move would touch ~15 test files for no behavioural gain.

# How to verify end-to-end

1. `docker compose up -d --build`. Generate `CLICKHOUSE_AGENT_PASSWORD` in `.env` first; with auth on, add yourself to `ANALYTICS_AGENT_ALLOWED_USERS`.
2. As `analytics_agent` in ClickHouse `/play`: a marts SELECT succeeds; `INSERT` and a bronze SELECT fail with 497; `SELECT 1 SETTINGS max_execution_time=300` fails with 452; `SHOW GRANTS` lists exactly two grants.
3. `curl :$LANGGRAPH_LOCAL_PORT/agents/available` — both graphs, with `allowed` reflecting the allowlist.
4. In the UI, switch to the Analytics Agent from the top-bar pill. The empty state, placeholder and starter prompts change; the thread list shows only that agent's threads.
5. Ask "How many runs failed this week versus last, by day? Chart it." Watch `describe_data` → `run_sql` → `create_chart`. The card hovers, re-themes on the dark toggle, and reveals its SQL. Reload and reopen the thread — the chart is still there, exactly once.
6. Ask for a box plot (see the package README's sample questions). `run_sql(save_as=…)` exports a CSV, the Python script pauses for approval, `create_chart(figure_path=…)` publishes it. Afterwards `git status` is clean — scratch never touches the repo tree.
7. `cd backend && uv run pytest` and `cd frontend && pnpm test`, then `pre-commit run --files $(git ls-files --modified --others --exclude-standard)`.
