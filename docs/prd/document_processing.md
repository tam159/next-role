# PRD: Document Processing (v1)

**Status:** shipped · **Scope:** career_agent only

## Why

File Upload (v1) lets the user drop CVs/JDs onto disk, but the agent only sees filenames — it can't read PDF/DOCX bytes. Step 2 of the career agent flow (`backend/app/career_agent/README.md`) is converting those uploads into clean markdown the LLM can reason over, so downstream steps (research, custom resume, interview prep) have something to chew on. CVs and JDs are visually rich (multi-column layouts, tables, embedded charts) — naive text extraction loses structure that the agent needs.

## What the user sees

After uploading via the paperclip or Workspace > Files, the chat composer auto-injects `Uploaded: <names>`. The user hits Send and:

1. Agent calls `list_files("/upload/")` to ground itself in what's actually on disk (the `Uploaded:` line is treated as a hint, not a contract — users can edit it).
2. For each confirmed file, agent calls `parse_document(filename, save_as)` in parallel. The tool runs LlamaParse synchronously (~10–30 s per doc) and persists the markdown to `/processed/<slug>.md`. The slug is content-meaningful — e.g. `tam-nguyen-lead-ai-ml-resume.md`, `aitomatic-forward-deployed-engineer.md`.
3. Agent replies with one short line per saved path. No markdown is dumped into the chat.

In Workspace > Files, processed markdown shows up at `/processed/<slug>.md` — clickable, viewable, sorted newest-first alongside raw uploads. Re-uploading the same source file under the same slug overwrites the processed copy in place.

## How — the key architectural choices

**Tool persists the file itself; the LLM never re-emits the markdown.** The naive flow — tool returns the parsed string, agent emits a `write_file(path, content)` call — would push thousands of tokens of markdown back through the model on every parse, doubling latency and cost. We avoid it by closing the agent's `CompositeBackend` over the tool via a small factory (`make_parse_document(backend)` in `tools.py`) and calling `backend.write(...)` directly. The route `/processed` is mapped to a `StoreBackend` (postgres-backed langgraph store) so the file is reachable from any future thread without disk persistence concerns.

**Upsert via try-write-then-edit.** deepagents' `BackendProtocol.write()` refuses to overwrite by design ("read then edit"), and the protocol has no `delete`. Re-uploading a CV/JD must refresh the parsed copy, so `_upsert` (`tools.py`) tries `write` first; on the already-exists error it reads the current content and runs `edit(old_string=existing, new_string=new)` — a full-content swap. Preserves `created_at`, atomic from the store's perspective, and one round-trip on the happy path.

**`list_files` tool exists because the built-in `ls` hides metadata.** deepagents' `ls` middleware tool returns only path strings to the LLM (mtime/size live in a `ToolMessage.artifact` the model never sees) and there's no sort. We added a sibling tool that returns full `FileInfo` entries sorted newest-first, working uniformly across FilesystemBackend (`/upload/`) and StoreBackend routes (`/processed/`, `/research/`, etc.). This is what lets the agent reconcile when the `Uploaded:` line and the on-disk reality drift.

**UTC datetime injected per-call.** Without a clock the agent can't tell "uploaded 5 seconds ago" from "uploaded yesterday". `UtcDatetimeMiddleware` (`middleware.py`) appends `Current UTC datetime: <iso8601>` to the system message on every model call via `wrap_model_call` / `awrap_model_call` — fresh per turn, not baked at module import (the LangGraph process is long-lived).

**Slug picked by the LLM, not the tool.** The agent already has filename context in the user message and can pick a meaningful slug (`tam-nguyen-lead-ai-ml-resume` vs the mechanical `resume-lead-ai-ml-tam-nguyen`). Two-arg tool — `filename` for the source, `save_as` for the slug — keeps the tool dumb and the naming smart.

**Frontend store listing iterates configured `pathPrefixes`.** Original `fetchStoreFiles` queried a single namespace `[...namespacePrefix, assistantId]` — wrong shape for any agent whose backend uses per-route namespaces. Rewritten to derive namespace per `pathPrefix` by mirroring the backend's `CompositeBackend` route-stripping. Same helper (`resolveStoreLocation`) drives both list and write, so they always hit the same rows.

## Files of interest

| Concern | Path |
|---|---|
| Agent assembly + composite backend route map | `backend/app/career_agent/agents.py` |
| `parse_document` and `list_files` tool factories + `_upsert` helper | `backend/app/career_agent/tools.py` |
| Per-call UTC datetime middleware | `backend/app/career_agent/middleware.py` |
| Upload-handling block + reconciliation rules | `backend/app/career_agent/prompts.py` (`SYSTEM_PROMPT`) |
| Tool + middleware unit tests (LlamaParse mocked) | `backend/tests/test_career_agent_tools.py`, `backend/tests/test_career_agent_middleware.py` |
| Pytest config (rootdir + asyncio mode) | `backend/pyproject.toml` (`[tool.pytest.ini_options]`) |
| Frontend file listing (store + disk merge) | `frontend/src/app/lib/agentFiles.ts` (`fetchStoreFiles`, `resolveStoreLocation`) |
| Path-prefix → namespace config | `frontend/src/app/config/agentFiles.ts` (`AGENT_FILE_SOURCES.career_agent.store`) |
| LlamaCloud SDK | `llama-cloud>=2.4.1` (already in `backend/pyproject.toml`) |
| API key | `LLAMA_CLOUD_API_KEY` in `.env` (template at `.env.example:14`) |

## Decisions worth remembering

- **LlamaParse over local extraction.** PyPDF/pdfplumber chokes on multi-column resumes and JD tables. LlamaCloud's agentic tier handles them out of the box and gives 10K free credits/month — comfortable for solo use. The `cost_optimizer` flag routes plain pages to a cheaper tier automatically, so a typical resume costs a fraction of a credit.
- **Sync client, sync tool.** `client.parsing.parse()` blocks until the job finishes (LlamaCloud SDK polls for us). LangGraph runs sync tools in a thread executor that propagates contextvars, so `StoreBackend`'s lazy `langgraph.config.get_store()` lookup still works. No async plumbing needed.
- **Tier choice: `agentic` + cost optimizer.** Defaults from the LlamaCloud quickstart. `agentic_plus` is overkill for resumes; `cost_effective` loses table structure on JDs. Cost optimizer claws back budget on text-heavy pages.
- **`virtual_mode=True` on the FilesystemBackend.** Required for `CompositeBackend` route semantics — otherwise `/upload/foo.pdf` resolves as a real filesystem absolute path (root `/`) rather than relative to `root_dir`. Default flips in deepagents 0.6.0; we set it explicitly now.
- **`content_builder` config dropped.** Was a stale template entry in `agentFiles.ts`, referenced nowhere else in the repo. Verified by grep before deletion.
- **Re-parse over migration.** When we renamed `/upload/processed/` → `/processed/`, old store rows under namespace `("career_agent", "upload", "processed")` orphaned. Re-parse on next upload (idempotent via `_upsert`) was cheaper than writing a one-shot migration script for a handful of rows.

## Deferred (intentional non-goals for v1)

- **Pre-flight cost preview.** No "this will cost N credits, proceed?" UI before the parse fires. Solo user; small docs; trust the cost optimizer.
- **DOC (legacy Word) and exotic formats.** LlamaParse accepts them but we haven't validated end-to-end. The accepted-extensions list in `frontend/src/app/api/files/upload/route.ts` already gates this at upload time.
- **Per-thread scoping for processed files.** Global namespace `("career_agent", "processed")`, matching the global `/upload/` directory. Revisit if multi-thread workflows emerge.
- **Background processing with progress UI.** Tool call blocks for ~10–30 s per doc. Acceptable for now; the LangGraph trace shows progress to the dev. A streaming UI would be a separate front-end PRD.
- **Editing parsed markdown in-place from the UI.** `writeAgentFile` already routes correctly to the store via `resolveStoreLocation`, but the workspace-side editor flow hasn't been QA'd against store-source files yet.
- **Deleting processed files from the UI.** `useChat.ts:removeFile` explicitly throws "Only disk-backed files can be deleted from the UI". Add `client.store.deleteItem(...)` when a real need arrives.

## How to verify end-to-end

1. `docker compose up -d` and confirm `LLAMA_CLOUD_API_KEY` is set in `.env`.
2. Upload a CV (PDF) via the chat paperclip. Composer auto-fills `Uploaded: <name>.pdf`.
3. Send. Watch the LangGraph trace (`http://localhost:<LANGGRAPH_LOCAL_PORT>/docs` or LangSmith if `LANGCHAIN_TRACING_V2=true`):
   - `list_files("/upload/")` fires first.
   - One `parse_document` call per file with a sensible `save_as`.
   - **No** subsequent `write_file` call regenerating the markdown.
   - Brief assistant reply naming the saved path.
4. Open Workspace > Files: `/processed/<slug>.md` appears, sorted newest-first, clickable for preview.
5. Query the langgraph store via the `next-role-postgres` MCP — confirm a row exists under namespace `("career_agent", "processed")` with the expected key.
6. **Edited-hint case.** Edit the textarea after upload to a name that doesn't exist on disk, send. Agent should call `list_files`, notice the mismatch, and ask one short clarifying question before parsing.
7. **Overwrite case.** Re-upload the same file (or invoke `parse_document` with the same `save_as`). The processed file updates in place; `created_at` stays, `modified_at` advances.
8. **Tests.** `cd backend && uv run pytest tests/ -q` — must stay 15/15 green; LlamaParse stays mocked.
