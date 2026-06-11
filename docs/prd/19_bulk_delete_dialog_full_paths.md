# PRD: Bulk-Delete Dialog Shows Full Paths (v1)

**Status:** shipped · **Scope:** Workspace > Files panel · **Extends:** [Multi-Select Delete for Workspace Files](14_multi_select_file_delete.md)

## Why

[PRD 14](14_multi_select_file_delete.md) specified the bulk-delete confirmation dialog as "a bulleted list of basenames". That spec hid a collision: the agent routinely writes same-named artifacts into different directories — a battlecard run and a tailored-resume run both emit `<jd-slug>.pdf` under their own folders. Selecting both for deletion rendered two `<li>` rows keyed by the same basename string, and React flagged it (`Encountered two children with the same key…`), surfacing as a `1 Issue` dev-overlay badge ([#13](https://github.com/tam159/next-role/issues/13)). Beyond the warning, non-unique keys let React duplicate or omit rows — and the user saw two identical lines in a permanent-deletion preview with no way to tell which file was which.

## What the user sees

The `Delete N files?` dialog now lists each selected file by its **full virtual path** (`/tailored_resume/<slug>/<jd-slug>.pdf`), one row per file, in a dialog widened to `sm:max-w-2xl` for the multi-file case. Paths wrap at hyphen boundaries (`break-words`), and the taller list (`max-h-72`) fits all five preview rows without scrolling. The 5-row cap with `and N more` is unchanged. The single-file dialog (`Delete file?`) keeps the compact width and still shows just the basename, exactly as before.

## How — the key architectural choice

**Render full paths for every row, keyed by path — not collision-only disambiguation.** Three shapes were considered: (a) key rows by path but keep displaying basenames — fixes the React error yet still shows two identical lines for a destructive action; (b) key by path and show a dimmed directory prefix only on colliding basenames — cleanest list, but needs a collision-counting pass and gives the same list two visual modes; (c) always show full paths. (c) was chosen (explicit user call): in a permanent-deletion preview, ambiguity is worse than noise, the full path is what the file cards' tooltips already show, and the implementation *removes* code (the basename-mapping `pendingNames` memo) instead of adding a counting pass.

## Files of interest

| Concern | Path |
|---|---|
| Dialog rows keyed + rendered by full path; `pendingNames` memo → plain `pendingPaths` | `frontend/src/app/components/TasksFilesSidebar.tsx` (`FilesPopover` delete dialog, lines ~270–400) |
| Basename for the unchanged single-file copy | `frontend/src/app/lib/fileCategories.ts` (`splitFilePath`) |

## Decisions worth remembering

- **Keys come from `pendingDelete`, never from derived display strings.** The paths are unique by construction (spread from the `selected` `Set`). Any display transform — basename, truncation — can collide, which was exactly the v1 bug. A repo-wide grep confirmed this list was the only basename-keyed render; the file-card grid already keys by path.
- **Single-file dialog keeps the basename.** PRD 14 promised "single-delete reads exactly as before", and a lone `<span>` can't key-collide. The basename now comes from the existing `splitFilePath` helper instead of an inline `split("/").pop()`.
- **Wider fixed dialog + `break-words`, not a user-resizable one.** The first cut kept `max-w-md` and wrapped with `break-all`, which chopped paths mid-token and forced scrolling at three rows — hard to read in exactly the dialog that guards a permanent delete. The bulk branch now widens to `sm:max-w-2xl`; the `sm:` prefix matters, because the shadcn `DialogContent` base carries `sm:max-w-lg` and tailwind-merge only replaces same-variant classes — an unprefixed `max-w-*` override silently loses on desktop (the pre-existing `max-w-md` was in fact rendering at `lg`). Rows wrap at hyphen boundaries via `break-words` (slugs are hyphen-heavy, so lines break legibly; over-long unbroken runs still force-break). Drag-to-resize was considered and rejected: native CSS `resize` fights Radix's `translate(-50%,-50%)` centering — the box re-centers while dragging so the handle drifts away from the cursor — and a clean version needs custom handles, position pinning, and size persistence: a lot of interaction surface for a confirmation glanced at for seconds. Content is bounded anyway (`DELETE_PREVIEW_LIMIT` = 5 rows), so a fixed wider cap fits everything.

## Deferred (intentional non-goals for v1)

- **Smart path truncation (middle-ellipsis, common-prefix folding).** Raw paths read fine at current workspace depth (2–3 segments). Revisit if nesting grows or paths start drowning the dialog.

## How to verify end-to-end

1. Open a thread whose workspace holds two same-named files in different directories (a battlecard + tailored-resume run produces `<jd-slug>.pdf` in both folders).
2. Select both (plus others) in Workspace > Files and click `Delete` in the action bar.
3. The dialog lists full paths in the widened box — the two same-named files are distinguishable, all five rows visible without scrolling, lines breaking at hyphens. The browser console (relayed to `docker compose logs frontend` as `[browser]` lines) shows no `Encountered two children with the same key` error, and the dev overlay shows no issue badge.
4. Cancel → the selection is preserved. The per-card trash icon still opens the single-file dialog showing only the basename.
