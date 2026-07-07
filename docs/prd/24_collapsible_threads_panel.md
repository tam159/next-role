# PRD: Collapsible Threads Panel (v1)

**Status:** shipped · **Scope:** `frontend/` (thread navigation) · **Extends:** [UI/UX Modernization](20_ui_modernization.md)

## Why

The UI modernization moved threads out of the old 3-panel layout into a left drawer that could be pinned into a docked column. That worked, but it left thread navigation as two different surfaces: an unpinned Radix slide-over controlled by URL state, and a pinned in-layout column rendered through a separate branch. The result was visually modal for something users need constantly, and mechanically fragile around thread selection and the top-bar threads button.

This change makes thread history part of the workspace chrome. The threads surface is always the same panel: collapsed by default, docked into the content row on desktop, overlaying the content row on smaller screens, and controlled from the top bar. Pinning is now only a persistence preference for "keep this open", not a separate rendering mode.

## What the user sees

The top-bar threads button toggles a left **Threads** panel below the top bar. On desktop, it animates open as a 320px docked column and the chat/workspace split keeps working to its right. On tablet/mobile widths, the same panel overlays the content row with the app scrim and shadow; clicking the scrim closes it while the top bar remains visible and clickable.

The panel header is identical whether pinned or unpinned: status filter, pin/unpin button, and close button are always present. Pinning keeps the panel open when selecting threads and restores it open on the next visit. Closing from the top-bar toggle, the header X, or the mobile scrim always unpins, because a closed panel cannot also mean "persistently open". When unpinned, selecting a thread closes the panel after setting the `threadId`.

## How — the key architectural choices

**One mounted panel replaces drawer + dock branches.** `HomePageInner` now renders a single `ThreadList` inside an always-present wrapper. The wrapper animates width from `0` to `var(--sidebar-width)` while the inner `aside` keeps a fixed 320px width, so thread-card contents do not reflow mid-animation. The collapsed wrapper is marked `inert`, but stays mounted so the thread list can keep reporting interrupt counts to the top-bar badge.

**Panel open state is local UI state, not URL state.** The old drawer used a `sidebar` query param alongside `threadId`; same-tick `nuqs` updates had already caused selection bugs where one query setter rebuilt from a stale snapshot and clobbered the other. Panel visibility is not valuable as a shareable URL, so `useThreadsPanel` owns `open` locally and only leaves `threadId` in the URL.

**Pinned means "persistently open", not "use a different component".** `useThreadsPanel` stores the pinned preference in `localStorage` and lazily initializes both `pinned` and `open` from it, so returning users get the panel open on first paint. Closing always clears the preference; pin/unpin flips persistence without changing the current open state.

## Files of interest

| Concern | Path |
|---|---|
| Single panel rendering, desktop dock, small-screen scrim | `frontend/src/app/page.tsx` (`HomePageInner`, lines ~41–190) |
| Open/pinned state machine and persisted preference | `frontend/src/app/hooks/useThreadsPanel.ts` (`useThreadsPanel`, lines ~15–56) |
| Top-bar toggle state + `aria-expanded` / `aria-controls` | `frontend/src/app/components/TopBar.tsx` (lines ~90–96) |
| Shared pin + close header controls | `frontend/src/app/components/ThreadList.tsx` (lines ~249–270) |
| Removed Radix slide-over wrapper | `frontend/src/app/components/ThreadsDrawer.tsx` |
| Regression coverage for panel state and header controls | `frontend/src/app/hooks/useThreadsPanel.test.tsx`, `frontend/src/app/components/ThreadList.test.tsx` |
| Design-system language for `threads-panel` | `frontend/DESIGN.md` |

## Decisions worth remembering

- **The drawer URL was deleted instead of repaired.** The previous bug was not just an implementation mistake; it came from modeling a transient chrome state (`sidebar`) as URL state beside a real navigation state (`threadId`). Keeping only `threadId` in `nuqs` removes the stale-query failure mode and makes the top-bar icon a simple toggle.
- **Collapsed panels stay mounted.** Conditional unmount would have been simpler, but it would stop `ThreadList` from polling/reporting interrupt counts while closed. `inert` gives the accessibility affordance of a non-interactive collapsed subtree while preserving the data flow that powers the red interrupt badge.
- **Close is available while pinned.** The prior pinned column hid the X because "pinned" was treated as a special docked mode. The new model treats pin as persistence only, so users can always close the panel from the same place; closing also unpins to avoid the contradictory state of "closed but pinned open".
- **Small screens reuse the panel instead of resurrecting a drawer.** The old Radix drawer brought modal behavior, but maintaining a separate mobile surface would recreate the branch split this change removes. The responsive variant is the same DOM with absolute positioning, shadow, and scrim under `lg`.

## Deferred (intentional non-goals for v1)

- **User-resizable thread width.** The panel is fixed at `--sidebar-width` / 320px to keep the chat/workspace split predictable. Revisit only if thread titles or metadata regularly need more horizontal space.
- **Shareable panel visibility.** URLs continue to represent the active assistant/thread, not whether navigation chrome is open. Revisit only if a real workflow needs links that open with thread history visible.
- **Formal motion tokens.** The 200ms width and scrim fades live in Tailwind classes, consistent with the existing ad hoc motion noted in `DESIGN.md`.

## How to verify end-to-end

1. `docker compose up -d`; open the frontend host port from `docker ps`.
2. On desktop width, click the top-bar threads icon: the panel opens below the top bar, pushes the chat/workspace row right, and the button reports expanded state. Click the icon or header X: the panel closes.
3. Open the panel, click pin, select a thread: the thread loads and the panel stays open. Reload: the panel starts open. Close it: the panel closes and the pinned preference is cleared.
4. Open the panel unpinned and select a thread: the `threadId` changes and the panel closes after selection.
5. Narrow below `1024px`: opening the panel overlays the content row with scrim + shadow; clicking the scrim closes it while the top bar remains usable.
6. Run `pnpm --dir frontend test frontend/src/app/hooks/useThreadsPanel.test.tsx frontend/src/app/components/ThreadList.test.tsx`.
