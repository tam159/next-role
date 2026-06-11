# PRD: Bulk-Delete Dialog Shows Full Paths (v1)

**Status:** shipped · **Scope:** Workspace > Files panel · **Extends:** [Multi-Select Delete for Workspace Files](14_multi_select_file_delete.md)

## Why

[PRD 14](14_multi_select_file_delete.md) specified the bulk-delete confirmation dialog as "a bulleted list of basenames". That spec hid a collision: the agent routinely writes same-named artifacts into different directories — a battlecard run and a tailored-resume run both emit `<jd-slug>.pdf` under their own folders. Selecting both for deletion rendered two `<li>` rows keyed by the same basename string, and React flagged it (`Encountered two children with the same key…`), surfacing as a `1 Issue` dev-overlay badge ([#13](https://github.com/tam159/next-role/issues/13)). Beyond the warning, non-unique keys let React duplicate or omit rows — and the user saw two identical lines in a permanent-deletion preview with no way to tell which file was which.

## What the user sees

The `Delete N files?` dialog now lists each selected file by its **full virtual path** (`/tailored_resume/<slug>/<jd-slug>.pdf`), one row per file, wrapping long paths mid-token (`break-all`) inside the existing scrollable list. The 5-row cap with `and N more` is unchanged. The single-file dialog (`Delete file?`) still shows just the basename, exactly as before.

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
- **`break-all` on rows, not a wider dialog.** Virtual paths are long, mono, and hyphen-heavy; mid-token wrapping inside `max-w-md` preserves the `max-h-40 overflow-y-auto` scroll behavior. Widening the dialog for one list wasn't worth diverging from the app's dialog sizing.

## Deferred (intentional non-goals for v1)

- **Smart path truncation (middle-ellipsis, common-prefix folding).** Raw paths read fine at current workspace depth (2–3 segments). Revisit if nesting grows or paths start drowning the dialog.

## How to verify end-to-end

1. Open a thread whose workspace holds two same-named files in different directories (a battlecard + tailored-resume run produces `<jd-slug>.pdf` in both folders).
2. Select both (plus others) in Workspace > Files and click `Delete` in the action bar.
3. The dialog lists full paths — the two same-named files are distinguishable. The browser console (relayed to `docker compose logs frontend` as `[browser]` lines) shows no `Encountered two children with the same key` error, and the dev overlay shows no issue badge.
4. Cancel → the selection is preserved. The per-card trash icon still opens the single-file dialog showing only the basename.
