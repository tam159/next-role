---
type: PRD
title: "Multi-Select Delete for Workspace Files"
description: "Hover checkboxes, shift-click ranges, and a bulk action bar fan one confirmation across many file deletions with a single refresh."
tags: [frontend, files, ui]
timestamp: '2026-05-26T17:00:18+07:00'
status: "shipped"
scope: "Workspace > Files panel"
version: v1
---

# Why

The Workspace > Files panel lets users delete files one at a time — hover a card, click the trash icon, confirm the dialog, wait for the refresh. After a run with the [Multi-Turn Updates](12_multi_turn_updates.md) workflow a thread typically accumulates 10+ artifacts (processed CV/JD/intake, research, tailored resume YAML+PDF, interview-prep doc, battlecard JSON+PDF, plus the original uploads), and cleaning house between iterations means N click-confirm cycles. The single-delete path is fine for the one-off; what's missing is a way to fan out a single confirmation across many files.

# What the user sees

A subtle checkbox appears in the top-left corner of each file card on hover, mirroring the existing trash-icon affordance in the top-right. Clicking the checkbox selects the card — the checkbox stays visible, and the card gets a ring outline. The card body itself still opens the file preview; selection and open are separate click targets.

When at least one file is selected, a slim action bar appears above the grid: `N selected` on the left; `Select all`, `Clear`, and a destructive `Delete` on the right. The bar uses the same `bg-muted-secondary` rounded chip styling as the existing section headers — it's not modal.

Keyboard shortcuts that match Finder / Drive:

- **Shift-click** a second checkbox to range-select between it and the last directly-clicked card (anchor pivots on plain clicks, not on shift-clicks).
- **Cmd/Ctrl + A** to select all (only while the panel has focus — engaging selection auto-focuses the wrapper).
- **Esc** to clear the current selection.

Confirming a bulk delete opens the same Radix `Dialog` used by the single-file path, with the title and body branching on count: `Delete N files?` and a bulleted list of basenames (capped at 5 + `and N more`). Single-delete reads exactly as before. On partial failure the toast reads `Deleted X of N (Y failed)`, and the selection is narrowed to the still-present failed paths so the user can retry; on full success the selection clears.

The bar's `Delete` button is disabled while the agent is streaming (`editDisabled`), matching the per-card trash icon. The checkboxes themselves stay enabled — users can stage a selection while the agent thinks and fire it once the run lands.

# How — the key architectural choices

**Drive-style hover checkboxes, not an explicit "Select" mode toggle.** Three patterns were on the table: (a) hover-revealed checkboxes with no mode switch (Drive, Dropbox), (b) an explicit "Select" button in the section header that flips the whole panel into bulk mode (iOS Photos), (c) invisible-but-Cmd-click-discoverable selection (Finder power-user). The Drive shape scales from 1→many with zero ceremony — there is no mode to enter and exit, and the card body click still opens the file in the same gesture model as v0. The Photos shape was rejected because it adds an extra click and a state the user has to remember to leave; the Finder shape was rejected because the affordance is invisible.

**No backend bulk-delete endpoint — fan out on the frontend with one refresh at the end.** The `DELETE /api/files/delete?path=…` route (LangGraph-managed, see `backend/CLAUDE.md`) takes one path per call. A new `removeFiles(paths)` helper in `useChat.ts` runs `Promise.allSettled` over per-path `deleteAgentFile` calls and `refreshFiles()`s **once** after they all settle. Considered (a) looping the existing `removeFile` from the UI — rejected because each `removeFile` triggers its own `refreshFiles`, so N deletes mean N refetches; (b) adding a backend bulk endpoint — rejected because the backend schema is owned by `langchain/langgraph-api:3.13` and adding a custom route would be a bigger commitment than the savings (one HTTP round-trip per file is cheap; the wasted refreshes were the actual cost).

**Custom `role="checkbox"` button, not a new `@radix-ui/react-checkbox` dependency.** The codebase already styles primitives one-off (the existing trash icon is a plain `<button>` with `aria-label`). Pulling Radix Checkbox in would require `pnpm --dir frontend add` + `docker compose restart frontend` per the project conventions, plus a small shadcn-style wrapper file, for a single surface that's effectively a 5×5 button with a `Check` icon. The custom button declares `role="checkbox"` and `aria-checked` so screen readers see it the same way.

**One delete dialog for 1-or-many.** `pendingDelete` widened from `string | null` to `string[] | null`. The single-card trash icon now calls `setPendingDelete([path])`; the bulk-bar Delete calls `setPendingDelete([...selected])`. The dialog title and body branch on `pendingDelete.length`. Considered keeping a separate `pendingBulkDelete` state — rejected because the two paths share the same loading state, same Cancel semantics, and same `editDisabled` gate; two dialogs would have meant two of every prop.

# Files of interest

| Concern | Path |
|---|---|
| `removeFiles(paths)` helper — `Promise.allSettled` + single `refreshFiles()` | `frontend/src/app/hooks/useChat.ts` (`removeFiles`) |
| Selection state, action bar, keyboard handlers, unified dialog | `frontend/src/app/components/TasksFilesSidebar.tsx` (`FilesPopover`) |
| Corner checkbox + ring outline + delete affordance on each card | `frontend/src/app/components/TasksFilesSidebar.tsx` (`FileCard`) |
| Threads `removeFiles` through props | `frontend/src/app/components/workspace/FilesSection.tsx`, `Workspace.tsx` |
| Reused per-file delete primitive | `frontend/src/app/lib/uploadFiles.ts` (`deleteAgentFile`) |

# Decisions worth remembering

- **Shift-click extends, plain click toggles, anchor only moves on plain click.** A pure "selection range from anchor → current" without anchor stability means a second shift-click would re-anchor mid-range; users expect successive shift-clicks to pivot from the *first* anchor (Finder behavior). Plain clicks set the anchor; shift-clicks read it but don't move it.
- **Selection auto-focuses the wrapper once `selected.size > 0`.** The wrapper has `tabIndex={-1}` and an `onKeyDown` for Esc / Cmd+A. Without the auto-focus, the first checkbox click leaves focus on the checkbox button and `keydown` bubbles up correctly — but if the user clicks the card *body* of a selected file to open it and then dismisses the viewer, focus may be elsewhere. Calling `wrapperRef.current?.focus()` whenever the selection grows from zero keeps the shortcuts live without forcing the user to click into empty grid space.
- **Partial-failure UX narrows the selection to the failures, doesn't clear.** After a bulk delete with errors, the surviving selection equals the set of paths that didn't delete. The bar still says `N selected` (= remaining failures) and the cards still show their checkmarks. The user can immediately retry just those, see why they failed in the toast, or `Clear` and walk away. Clearing entirely on partial success would lose the "what's left" signal.
- **`useEffect` prunes the selection on `files` change.** When `refreshFiles` returns and the `files` map drops a path (deleted-by-this-action, or deleted out-of-band by the agent), the selection Set could keep a stale entry that would never be reachable to deselect. The effect compares the Set against the new keys and drops anything missing, but only sets state if something actually changed (no-op re-renders are avoided by the `changed` flag).
- **Sticky action bar inside the scroll wrapper, not anchored to the panel header.** `position: sticky; top: 0` on the bar means it pins to the top of the file grid as the user scrolls a long list, but it lives *inside* `FilesPopover` so it disappears when the Files section is collapsed (`filesOpen=false`) — no orphan bar on a hidden grid.
- **Reply / toast wording matches the rest of the app.** Single delete keeps `Deleted <basename>`; bulk full-success says `Deleted N files`; partial says `Deleted X of N (Y failed)` (toast level `warning`). Total failure is a single `error`. The wording mirrors the `Wrote / Updated` distinction from [PRD 12](12_multi_turn_updates.md) — concrete count + outcome.
- **Checkbox button stops propagation; the card body click is independent.** `e.stopPropagation()` on the checkbox `onClick` is what keeps a checkbox click from also opening the file preview. Without it, the same click would toggle selection *and* fire the surrounding `onOpen`, which is hostile.

# Deferred (intentional non-goals for v1)

- **Backend bulk-delete endpoint.** N parallel HTTP `DELETE`s with a single refresh is fast enough at the file counts we see (10–20 typical, 100s at the high end is hypothetical). If we ever batch-clean thousands, a bulk endpoint becomes worth the LangGraph customization.
- **Drag-to-select / marquee.** Density is low (cards are ~220px wide, ~10 visible at once); the checkbox + shift-click pair covers the same intent without a new gesture.
- **Touch / long-press selection.** Workspace is desktop-first; no mobile signal yet.
- **Undo / trash bin.** The backend `DELETE` is hard — files are unlinked from disk. The two-step confirmation dialog is the only safety net. If we start seeing "I deleted the wrong file" reports, a per-thread snapshot folder (sibling to [PRD 12](12_multi_turn_updates.md)'s deferred snapshot idea) becomes the natural follow-up for both single and bulk delete.
- **Selection persistence across panel collapse / thread switch.** Collapsing the Files section unmounts `FilesPopover` and drops the selection. Acceptable for a destructive action — re-selecting after navigating away is a small price for not leaving a loaded "delete N files" gun cocked.
- **`Cmd+Click` / `Ctrl+Click` on the card body to toggle without the checkbox.** Power-user shortcut; not requested, and the always-visible-on-hover checkbox makes it redundant for discovery.

# How to verify end-to-end

1. `docker compose up -d`; grab the frontend host port from `docker ps`. Open Workspace > Files in a thread with ≥3 files.
2. Hover a card → corner checkbox appears top-left. Click it → card shows a ring, checkbox stays visible. The action bar appears above the grid: `1 selected · Select all · Clear · Delete`.
3. Click a second card's checkbox → bar reads `2 selected`. Click `Delete`. Dialog title is `Delete 2 files?`; body is a bulleted list of the two basenames. Cancel — dialog closes, bar still shows `2 selected`, both cards still checked.
4. Click `Delete` again, confirm. Toast reads `Deleted 2 files`; both cards disappear; the file count in the section header drops by 2; the panel does one refetch, not two.
5. Click a card body of a *selected* file (before deleting) → the file viewer opens (the click did not just toggle selection).
6. Shift-click test: click the first card's checkbox, then shift-click the third. The middle card also gets selected (range from anchor `1` → `3`).
7. Cmd/Ctrl + A while the panel has focus → all cards selected. Press Esc → all cleared. The bar disappears.
8. Partial-failure simulation: in DevTools, throttle network or block one `DELETE` call. Confirm a bulk delete of 2 → toast `Deleted 1 of 2 (1 failed)`, the failed card stays in the grid *and* stays selected; the succeeded card disappears.
9. With the agent running (send a message and let it stream), the bar's `Delete` button is disabled and the per-card trash icon is disabled, while the checkboxes remain interactive.
10. Single-card trash icon still works and routes through the same dialog (title now says `Delete file?`, body keeps the original `<filename> will be permanently removed` copy).
