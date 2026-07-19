---
type: PRD
title: "UI/UX Modernization"
description: "Warm paper-and-espresso restyle with theme toggle and selectable accents — a repaint of tokens and chrome that preserves every streaming behavior."
tags: [frontend, ui]
timestamp: '2026-06-27T22:52:41+07:00'
status: "shipped"
scope: "frontend/ (whole UI)"
version: v1
---

**Extends:** [Color-coded Workspace File Cards](09_file_category_colors.md), [Configurable LLM Models](15_configurable_llm_models.md)

# Why

The app worked but looked like a generic LangGraph chat shell: a teal/green palette, a single Inter typeface, dark mode only via `prefers-color-scheme` (no toggle), and chrome that read as boilerplate (a "GenAI-Accelerated Career Advancement" text header, plain tool boxes, a resizable 3-panel layout). Claude Design produced a hi-fi handoff (`design_handoff_nextrole_ui/`) for a warm, editorial look. The goal was to adopt that visual language **and** add real theme/accent controls **while preserving every existing behavior** — streaming, subagent discovery, tool-approval interrupts, the three-source file workspace, thread hydration, and the streaming-perf work from [PRD 05](05_chat_streaming_throttle.md). Crucially the app already had the design's conceptual structure (chat + workspace with Plan/Files/Sources), so this is a **restyle, not a rebuild**.

# What the user sees

A warm "paper + espresso" interface with a **logo-matched emerald accent**. New top bar: the NextRole rocket logo + wordmark, the active thread's title/sub, a **theme toggle** (light/dark/system), settings, and an emerald "New thread" button. A **Settings** modal adds an Appearance section (theme + **4 selectable accents**: emerald/indigo/blue/coral, persisted) alongside the existing model/connection config. The empty state is an editorial **hero** ("Land your *next role*, faster." in Newsreader serif) with suggestion chips. Assistant turns show a logo avatar (once per turn) and a vertical **tool-activity timeline rail** (status node + connector + mono tool name, expandable). Threads moved to a left **slide-over drawer** that can be **pinned** into a persistent docked column. Workspace cards were refreshed (Plan progress bar + todo timeline, Files as a 2-up grid colored by folder with a format badge, Sources with letter badges). **File paths the agent writes in replies are clickable** and open the file preview. Full light/dark theming throughout.

Deliberate negative space: the **assistant pill is a read-only "team" roster, not a switcher** (the design's "4 assistants" are really our 1 agent + 3 `task`-tool subagents); the **composer has no paperclip** (hidden — uploads live in Workspace → Files); the **Next.js dev indicator is hidden**; **temperature / system-instructions were not added**.

# How — the key architectural choices

**Repaint by token *value*, via a brand-accent namespace that feeds both token layers.** The whole palette swap is almost entirely a CSS-variable value change in `globals.css` — component classes barely changed, so the app re-tinted in one place. The app runs two parallel token layers (the `--color-*`/`--bg-*` set consumed by `tailwind.config.mjs` extends, and the Radix HSL set `--primary`/`--background`/… consumed by `components/ui/*`). A new `--brand-accent*` namespace holds the selectable accent and feeds **both** `--color-primary` and the Radix `--primary`/`--ring` (HSL), so brand color flows everywhere from one source — without reusing Radix's `--accent`, which is a *muted hover surface*, not the brand.

**Theme and accent are two orthogonal mechanisms.** Light/dark moved off `@media (prefers-color-scheme)` to a `.dark` class driven by `next-themes` (explicit toggle, `system` default). Accent is separate: an `AccentProvider` sets a `data-accent` attribute (emerald default) persisted to `localStorage`, with the per-accent token blocks (`:root[data-accent="…"]` / `.dark[data-accent="…"]`) in `globals.css`. An inline pre-paint `<script>` in `layout.tsx` restores the saved accent before first paint to avoid FOUC.

**The tool timeline rail is markup-only over frozen streaming logic.** `ToolCallBox`/`SubagentCard` were restyled into a rail (status node + hairline connector, reusing `PlanSection`'s `before:` idiom) without touching their `React.memo`, the `processedMessages` caches, the pending-guarded `parseToolError`/`previewValue`, or the pointer/click dedupe from [PRD 05](05_chat_streaming_throttle.md).

# Files of interest

| Concern | Path |
|---|---|
| Palette + dark `.dark` block + 4 accent blocks + tokens | `frontend/src/app/globals.css` |
| `darkMode: ["class"]`, fonts, `brand-accent`/surface token utilities | `frontend/tailwind.config.mjs` |
| 3 fonts (next/font), provider wrap, accent pre-paint script | `frontend/src/app/layout.tsx` |
| Theme + accent providers | `frontend/src/providers/ThemeProvider.tsx`, `AccentProvider.tsx` |
| 2-panel layout, threads pin/dock, drawer-close effect | `frontend/src/app/page.tsx` (`handleThreadSelect`, `threadsPinned`) |
| Top bar, brand logo, read-only roster pill | `frontend/src/app/components/TopBar.tsx`, `LogoMark.tsx` |
| Hero, composer (circular send, gated attach), `COMPOSER_ATTACH_ENABLED` | `frontend/src/app/components/ChatInterface.tsx` |
| Avatar once-per-turn, bubbles, rail container | `frontend/src/app/components/ChatMessage.tsx` (`showAvatar`) |
| Tool rail row + status node + icon map | `frontend/src/app/components/ToolCallBox.tsx` (`TOOL_ICON_MAP`, `statusNode`) |
| Threads slide-over + pin | `frontend/src/app/components/ThreadsDrawer.tsx`, `ThreadList.tsx` |
| Settings modal + appearance section | `frontend/src/app/components/ConfigDialog.tsx`, `AppearanceSettings.tsx` |
| Workspace cards (chip/progress/file color/sources) | `frontend/src/app/components/workspace/*`, `TasksFilesSidebar.tsx` (`FileCard`) |
| Clickable file paths | `frontend/src/providers/FilePreviewProvider.tsx`, `frontend/src/app/utils/filePaths.ts`, `MarkdownContent.tsx` |
| Hide dev indicator | `frontend/next.config.ts` (`devIndicators: false`) |
| Design-system spec + pointer | `frontend/DESIGN.md`, `frontend/CLAUDE.md` |

# Decisions worth remembering

- **`bg-primary`/`text-primary` are the *paper* tokens, not the brand.** In `tailwind.config.mjs` the `backgroundColor`/`textColor` namespaces map `primary` → `--bg-primary`/`--text-primary` (canvas/ink), so `bg-primary` renders near-white. Brand-colored elements must use `bg-brand-accent`/`text-brand-accent`/`text-on-accent` or the new Button `variant="primary"`. This bit twice (the washed-out progress bar and workspace chips both used `bg-primary`). Radix `colors.primary` (= `hsl(var(--primary))`) *is* the brand and drives focus rings.
- **Emerald is the default accent, not the design's indigo (user call).** The green matches the rocket logo and the README diagram palette; indigo was demoted to a selectable option. Implemented by making `:root`/`.dark` default to emerald and adding explicit `[data-accent="indigo"]` blocks — so SSR defaults to emerald with no flash.
- **Assistant avatar renders once per turn, pinned to the top.** First cut stamped the logo on every assistant message and let it drift mid-stream. Best practice (claude.ai/ChatGPT/Gemini) is identity once per turn; we thread the existing `showAvatar` flag to `ChatMessage` and pin it with `items-start` so it stays put while text streams.
- **Threads drawer-select used two nuqs setters and dropped `threadId`.** Calling `setThreadId(id)` then `setSidebar(null)` in one handler made the second setter rebuild the query from a render-time snapshot and clobber the first — the drawer closed but no thread loaded. Fix: the handler only sets `threadId`; a `useEffect` closes the drawer *after* it commits. (Pinned dock avoids this entirely since it never closes on select.)
- **Clickable file paths are validated against the real file set.** A remark plugin (`remarkFilePaths`) wraps path-like text, but `MarkdownContent` only renders a link when `FilePreviewProvider.resolveFile` matches an existing file — so a hallucinated/misspelled path (`/procesed/x.md`) stays plain text. Two non-obvious fixes: react-markdown's default `urlTransform` sanitized our `nextrole-file:` sentinel scheme to an empty href (pass a custom `urlTransform`), and opening the Radix preview modal synchronously from the triggering click self-dismissed it (defer with `setTimeout(0)`).
- **Files are colored by folder category, not file type (user reverted a type-based cut).** Keeps the [PRD 09](09_file_category_colors.md) grouping (`getFileCategory` → `iconVar`) so a folder's artifacts share a hue; the new format badge (PDF/JSON/YAML/MD) carries the type. The palette migrated with the theme; `tailored_resume` now follows the accent.
- **Composer attach is gated, not deleted.** `COMPOSER_ATTACH_ENABLED = false` hides the paperclip while keeping the upload code wired (flip to re-show); the redundant capability lives in Workspace → Files. The Next.js "N" indicator is hidden via `devIndicators: false` (errors still report) rather than an env toggle, because the frontend container only receives the explicit `NEXT_PUBLIC_*` vars.
- **`DESIGN.md` lives in `frontend/`, referenced (not `@import`-ed) from `frontend/CLAUDE.md`.** Co-located with the code it documents (matching the per-app CLAUDE.md convention); a pointer instead of a CLAUDE.md `@import` keeps the ~600-line spec out of every session's context until design work actually needs it.

# Deferred (intentional non-goals for v1)

- **Temperature + system-instruction controls.** In the design's Settings but not backend-wired (`middleware.py` only reads `configurable.main_agent_model`/`subagent_model`). Adding them means agent-runtime work; revisit when that's in scope.
- **A real assistant/agent switcher.** The deployment exposes one selectable assistant; the roster pill stays read-only until multiple graphs/assistants exist.
- **Env-var toggle for the dev indicator / re-showing the composer attach.** Both are one-line flips today; wire an env path only if toggling becomes frequent.
- **Motion tokens.** Streaming caret, tool sweep, progress sheen, and drawer slide are encoded ad hoc, not formalized.

# How to verify end-to-end

1. `docker compose up -d`; open the frontend host port from `docker ps`. (After pulling, restart the frontend once — `next-themes` was added and `next.config.ts` changed, both read at boot.)
2. Empty state shows the serif hero + chips; a chip fills the composer; there is **no paperclip** and **no "N" dev indicator** bottom-left.
3. Open Settings → toggle Light/Dark/System and click each accent swatch; the whole app re-tints live and **persists across reload with no flash**. Default accent is emerald.
4. Open a thread with tool activity: the tool rail shows status nodes + mono names; `write_todos`/`overwrite_file` use the write icon, `read_file` a book, `execute` a terminal; expanding a row shows the mono detail. The assistant logo avatar appears once at the top of the turn.
5. Open the threads drawer, **select a thread → it loads and the drawer closes**. Re-open, click **pin** → it docks as a persistent column and **stays open** when switching threads.
6. Ask the agent to list the files it created; the paths render as emerald links — clicking one opens the file preview. A made-up path renders as plain text.
7. Send a fresh message and watch it stream (working indicator → Stop button → avatar + streamed reply); confirm via React DevTools that only the streaming row re-renders (streaming perf intact).
8. Run `pnpm --dir frontend build` + `type-check` + `lint` — all clean.
