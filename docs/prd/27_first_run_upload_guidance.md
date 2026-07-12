# PRD: First-Run Upload Guidance (v1)

**Status:** shipped · **Scope:** `frontend/` (chat empty state, Workspace Files) · **Extends:** [File Upload](01_file_upload.md)

## Why

NextRole only becomes useful once the user uploads a resume and a job description, but nothing in the UI drove that first action: the chat empty state offered suggestion chips, the hero subhead *said* "Drop in your resume and a job post" without an affordance, and the only upload control was a small ghost button in the Files card header. New users read the chips, typed a prompt, and got generic output because the agent had no documents.

The original idea was an animated arrow pointing from the chat to the Upload button. UX research (NN/g on attention animation, Appcues/Userpilot onboarding studies, empty-state literature) consistently rates coach marks/arrows as skipped and distracting; the reliable pattern for a critical first action is an actionable empty state — put the action where the user is already looking — plus at most a subtle pulsing hotspot on the real control. This feature ships that package instead of the arrow.

## What the user sees

While the user has **zero uploaded files** (paths under `/upload/`), the chat hero shows a dashed **upload card** between the subhead and the suggestion chips ("Add your resume or a job description — click to browse or drop files"). Click opens the picker; drag-and-drop uploads in place, filtering to PDF/DOC/DOCX/TXT/MD and toasting skipped files. The Files card shows a real empty state (icon, "No files yet", one line of copy, a primary **Upload files** button) instead of the old text-only line.

Once files exist, the hero card and empty state retire, and a dashed **Upload files** tile renders as the last item in the file grid — the persistent affordance for uploads #2+ (drag-and-drop works there too; the label swaps to "Drop to upload" while dragging). The Files header keeps an outlined **Upload** button in all states, carrying a small accent **pulse dot** until the user clicks any panel upload control. Every affordance uses the "Upload" verb — one action, one name. Deliberately absent: no arrow, no coach-mark overlay, no tour library, and the composer paperclip stays disabled (`COMPOSER_ATTACH_ENABLED`) — uploads live in the Workspace.

## How — the key architectural choices

**Guidance keys off uploads, not files, behind a `filesReady` gate.** `files` in `useChat` merges agent-generated state/store/artifact files, so "has files" is the wrong signal — `useUploadCue` filters for the `/upload/` prefix. The first `refreshFiles` runs before the assistant resolves (`graphId` null), skipping the artifact list entirely, so a naive "fetch finished" flag would flash the CTA at returning users; `filesReady` only flips after a fetch that ran with a non-null `graphId` (guidance appears ~1s after first paint — by design, never a flash-then-remove).

**One upload path, shared as hooks.** `FilesSection.handleSelect` and `ChatInterface.handleAttach` were near-identical copies; both now consume `useFileUpload` (upload → toasts → `appendUploadNote` → `refreshFiles`), and the drop handling (drag state, extension filter, skipped-toast) lives in `useUploadDrop`, shared by the hero card and the grid tile so the two drop targets cannot drift.

**The dot dismisses on click, not on upload.** The first cut auto-retired the dot when uploads appeared; owner testing immediately hit the gap — a user whose first upload came from the hero card never learned where the panel Upload button is, and after upload #1 all guidance vanished at once. The dot now persists (across sessions, through uploads) until the user clicks a panel upload trigger (header button, empty-state CTA, tile) — cancelling the picker still counts. Dismissal is `localStorage` `nr-upload-cue-dismissed-v2`; the key was bumped because v1 values were written under the old "uploaded once" meaning.

## Files of interest

| Concern | Path |
|---|---|
| Shared upload action + drag-and-drop hooks | `frontend/src/app/hooks/useFileUpload.ts` (`useFileUpload`, `useUploadDrop`) |
| Cue gating and click-to-dismiss persistence | `frontend/src/app/hooks/useUploadCue.ts` |
| `filesReady` signal (graphId-gated) | `frontend/src/app/hooks/useChat.ts` (lines ~119–123, ~170) |
| Hero upload card + shared hidden input | `frontend/src/app/components/ChatInterface.tsx` (empty-state block) |
| Files empty state, outlined Upload button, pulse dot | `frontend/src/app/components/workspace/FilesSection.tsx` |
| Upload tile in the file grid (`onAddFiles`/`onDropFiles`) | `frontend/src/app/components/TasksFilesSidebar.tsx` (`FilesPopover`) |
| `UPLOAD_ACCEPT` + `isAcceptedUploadName` drop filter | `frontend/src/app/lib/uploadFiles.ts` |
| Design language: `upload-dropzone`, `files-empty-state`, `file-add-tile`, `upload-cue-dot` | `frontend/DESIGN.md` |
| Regression coverage | `frontend/src/app/hooks/useFileUpload.test.tsx`, `useUploadCue.test.tsx`, `frontend/src/app/components/ChatInterface.test.tsx`, `TasksFilesSidebar.test.tsx` |

## Decisions worth remembering

- **The arrow was rejected, not deferred.** A cross-panel animated pointer is the coach-mark antipattern (skipped, naggy, fragile across the resizable panel layout). If guidance ever feels insufficient, strengthen the actionable surfaces — do not add arrows or a tour library; `DESIGN.md` records this as a hard rule on `upload-cue-dot`.
- **Empty states are permanent UX; only the dot is dismissible.** The hero card and Files empty state render whenever the user has zero uploads — deleting every file brings them back. The dot alone is persisted-dismissed, and deleting files must not resurrect it.
- **The tile answers "where do the next files go".** After upload #1 the empty states retire, and the owner's testing showed the ghost header button alone read as hidden. The in-grid tile (Drive/Dropbox pattern) is the persistent in-content affordance; the header button (upgraded ghost → outline) stays as the card-level command — the only entry when the card is collapsed, and the dot's anchor. Two entry points are intentional redundancy (NN/g multiple-entry-points), but they must share one label verb: "Upload".
- **Composer paperclip stayed disabled.** The hero card reuses the same hidden input the paperclip would use (moved out of the `COMPOSER_ATTACH_ENABLED` gate), so flipping the flag back on remains a one-line change without a second upload path.

## Deferred (intentional non-goals for v1)

- **Whole-panel or window-level drop targets.** Drag-and-drop is scoped to the hero card and the tile; a global handler risks hijacking drops meant for the browser and needs drag-enter choreography. Revisit if users demonstrably drop files onto the file list itself.
- **Per-user dismissal.** The dot's dismissal is per browser (`localStorage`), while `hasUploads` is per-user server truth. A second user on the same browser misses the dot but still gets the non-dismissible empty states. Suffix the key with a user id only if that becomes a real complaint.
- **Resume-vs-JD completeness guidance** (e.g. "you added a resume — now add the job description"). Needs content-type detection of uploads; the agent already asks for what it's missing.

## How to verify end-to-end

1. `docker compose up -d`; open the frontend port from `docker ps`. With auth enabled, sign up a throwaway user (fresh per-user state).
2. Fresh user: hero shows the dashed upload card between subhead and chips; Files card shows the empty-state block; the outlined Upload button carries a pulsing accent dot (static under `prefers-reduced-motion`).
3. Upload via the hero card (click or drop a PDF): card and empty state retire, the file card appears with the **Upload files** tile beside it, the composer pre-fills "Uploaded: …", and the dot **remains**.
4. Drop a `.pdf` + `.exe` together on the tile: the PDF uploads, a toast reports one skipped unsupported file.
5. Click any panel upload control, then cancel the picker: the dot disappears and stays gone across reloads (`localStorage` `nr-upload-cue-dismissed-v2` = `1`). Delete all files: empty states return; the dot does not.
6. Hard-reload with uploads present: no CTA flash at first paint. Run `pnpm --dir frontend test`.
