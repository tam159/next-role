---
type: PRD
title: "Color-coded Workspace File Cards"
description: "Tint each Workspace file card's icon by its root folder so users can spot the right artifact at a glance."
tags: [frontend, ui, files]
timestamp: '2026-05-23T11:57:56+07:00'
status: "shipped"
scope: "Workspace > Files panel"
version: v1
---

# Why

The Workspace > Files panel renders every agent-produced file as a visually identical white card in an auto-fill grid. As the agent produces output across `/tailored_resume/`, `/interview_battlecard/`, `/research/`, `/processed/`, `/interview_coach/`, and `/upload/`, users end up scanning ~10 uniform tiles to find the artifact they want. The full file path is already in the card label, but reading 10 long paths to spot the one battlecard for a given role is slow. A subtle visual cue per category would let users land on the right file in one glance — without breaking the cream/teal calm of the existing theme.

# What the user sees

Each file card's icon is tinted by its root folder, the prefix path (e.g. `/research/`) is rendered in a muted tertiary text color while the filename stays in normal foreground, and long paths truncate with an ellipsis. The card itself, padding, hover, and delete control are unchanged. The full path remains on hover via the existing `title` tooltip.

Six categories get a distinct icon color; system folders (`/memory/`, `/workspace/`, anything unmapped) keep the existing brand-teal icon — no regression.

| Category | Token | Hue |
|---|---|---|
| `tailored_resume` | `--color-primary` | teal (brand) |
| `interview_battlecard` | `--color-warning` | amber |
| `interview_coach` | `--color-category-plum` | muted plum |
| `research` | `--color-category-slate` | slate blue |
| `processed` | `--color-success` | green |
| `upload` | `--color-category-clay` | warm clay |

# How — the key architectural choices

**Mapping is purely a frontend concern, derived from the existing virtual path string.** No backend changes, no schema work, no new API. The 6 colored categories are listed in one module that the Files component consults per render.

Why this shape:

- **CSS variables, not Tailwind config.** Colors are referenced via `style={{ color: "var(--color-...)" }}` inline, matching the existing `--color-file-button` pattern already used in `TasksFilesSidebar.tsx`. Dark mode comes free because every `--color-*` token has a `prefers-color-scheme: dark` override in `globals.css`. Skipping Tailwind theme extension keeps this a one-off palette without bloating the design system.
- **Color the icon, nothing else.** An early design used a 3px left accent bar plus icon tint; user review said the bar was redundant once the icon carried the signal. Final design uses icon tint alone — quieter, no extra DOM, full card padding preserved.
- **Hues chosen for separability at icon size, not brand fidelity.** Initial palette reused `--color-primary`, `--color-secondary`, `--text-brand-tertiary`, and `--color-success` — all teal-family. At small icon sizes they read as "four greens." Final palette keeps the two non-teal semantic tokens (warning amber, success green) and adds three new tokens (plum, slate, clay) at low saturation. Six hues, each ≥40° apart on the wheel.

# Files of interest

| Concern | Path |
|---|---|
| Category mapping + helpers | `frontend/src/app/lib/fileCategories.ts` |
| New palette tokens (light + dark) | `frontend/src/app/globals.css` (`--color-category-plum`, `--color-category-slate`, `--color-category-clay`) |
| File card render | `frontend/src/app/components/TasksFilesSidebar.tsx` (`FilesPopover`) |

# Decisions worth remembering

- **Icon-only color, no badges, no group headers, no tinted backgrounds.** User explicitly chose the quietest option. Future requests to "make categories more prominent" should start by reconsidering whether the calm theme is still the right goal — not by stacking treatments on top of this one.
- **Three new CSS tokens, not reassignment of `--color-error`.** Reds remain reserved for the destructive delete action so the category palette never collides with destructive UI semantics.
- **Filename rendering ended up as one inline `<span class="truncate">`, not flex-split parts.** Three intermediate designs tried to keep the file extension always visible (flex with `shrink-0` extension, `-ml-px` negative margin to close ellipsis gaps, JS character-budget truncation that built `prefix + stem + "…" + ext` as one string). Each had its own artifact — visible whitespace gaps between `…` and the extension, double-dot rendering (`tam-….md` reading as `tam-....md`), or invisible clipping when a long prefix pushed the JS-truncated tail past the card's `overflow:hidden` edge. The final shape — one truncating span with native CSS ellipsis — is robust and consistent across rows, at the cost of sometimes hiding the extension on very long paths. The `title={filePath}` hover tooltip preserves the full path for users who need it.
- **`text-tertiary` for the prefix.** The token already exists in `globals.css` and is wired up via `tailwind.config.mjs`. No new theme color needed for the dimming effect.
- **`getFileCategory` matches only the first path segment.** Disk-backed sources like `/tailored_resume/<role>/<file>.yaml` produce a long virtual path, but the root segment is what matters for the color decision. Nested folders are reflected in the dimmed prefix text, not the icon color.
- **Iteration order preserved.** The grid still renders files in the order they appear in the `files` record (chronological-ish). No regrouping or sorting by category — that's a bigger UX change worth its own PRD.

# Deferred (intentional non-goals for v1)

- **Pixel-accurate middle-truncation that always preserves the file extension.** Would need canvas `measureText()` plus a `ResizeObserver` on the card. Worth doing only if losing the extension on long paths turns out to actually hurt usage.
- **Group-by-category view with section headers.** Stronger structure, bigger UX change. Possible follow-up if users start producing >10 files per session.
- **Drag-to-reorder, filter chips, search.** Out of scope.
- **Per-category icon glyphs** (e.g., briefcase for battlecard, magnifier for research). Color alone is the lighter touch; revisit if users want stronger differentiation.
- **Light/dark accessibility audit beyond informal contrast checks.** All six tokens were eyeballed against both surface colors but not run through a WCAG checker.

# How to verify end-to-end

1. `docker compose up -d` and open the frontend.
2. Trigger the agent to produce files across at least four of the six categories — e.g. ask for a tailored resume, a battlecard, a research note, and process an upload. Confirm each card's icon matches the palette table above.
3. Drop a file into a system folder (e.g. `/memory/`) and confirm its card looks identical to the pre-change baseline — brand-teal icon, no tint.
4. Hover any card → `title` tooltip shows the full virtual path.
5. Resize the panel narrow enough to truncate. Filename ends in `…`; prefix `/research/` is still readable as the dimmer leading text.
6. Toggle OS-level dark mode and confirm all six hues remain readable on the dark surface — no low-contrast issues, no token still resolving to its light value.
