# PRD

* [@langchain/react migration + subagent streaming re-enable](18_langchain_react_migration.md) - Swap the frontend to @langchain/react's v2 stream runtime — killing the O(n²) token concat — and flip subagent streaming back on.
* [Bulk-Delete Dialog Shows Full Paths](19_bulk_delete_dialog_full_paths.md) - The bulk-delete confirmation lists full virtual paths keyed by path, fixing duplicate-basename React key collisions and ambiguous rows.
* [Career-agent Stages 3–5 — recon, tailor, coach, battlecard](04_initialize_subagents.md) - Ship workflow stages 3–5: hiring-recon, resume-tailor, and interview-coach subagents plus the interview-battlecard skill.
* [Career-Agent Workflow Orchestration](08_agent_workflow.md) - Canonical v2 reference for the 5-stage workflow: skills-based subagents, parallel Stage 4, and the YAML→PDF resume pipeline end-to-end.
* [Chat Streaming Throttle](05_chat_streaming_throttle.md) - 80 ms hot-window render throttle and identity-stable caches that kept the chat surface responsive while parallel subagents streamed long tool args.
* [Collapsible Threads Panel](24_collapsible_threads_panel.md) - One always-mounted Threads panel — docked on desktop, overlay on mobile — replaces the drawer/dock split; pinning is just persistence.
* [Color-coded Workspace File Cards](09_file_category_colors.md) - Tint each Workspace file card's icon by its root folder so users can spot the right artifact at a glance.
* [Configurable LLM Models](15_configurable_llm_models.md) - Settings-dialog overrides for main-agent and subagent models via configurable keys and a shared ModelOverrideMiddleware.
* [Disable token streaming for subagents](16_disable_subagent_streaming.md) - Backend middleware flips disable_streaming on subagent models to kill an O(n²) SDK concat that froze the chat during parallel subagent runs.
* [Document Processing](02_document_processing.md) - Convert uploaded CVs/JDs into clean markdown with LlamaParse, persisted by the tool itself so the LLM never re-emits document bodies.
* [File Upload](01_file_upload.md) - Upload a CV and optional JD into the career agent's workspace — the entry point for the whole agent flow.
* [First-Run Upload Guidance](27_first_run_upload_guidance.md) - Actionable empty states, an in-grid upload tile, and a dismissible pulse cue drive the critical first upload — no coach-mark arrows.
* [Frontend Test Suite + Required CI Check](22_frontend_test_suite.md) - A 367-test Vitest suite with a third required CI check (frontend-tests) that skip-passes on backend-only and docs-only changes.
* [Interview Battlecard — JSON + PDF via weasyprint](11_interview_battlecard_pdf.md) - Stage 5 emits a hand-editable battlecard JSON plus an A4-landscape PDF rendered in-process with jinja2 + weasyprint.
* [JD-from-URL Extraction](03_jd_url_extraction.md) - Paste a JD URL in chat and Tavily-extract it into the same /processed/<slug>.md artifact that uploaded JDs produce.
* [Long-term user-preference memory](17_preference_memory.md) - A single always-loaded /memory/preferences.md persists durable user preferences across threads — saving is one edit_file, applying costs zero tool calls.
* [Multi-Select Delete for Workspace Files](14_multi_select_file_delete.md) - Hover checkboxes, shift-click ranges, and a bulk action bar fan one confirmation across many file deletions with a single refresh.
* [Multi-Turn Updates to Career-Agent Artifacts](12_multi_turn_updates.md) - After the one-shot pipeline, users can ask for in-place updates to any artifact — routed to the owning subagent in update mode with edit-by-default.
* [Multi-User Authentication & Per-User Isolation](26_multi_user_auth.md) - Opt-in accounts (Google + email/password) with JWT-verified, owner-scoped threads, files, and memory — zero-login mode stays byte-identical.
* [Object Storage for Binary Artifacts](25_object_storage_artifacts.md) - Move binary artifacts (uploads, PDFs) into S3-compatible object storage — SeaweedFS locally — behind an ObjectStoreBackend and a Python files API.
* [Own the agent server — drop the langchain/langgraph-api base image](21_own_agent_server.md) - Move the whole agent server in-repo on a python-slim image — replacing the closed langgraph-api base image with our own API, runtime, and core-server.
* [Print Workspace File as PDF](10_print_file_as_pdf.md) - A Print button in the file viewer renders markdown/code/DOCX into a hidden iframe and calls window.print() for a save-as-PDF flow with no PDF library.
* [Progressive Tool Disclosure](23_progressive_tool_disclosure.md) - Tool calls and subagent cards stream expanded while running and auto-collapse per unit into summary rows the moment they finish.
* [Subagents-with-Skills Refactor](06_subagents_with_skills.md) - Move each subagent's workflow out of inline YAML system prompts into per-consumer SKILL.md files so workflows become reusable units.
* [Tailored Resume — YAML + PDF via rendercv](07_tailored_resume_pdf.md) - resume-tailor writes a rendercv YAML as the editable source of truth and renders the recruiter-ready PDF via the execute tool.
* [UI/UX Modernization](20_ui_modernization.md) - Warm paper-and-espresso restyle with theme toggle and selectable accents — a repaint of tokens and chrome that preserves every streaming behavior.
* [Virtual-Path Translation in the execute Tool](13_execute_virtual_path_translation.md) - VirtualPathShellBackend rewrites virtual /-prefixed paths in execute commands so the agent uses one path convention end-to-end.

# Reference

* [PRD Bundle Guide](README.md) - How this folder works: an OKF knowledge bundle of per-feature PRDs — AI tools start at index.md, humans open viz.html.
