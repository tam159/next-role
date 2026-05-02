# PRD: File Upload (v1)

**Status:** shipped · **Scope:** career_agent only

## Why

The career agent's flow (`backend/app/career_agent/README.md`) starts with the user uploading a CV and optional JD into `upload/`. Without this entry point, nothing else (process → research → custom resume → interview prep) can run. This is the first user-facing feature on the agent.

## What the user sees

Two upload surfaces, same backend:

1. **Paperclip in the chat composer** (`ChatInterface.tsx`) — picks files, uploads, then auto-injects `Uploaded: <names>` into the textarea so the user can hit Send and the agent has explicit context.
2. **Upload button in Workspace > Files** (`FilesSection.tsx`) — for batch management. The Files section is always visible (even empty) so the affordance is discoverable.

Accepted: `.pdf`, `.doc`, `.docx`, `.txt`, `.md`. Max 10 MB each. Re-uploading the same filename overwrites. Files appear in the Files grid; clicking opens a preview dialog (PDF via `<iframe>`, DOCX via `mammoth`, text/markdown rendered, `.doc` falls through to "Use Download").

**Deletion** (also v1) has two surfaces with shared confirmation:

1. **Hover-revealed trash icon** on each file card — primary path, single click → confirm.
2. **Delete button in the preview dialog header** — for when the user is already inspecting and decides to remove.

Both open the same `Delete file?` confirmation (filename in mono, destructive-styled Delete button). No undo-toast pattern; the confirm step is the only friction. The dialog is reused (`@radix-ui/react-dialog` via `components/ui/dialog.tsx`) — no new primitive added.

## How — the key architectural choice

**The frontend writes directly to the agent's on-disk path; no Python HTTP endpoint exists.**

`docker-compose.yml` already mounts the entire repo into the frontend container (`.:/deps/next-role`), so a Next.js API route running in the FE container can `fs.writeFile` to `backend/app/career_agent/upload/<filename>`. The agent's `FilesystemBackend(root_dir=CAREER_AGENT_DIR)` (in `agents.py`) reads the same path on its next tool call. No LangGraph custom routes, no FastAPI sibling, no PDF parser shipped in v1.

The path allowlist that gates writes lives in `frontend/src/app/config/agentFiles.ts` under `AGENT_FILE_SOURCES.career_agent.disk` — the existing `resolveSafe()` in `frontend/src/app/api/files/_lib.ts` enforces it.

## Files of interest

| Concern | Path |
|---|---|
| Upload API route (multipart POST) | `frontend/src/app/api/files/upload/route.ts` |
| Delete API route (DELETE) | `frontend/src/app/api/files/delete/route.ts` |
| Shared client helpers (`uploadAgentFiles`, `deleteAgentFile`) | `frontend/src/app/lib/uploadFiles.ts` |
| Disk allowlist for `career_agent` | `frontend/src/app/config/agentFiles.ts` |
| Composer paperclip | `frontend/src/app/components/ChatInterface.tsx` |
| Workspace upload button | `frontend/src/app/components/workspace/FilesSection.tsx` |
| File grid + trash icon + confirm dialog | `frontend/src/app/components/TasksFilesSidebar.tsx` (`FilesPopover`) |
| Preview rendering (PDF/DOCX/MD/text) + Delete button | `frontend/src/app/components/FileViewDialog.tsx` |
| `removeFile` callback (resolves virtual → disk path) | `frontend/src/app/hooks/useChat.ts` |
| Agent's filesystem-backed root | `backend/app/career_agent/agents.py` (CompositeBackend default) |
| Privacy-gated gitignore | `backend/.gitignore` (free-floating `upload/`, etc.) |

## Decisions worth remembering

- **Global file scoping**, not per-thread — uploads land at `upload/<filename>` flat. Matches the README, simpler for a solo user. Revisit if multi-thread workflows emerge.
- **No backend HTTP endpoint** — would need a custom langgraph-api route or a sibling FastAPI app; not justified when the bind-mount path already works.
- **Snake_case directory convention** — output dirs use `custom_resume/`, `interview_prep/`, etc., matching the routes already declared in `agents.py`. Existing skills dirs (`skills/custom-resume/`, `skills/interview-prep/`) are inputs, left as-is.
- **PII never goes to git** — `backend/.gitignore` blocks `upload/`, `research/`, `custom_resume/`, `interview_prep/`, `interview_cheat_sheet/`. Verify empirically with `git add -A --dry-run` after any rule change.
- **Mammoth for DOCX preview** — runs in browser via dynamic import. Types stub at `frontend/src/types/mammoth.d.ts` since the package ships none.

## Deferred (intentional non-goals for v1)

- **Document processing** — extracting text from PDF/DOCX into `/processed/` is step 2 of the agent flow, a separate feature. v1 only stores raw bytes.
- **`.doc` (legacy Word) preview** — no good in-browser parser; falls back to download.
- **Per-thread upload scoping** — see decisions above.
- **Drag-and-drop** — only file picker for v1.

## How to verify end-to-end

1. `docker compose up -d` and grab the frontend host port from `docker ps`.
2. Open the UI, upload a PDF and a DOCX via either surface, watch toast + file appearing in Files.
3. From the host: `ls backend/app/career_agent/upload/` shows the bytes.
4. In chat, ask the agent `List the files in /upload/` — its `ls` tool should return them.
5. `git add -A --dry-run backend/app/career_agent/` should stage nothing under `upload/`.
