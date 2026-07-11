---
version: "1.0"
name: NextRole-design-system
description: 'A warm "paper + espresso" editorial interface for NextRole, a GenAI career copilot. The system anchors on a warm paper canvas with an editorial serif (Newsreader) for hero/display moments, a humanist grotesk (Schibsted Grotesk) for all UI/chat, and JetBrains Mono for file paths, tool names, and code. The signature voltage is a logo-matched emerald accent (user-selectable; also indigo/blue/coral) over warm neutrals — deliberately warm and humanist where most AI tools use cool blue/slate. The layout is a focused two-panel workspace: a conversation panel (chat + a tool-activity timeline rail) beside a Workspace panel (Plan, Files, Sources). Full light + dark theming. The brand mark is a rocket-over-rising-"N".'
colors:
  # Brand accent (emerald default; user-selectable per data-accent)
  accent: "#0e9f6e"
  accent-hover: "#0b855c"
  accent-soft: "#e3f5ec"
  accent-text: "#0b7e58"
  on-accent: "#ffffff"
  accent-indigo: "#5b5bd6"
  accent-blue: "#2563eb"
  accent-coral: "#e0623c"
  # Paper neutrals (light, default)
  bg: "#f3f0e9"
  surface: "#fbfaf5"
  surface-2: "#ffffff"
  surface-3: "#f4f1ea"
  text: "#211f1a"
  text-2: "#6e6a60"
  text-3: "#9c968b"
  border: "#e7e1d4"
  border-2: "#d8d1c2"
  # Espresso neutrals (dark)
  dark-bg: "#191611"
  dark-surface: "#211d17"
  dark-surface-2: "#272219"
  dark-elevated: "#2a261f"
  dark-surface-3: "#2f2a20"
  dark-text: "#f1ede4"
  dark-text-2: "#aba496"
  dark-text-3: "#7d766a"
  dark-border: "#332e25"
  dark-border-2: "#403a2f"
  dark-accent: "#3fcf95"
  on-accent-dark: "#1a1713"
  # Semantic
  success: "#3f9d6b"
  success-soft: "#e8f3ec"
  warm: "#d9785a"
  warning: "#c47a16"
  error: "#d9534f"
  scrim: "rgba(40,34,24,0.34)"
  # File-category hues (color files by folder)
  cat-tailored-resume: "{colors.accent}"
  cat-interview-battlecard: "#c47a16"
  cat-interview-coach: "#8a5a9e"
  cat-research: "#4a6b8a"
  cat-processed: "#5a8a4a"
  cat-upload: "#b56a7a"
typography:
  hero:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: 40px
    fontWeight: 500
    lineHeight: 1.08
    letterSpacing: -0.01em
    note: "Editorial serif. Emphasis word set in italic {colors.accent-text}."
  preview-title:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: 26px
    fontWeight: 500
    lineHeight: 1.2
  wordmark:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: 17px
    fontWeight: 700
    letterSpacing: -0.02em
  title-lg:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: 17px
    fontWeight: 700
    letterSpacing: -0.01em
  title:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 600
  body:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.6
  body-sm:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
  button:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: 13.5px
    fontWeight: 600
  eyebrow:
    fontFamily: "Schibsted Grotesk, system-ui, sans-serif"
    fontSize: 11px
    fontWeight: 700
    letterSpacing: 0.08em
    textTransform: uppercase
    color: "{colors.text-3}"
  tool-name:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: 12.5px
    fontWeight: 500
  path:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: 11px
    fontWeight: 400
    color: "{colors.text-3}"
  code:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
rounded:
  squircle: 9px
  chip: 11px
  button: 10px
  file-card: 12px
  card: 16px
  composer: 18px
  modal: 18px
  pill: 9999px
  full: 9999px
  user-bubble: "16px 16px 5px 16px"
spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  panel: 18px
  message-gap: 28px
shadow:
  sm: "0 1px 2px rgba(60,50,30,0.05)"
  lg: "0 16px 48px -18px rgba(60,50,30,0.30)"
  xl: "0 24px 64px -20px rgba(60,50,30,0.34)"
  sm-dark: "0 1px 2px rgba(0,0,0,0.40)"
  lg-dark: "0 18px 56px -18px rgba(0,0,0,0.62)"
components:
  top-bar:
    backgroundColor: "{colors.surface}"
    height: 60px
    borderBottom: "1px {colors.border}"
  icon-button:
    size: 38px
    rounded: "{rounded.button}"
    textColor: "{colors.text-2}"
    hoverBackground: "{colors.surface-3}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    activeBackground: "{colors.accent-hover}"
    typography: "{typography.button}"
    rounded: "{rounded.button}"
    height: 38px
    shadow: "{shadow.sm}"
  button-outline:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    border: "1px {colors.border}"
    hoverBackground: "{colors.surface-3}"
    rounded: "{rounded.button}"
  button-ghost:
    backgroundColor: transparent
    textColor: "{colors.text-2}"
    hoverBackground: "{colors.surface-3}"
    rounded: "{rounded.button}"
  roster-pill:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    border: "1px {colors.border}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.button}"
    height: 38px
    note: "Accent dot + active agent name + chevron → read-only team roster popover."
  hero:
    backgroundColor: "{colors.bg}"
    typography: "{typography.hero}"
  suggestion-chip:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    border: "1px {colors.border}"
    hoverBorder: "{colors.accent}"
    typography: "{typography.button}"
    rounded: "{rounded.chip}"
    shadow: "{shadow.sm}"
  user-bubble:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.text}"
    border: "1px {colors.border}"
    typography: "{typography.body}"
    rounded: "{rounded.user-bubble}"
    maxWidth: "78%"
  assistant-turn:
    note: "28px logo-mark avatar (once per turn, top-aligned) + content column."
    typography: "{typography.body}"
  tool-rail-row:
    note: "Status node (running ring / done check / error) on a vertical rail + kind icon + mono tool name + summary + chevron; expands to a mono detail panel."
    typography: "{typography.tool-name}"
    nodeSize: 26px
    railColor: "{colors.border-2}"
    expandedBackground: "{colors.surface-2}"
    detailBackground: "{colors.surface-3}"
  tool-call-group:
    note: "A consecutive run of main-agent tool calls (across AI messages; broken by prose, a subagent, or a user message) behind a rail-geometry summary row ('N tool calls' + deduped names + failed pill). Live while running — the tip group stays open between batches — then auto-collapses; manual toggle wins; approval pins open. Expanded body clusters per-message batches; simultaneous ones open with an 'N in parallel' micro-label and close with a border-2 hairline break."
    nodeSize: 26px
    railColor: "{colors.border-2}"
  subagent-card:
    backgroundColor: "{colors.surface-2}"
    border: "1px {colors.border}"
    rounded: "{rounded.card}"
    shadow: "{shadow.xs}"
    note: "One card per subagent: header (identity-icon chip + name + status badge + 'n tools' pill + duration + chevron) over a border-t INPUT/ACTIVITY/OUTPUT body. Identity icons: hiring-recon=Radar, resume-tailor=Scissors, interview-coach=MessagesSquare, default=Bot. ACTIVITY clusters the subagent's own steps with the same 'N in parallel' labels as tool-call-group. Expanded while running, auto-collapses to the header on completion."
  composer:
    backgroundColor: "{colors.surface-2}"
    border: "1px {colors.border}"
    focusRing: "{colors.accent}"
    rounded: "{rounded.composer}"
    shadow: "{shadow.lg}"
    note: "Auto-growing textarea, paperclip attach, hint, circular send (accent when filled, {colors.border-2} when empty)."
  send-button:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    disabledBackground: "{colors.border-2}"
    rounded: "{rounded.full}"
    size: 36px
  file-path-link:
    textColor: "{colors.accent}"
    typography: "{typography.tool-name}"
    hoverBackground: "{colors.accent-soft}"
    note: "Clickable agent-written path that opens the file preview — only when the path resolves to a real file."
  workspace-card:
    backgroundColor: "{colors.surface-2}"
    border: "1px {colors.border}"
    rounded: "{rounded.card}"
    shadow: "{shadow.sm}"
    note: "Collapsible. Header = accent-soft icon chip + title + count pill + chevron."
  card-icon-chip:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    size: 36px
    rounded: "{rounded.squircle}"
  progress-bar:
    trackColor: "{colors.surface-3}"
    fillColor: "{colors.accent}"
    height: 10px
    rounded: "{rounded.pill}"
    note: "Animated sheen while work is active."
  file-card:
    backgroundColor: "{colors.surface-2}"
    border: "1px {colors.border}"
    hoverBorder: "{colors.accent}"
    rounded: "{rounded.file-card}"
    shadow: "{shadow.sm}"
    note: "Type squircle + colored category badge (color by folder) + name + mono dir path. Hover lifts."
  source-row:
    backgroundColor: transparent
    hoverBackground: "{colors.surface-3}"
    rounded: "{rounded.button}"
    note: "Square letter badge + title + domain + external-link icon."
  threads-panel:
    backgroundColor: "{colors.surface}"
    width: 320px
    border: "1px {colors.border} right hairline"
    note: "Collapsible docked column below the top bar; width animates 0↔320px over 200ms. Pin keeps it open across thread selection and restores it on load. Below 1024px it overlays the content row with {shadow.lg} + {colors.scrim}."
  thread-card:
    backgroundColor: transparent
    activeBackground: "{colors.accent-soft}"
    activeBorder: "{colors.accent}"
    rounded: "{rounded.file-card}"
  modal:
    backgroundColor: "{colors.surface-2}"
    rounded: "{rounded.modal}"
    shadow: "{shadow.lg}"
    scrim: "{colors.scrim}"
  theme-segmented:
    trackBackground: "{colors.surface-3}"
    activeBackground: "{colors.surface-2}"
    rounded: "{rounded.pill}"
    note: "Light / Dark / System."
  accent-swatch:
    size: 32px
    rounded: "{rounded.squircle}"
    selectedRing: "{colors.accent}"
  text-input:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    border: "1px {colors.border}"
    focusRing: "{colors.accent}"
    rounded: "{rounded.button}"
  badge-type:
    typography: "{typography.eyebrow}"
    rounded: 6px
    note: "Format label (PDF/JSON/YAML/MD) tinted by file-category hue."
---

## Overview

NextRole is a **warm, editorial** interface for a GenAI career copilot. The base atmosphere is a **warm paper canvas** (`{colors.bg}` — #f3f0e9) — deliberately not the cool gray-white most AI tools use. Display moments run an **editorial serif** (Newsreader) — the empty-state hero ("Land your _next role_, faster.") and document-preview titles — while everything else (chrome, chat, labels) uses a **humanist grotesk** (Schibsted Grotesk). **JetBrains Mono** carries file paths, tool names, and code. The pairing reads like a considered product, not a SaaS template.

Brand voltage comes from a **logo-matched emerald accent** (`{colors.accent}` — #0e9f6e), the default of a **user-selectable accent** system (also indigo, blue, coral). The accent is warm-leaning green, echoing the rocket-over-rising-"N" brand mark, and a deliberate counter-position to the cool blues of most AI products. The accent drives primary CTAs, the brand logo tint, focus rings, the progress bar, links, and the active-thread highlight.

The product is organized as a **focused two-panel workspace**:

1. **Conversation panel** (`{colors.bg}`) — chat with the agent plus a vertical **tool-activity timeline rail** that shows each tool/subagent step.
2. **Workspace panel** (`{colors.surface}`) — three collapsible cards: **Plan** (todos + progress), **Files** (generated artifacts), **Sources** (web research).

Threads live in a **collapsible docked panel** below the top bar, toggled from the top-bar threads button; **pinning** keeps it open across thread switches and restores it on load. Everything is fully themed for **light (paper) and dark (espresso)**.

**Key Characteristics:**

- Warm paper canvas (`{colors.bg}`) with warm-ink text (`{colors.text}` — #211f1a). The brand's defining choice over pure white.
- Logo-matched emerald accent (`{colors.accent}`), user-selectable; the whole app re-tints from one CSS variable set.
- Editorial serif (Newsreader 500) for the hero + preview titles only; humanist grotesk (Schibsted Grotesk) for everything else; JetBrains Mono for paths/tools/code.
- A vertical **tool-activity timeline rail** — status nodes (spinner / check / hollow) connected by a hairline, with mono tool names and expandable detail. The product shows its agentic work, not abstract chrome.
- File artifacts colored **by folder category** (`{colors.cat-*}`), with a format badge (PDF/JSON/YAML/MD) — related artifacts share a hue.
- Three-surface depth: canvas (`{colors.bg}`) → panel (`{colors.surface}`) → card/bubble/modal (`{colors.surface-2}`), plus inset fills (`{colors.surface-3}`).
- Radius is hierarchical: `{rounded.squircle}` (9px) icon chips, `{rounded.button}`–`{rounded.chip}` (10–11px) buttons/chips, `{rounded.file-card}` (12px) file cards, `{rounded.card}` (16px) cards, `{rounded.composer}` (18px) composer/modals, `{rounded.pill}` pills.
- Color-block-first depth: soft shadows (`{shadow.sm}` on cards, `{shadow.lg}` on overlays); most separation comes from surface contrast and hairline borders.

## Colors

### Brand & Accent

The accent is **user-selectable** and persisted (`data-accent` on the root); **emerald is the default**. All four ship a solid / hover / soft / text quartet so any accent re-tints CTAs, links, focus rings, the progress bar, and the logo. `--on-accent` is white on light, near-black (`#1a1713`) on dark.

- **Emerald (default)** (`{colors.accent}` — #0e9f6e): solid; hover `{colors.accent-hover}` (#0b855c), soft `{colors.accent-soft}` (#e3f5ec) for tints/bubbles/chips, text `{colors.accent-text}` (#0b7e58) for accent-on-paper text. Dark variant `{colors.dark-accent}` (#3fcf95).
- **Indigo** (`{colors.accent-indigo}` — #5b5bd6), **Blue** (`{colors.accent-blue}` — #2563eb), **Coral** (`{colors.accent-coral}` — #e0623c): alternate accents, each with their own soft/hover/text + dark variants.

### Surface (Light — paper)

- **Canvas** (`{colors.bg}` — #f3f0e9): the conversation floor and deepest app background.
- **Surface** (`{colors.surface}` — #fbfaf5): raised panels — top bar, workspace, threads panel.
- **Surface-2** (`{colors.surface-2}` — #ffffff): cards, chat bubbles, composer, modals, file cards.
- **Surface-3** (`{colors.surface-3}` — #f4f1ea): insets, hover fills, segmented-control track, code/detail panels.

### Surface (Dark — espresso)

- **Canvas** (`{colors.dark-bg}` — #191611) · **Surface** (`{colors.dark-surface}` — #211d17) · **Surface-2** (`{colors.dark-surface-2}` — #272219) · **Elevated/popover** (`{colors.dark-elevated}` — #2a261f) · **Surface-3** (`{colors.dark-surface-3}` — #2f2a20). The same surface ladder, warm and dark — never cool charcoal.

### Text

- **Text** (`{colors.text}` — #211f1a / dark #f1ede4): headings + primary text.
- **Text-2** (`{colors.text-2}` — #6e6a60 / dark #aba496): secondary text, summaries.
- **Text-3** (`{colors.text-3}` — #9c968b / dark #7d766a): tertiary, placeholders, mono paths, eyebrow labels.

### Borders

- **Border** (`{colors.border}` — #e7e1d4 / dark #332e25): hairline borders.
- **Border-2** (`{colors.border-2}` — #d8d1c2 / dark #403a2f): stronger borders, hollow status nodes, the timeline rail line, the empty/disabled send button.

### Semantic

- **Success** (`{colors.success}` — #3f9d6b): completed checks; **Success-soft** (`{colors.success-soft}` — #e8f3ec) for the done-node background.
- **Warm** (`{colors.warm}` — #d9785a): PDF / decorative warm accent.
- **Warning** (`{colors.warning}` — #c47a16): in-progress / needs-review states.
- **Error** (`{colors.error}` — #d9534f): destructive actions, error tool states.
- **Scrim** (`{colors.scrim}` — rgba(40,34,24,.34) / dark rgba(0,0,0,.55)): modal backdrop; threads-panel backdrop on small screens.

### File-Category Hues

Files are colored **by their top-level folder**, so related artifacts share a hue (the format is shown on the badge text):

- `tailored_resume` → `{colors.cat-tailored-resume}` (the accent) · `interview_battlecard` → `{colors.cat-interview-battlecard}` (#c47a16) · `interview_coach` → `{colors.cat-interview-coach}` (#8a5a9e) · `research` → `{colors.cat-research}` (#4a6b8a) · `processed` → `{colors.cat-processed}` (#5a8a4a) · `upload` → `{colors.cat-upload}` (#b56a7a).

## Typography

### Font Families

- **Newsreader** (serif, weights 400/500 + italic) — editorial display only: the hero headline and document-preview titles. Loaded via `next/font` as `--font-serif`.
- **Schibsted Grotesk** (humanist sans, 400/500/600/700) — all chrome, labels, buttons, and chat text. `--font-sans`; the default body face.
- **JetBrains Mono** (mono, 400/500) — file paths, tool names, attachment chips, code/JSON/YAML. `--font-mono`.

The serif is scoped tightly — never applied to markdown headings or card titles (those stay grotesk), so the serif stays a deliberate editorial flourish.

### Hierarchy

| Token                        | Family            | Size   | Weight    | Tracking      | Use                                                                                             |
| ---------------------------- | ----------------- | ------ | --------- | ------------- | ----------------------------------------------------------------------------------------------- |
| `{typography.hero}`          | Newsreader        | 40px   | 500       | -0.01em       | Empty-state hero ("Land your _next role_, faster.") — emphasis italic in `{colors.accent-text}` |
| `{typography.preview-title}` | Newsreader        | 26px   | 500       | —             | File-preview document title                                                                     |
| `{typography.wordmark}`      | Schibsted Grotesk | 17px   | 700       | -0.02em       | "NextRole" wordmark                                                                             |
| `{typography.title-lg}`      | Schibsted Grotesk | 17px   | 700       | -0.01em       | Workspace + section headers                                                                     |
| `{typography.title}`         | Schibsted Grotesk | 15px   | 600       | —             | Card titles, thread titles                                                                      |
| `{typography.body}`          | Schibsted Grotesk | 15px   | 400 / 1.6 | —             | Chat + body text                                                                                |
| `{typography.body-sm}`       | Schibsted Grotesk | 13px   | 400 / 1.5 | —             | Secondary text, subs, hints                                                                     |
| `{typography.button}`        | Schibsted Grotesk | 13.5px | 600       | —             | Buttons, pills, chips                                                                           |
| `{typography.eyebrow}`       | Schibsted Grotesk | 11px   | 700       | 0.08em, UPPER | Section eyebrows ("APPEARANCE", "YOUR PREP TEAM") in `{colors.text-3}`                          |
| `{typography.tool-name}`     | JetBrains Mono    | 12.5px | 500       | —             | Tool names in the rail, file links                                                              |
| `{typography.path}`          | JetBrains Mono    | 11px   | 400       | —             | File dir paths in `{colors.text-3}`                                                             |
| `{typography.code}`          | JetBrains Mono    | 14px   | 400 / 1.6 | —             | Code/JSON/YAML blocks                                                                           |

### Principles

The serif hero is the brand's literary signal — set at weight 500 with the emphasis word italic in the accent. Body and chat stay grotesk 400 at 1.6 line-height for readability. Mono is reserved for machine artifacts (paths, tools, code) — never for prose. Eyebrow labels (uppercase, tracked, tertiary) section the overlays.

## Layout

### Spacing System

- **Base unit:** 4px. Tokens: `{spacing.xxs}` 4 · `{spacing.xs}` 8 · `{spacing.sm}` 12 · `{spacing.md}` 16 · `{spacing.lg}` 24 · `{spacing.xl}` 32.
- **Panel padding:** `{spacing.panel}` (~18px). **Card padding:** ~16px (px-4 py-3.5). **Message vertical gap:** `{spacing.message-gap}` (~28px).
- **Conversation column:** centered, `max-width: 760px`.

### Grid & Containers

- **App shell:** full-height column — `{component.top-bar}` (60px) over a horizontal split.
- **Main split:** resizable Conversation (~46%) + Workspace (~54%), via `react-resizable-panels`. When the threads panel is open, a 320px (`--sidebar-width`) docked column precedes the split; it collapses to 0 with a 200ms width animation.
- **Workspace cards:** stacked, `flex-shrink: 0` so each keeps natural height and the panel scrolls.
- **File grid:** auto-fill 2-up of `{component.file-card}` (min ~220px).

### Whitespace Philosophy

Generous internal padding + the warm canvas give the conversation an unhurried, editorial pace. The tool rail keeps dense agent activity legible by indenting each step under one turn-avatar with a hairline connector.

## Elevation & Depth

| Level    | Treatment                                                        | Use                                                   |
| -------- | ---------------------------------------------------------------- | ----------------------------------------------------- |
| Flat     | No shadow, no border                                             | Conversation floor, panels                            |
| Hairline | 1px `{colors.border}`                                            | Cards, inputs, dividers                               |
| Card     | `{colors.surface-2}` + `{shadow.sm}`                             | Workspace cards, file cards, bubbles                  |
| Overlay  | `{colors.surface-2/.surface}` + `{shadow.lg}` + `{colors.scrim}` | Modals, roster popover, threads panel (small screens) |
| Running  | `tool-running-sweep` / `progress-active` sheen                   | Active tool node + progress bar                       |

Depth is **color-block first** — surface contrast (canvas → surface → surface-2) and hairlines carry most separation; shadows are soft and warm (`rgba(60,50,30,…)` on light, black on dark). The brand mark adds an accent **glow** behind the hero logo only.

## Shapes

### Border Radius Scale

| Token                   | Value      | Use                                                  |
| ----------------------- | ---------- | ---------------------------------------------------- |
| `{rounded.squircle}`    | 9px        | Icon chips, accent swatches, status/type squircles   |
| `{rounded.button}`      | 10px       | Icon buttons, primary/outline buttons                |
| `{rounded.chip}`        | 11px       | Suggestion chips                                     |
| `{rounded.file-card}`   | 12px       | File cards, thread cards                             |
| `{rounded.card}`        | 16px       | Workspace cards                                      |
| `{rounded.composer}`    | 18px       | Composer, modals                                     |
| `{rounded.pill}`        | 9999px     | Pills, segmented control, status dots, circular send |
| `{rounded.user-bubble}` | 16 16 5 16 | User chat bubble (notched bottom-right)              |

### Iconography & Mark

- **Icons:** Lucide, 1.7–1.8 stroke, `currentColor` — sized 14–19px.
- **Brand mark:** the rocket-over-rising-"N" logo (`/public/next-role-logo.png`), used in the top bar, the hero (with an accent glow), and as the assistant avatar (28px, once per turn).
- **Tool status nodes:** 26px circles — running = spinning accent ring on `{colors.accent-soft}`; done = check on `{colors.success-soft}`; error = alert on error-tint; the timeline rail connects them in `{colors.border-2}`.
- **Disclosure chevrons:** one convention everywhere (tool rows, tool-call groups, subagent cards, workspace cards, sidebar sections, argument keys): a `ChevronDown` that points **right when collapsed** (`-rotate-90`) and **down when expanded**, animated with `transition-transform duration-200`. Never down→up.

## Components

### Top Bar

**`top-bar`** — 60px, `{colors.surface}`, bottom hairline. Left: threads toggle (`{component.icon-button}`), brand mark + `{typography.wordmark}`, a vertical divider, the active thread title (`{typography.title}`) over a sub (`{typography.body-sm}` in `{colors.text-3}`). Right: `{component.roster-pill}`, theme toggle, settings, and a `{component.button-primary}` "New thread".

**`icon-button`** — 38px, `{rounded.button}`, `{colors.text-2}`, hover fills `{colors.surface-3}`. Threads, theme (sun/moon), settings.

**`roster-pill`** — accent dot + active agent name + chevron → a **read-only "Your prep team" popover** listing the Career Agent and the specialist subagents it delegates to (not a switcher — it surfaces the multi-agent architecture honestly).

### Buttons

**`button-primary`** — the accent CTA. `{colors.accent}` bg, `{colors.on-accent}` text, `{shadow.sm}`, `{rounded.button}`, 38px; active → `{colors.accent-hover}`. Re-tints with the selected accent.
**`button-outline`** — `{colors.surface-2}` + 1px `{colors.border}`, hover `{colors.surface-3}`.
**`button-ghost`** — transparent, hover `{colors.surface-3}`; for icon/secondary actions.

### Conversation

**`hero`** — centered empty state: glowing brand mark, `{typography.hero}` headline with the accent italic emphasis, a `{colors.text-2}` subtitle, then (first-run only) `{component.upload-dropzone}`, and a row of `{component.suggestion-chip}` that fill the composer.

**`upload-dropzone`** — the first-run featured action: a dashed-hairline `{rounded.card}`-scale card (`{colors.surface-2}` at 70%) with a `{component.card-icon-chip}`, a bold one-line ask ("Add your resume or a job description") and a `{colors.text-3}` sub-line; click opens the file picker, drag-over/drop uploads in place (accent border + `{colors.accent-soft}` wash while dragging). Renders only while the user has **zero uploads** (`filesReady && !hasUploads` from `useUploadCue`) — it retires permanently on the first upload, never via a dismiss control.

**`suggestion-chip`** — icon + label, `{colors.surface-2}` + hairline, hover lifts + accent border. Order: Research → Tailor → Prepare for an interview → Build a battlecard.

**`user-bubble`** — right-aligned, `{colors.accent-soft}` bg, 1px border, `{rounded.user-bubble}`, max-width 78%; optional mono attachment chips.

**`assistant-turn`** — a 28px brand-mark avatar (rendered **once per turn**, pinned to the top via `items-start`) + a content column carrying the thinking indicator, the tool rail, and the streamed reply.

**`tool-rail-row`** — the signature element. A `[node | content]` row on a vertical rail (`{colors.border-2}`). The node shows status (spinner / `{colors.success-soft}` check / hollow `{colors.border-2}` / error). Content = kind icon + `{typography.tool-name}` + one-line summary + chevron; expanding reveals a `{colors.surface-3}` mono detail panel (Arguments / Result). Subagent steps nest as a branch with their own nested rows. Streaming uses the `tool-running-sweep` sheen.

**`tool-call-group`** — a consecutive run of main-agent tool calls as a single disclosure unit, spanning AI messages until broken by prose, a subagent card, or a user message. Progressive disclosure: expanded with live rows while the run is active (the transcript-tip group holds open across the model's think-pauses between batches), then **auto-collapses the moment the run ends** to a summary row in the same `[node | content]` rail geometry — group status node + "N tool calls" + up to 3 deduped mono names (+K overflow) + a `{colors.error}`-tinted "n failed" pill when applicable + chevron. The expanded body preserves per-message batches: calls issued together are clustered, labeled with an "N in parallel" micro-header, and closed with a light hairline break so the cluster's end stays legible. A manual toggle always wins and disables auto-driving; a pending tool approval pins the group open. A run of one call renders as its plain `{component.tool-rail-row}`. Historical messages mount collapsed with no animation.

**`subagent-card`** — one subagent as a single `{colors.surface-2}` + hairline `{rounded.card}` card (replaces the old separate indicator chip + panel). Always-visible header row: neutral Bot chip + subagent name + status badge (Running / Complete / Failed / Queued) + `{colors.surface-3}` "n tools" count pill + duration (terminal only) + `{component.workspace-card}`-style chevron; `tool-running-sweep` on the header while running. Body below a hairline divider: INPUT / ACTIVITY (nested tool rail) / OUTPUT. Same auto-collapse rules as `{component.tool-call-group}`, keyed on the subagent's own completion. The queued state is the same header without a body, so discovery landing doesn't jump.

**`composer`** — `{colors.surface-2}`, `{rounded.composer}`, `{shadow.lg}`, focus-within ring in the accent. Auto-growing textarea, a paperclip attach (reuses the file-upload path), the "Enter to send · Shift+Enter" hint, and a circular **`send-button`** (`{colors.accent}` when there's text, `{colors.border-2}` when empty; a red Stop while the agent runs).

**`file-path-link`** — agent-written file paths in replies become clickable links (`{colors.accent}`, mono, file icon, hover `{colors.accent-soft}`) that open `{component.modal}` file preview. **Only paths that resolve to a real workspace file are linked** — hallucinated/misspelled paths render as plain text.

### Workspace

**`workspace-card`** — `{colors.surface-2}` + hairline, `{rounded.card}`, `{shadow.sm}`, collapsible. Header = `{component.card-icon-chip}` + title (`{typography.title-lg}`) + count pill + chevron.

**`card-icon-chip`** — 36px `{colors.accent-soft}` square holding an accent icon.

**`progress-bar`** (Plan) — `{colors.surface-3}` track, `{colors.accent}` fill, 10px, `{rounded.pill}`, with a moving sheen while active. Below it, a todo timeline mirrors the tool rail: done = struck-through check, active = spinner, todo = hollow circle.

**`file-card`** — `{colors.surface-2}` + hairline, `{rounded.file-card}`, hover lifts + accent border. Top row: a type squircle + a `{component.badge-type}` (format label, colored by `{colors.cat-*}`). Then the file name (`{typography.title}`) and a mono dir `{typography.path}`.

**`files-empty-state`** — the Files card with zero entries: centered `{component.card-icon-chip}` with an Upload glyph, "No files yet" title, one supporting line, and a `{component.button-primary}` "Upload files" CTA wired to the same picker as the header Upload button (the header action is `{component.button-outline}` — one primary per card).

**`file-add-tile`** — the last item in the file grid, always present while the card has files: a dashed-hairline tile matching `{component.file-card}` footprint, accent Upload glyph, "Upload files" label, mono format hint. It is the persistent upload affordance once the empty states retire — uploads #2+ happen where the files live. Click opens the picker; drag-over swaps the label to "Drop to upload" with accent border + `{colors.accent-soft}` wash (same drop filtering as `{component.upload-dropzone}`); disabled while uploading or the agent streams. **Label rule:** every upload affordance uses the "Upload" verb ("Upload" header action, "Upload files" empty-state CTA and tile) — one action, one name.

**`upload-cue-dot`** — first-run hotspot on the Files header Upload button: an 8px `{colors.accent}` dot ringed in `{colors.surface-2}`, pulsing via `motion-safe:animate-pulse` (static but visible under reduced motion). Shows while `filesReady && !dismissed` — it survives the first upload (a hero-card upload doesn't teach where the button is) and only retires when the user clicks a panel upload trigger (header button, empty-state CTA, add-files tile), persisted as localStorage `nr-upload-cue-dismissed-v2`. The dot is the **only** dismissible guidance piece — never add arrows, coach marks, or tours.

**`source-row`** — square letter badge + title + domain (`{colors.text-3}`) + external-link icon; hover `{colors.surface-3}`.

### Overlays & Panels

**`threads-panel`** — collapsible docked column (320px `--sidebar-width`, `{colors.surface}`, right hairline) below the top bar; the top-bar threads button toggles it (`aria-expanded`), animating width 0↔320px over 200ms. Header "Threads" + status filter + **pin** toggle + close — same controls in both pin states. Pinned = stays open while switching threads and is restored open on the next visit; unpinned = auto-closes on select; closing (toggle or X) always unpins. Stays mounted while collapsed (`inert`) so the interrupt badge keeps updating. Below 1024px it overlays the content row with `{shadow.lg}` + `{colors.scrim}` (scrim click closes). Holds `{component.thread-card}` rows.

**`thread-card`** — status dot + title + role/meta; active = `{colors.accent-soft}` bg + accent border.

**`modal`** — `{colors.surface-2}`, `{rounded.modal}`, `{shadow.lg}`, `{colors.scrim}`. Sticky header + scroll body + sticky footer. Used by Settings and the file-preview dialog (icon chip + path + Edit/Copy/Download/Print + close; body renders md/code/PDF/image).

**`theme-segmented`** — Light / Dark / System pill control (`{colors.surface-3}` track, `{colors.surface-2}` active). **`accent-swatch`** — 32px `{rounded.squircle}` color swatches; the selected one is ringed in the accent with a check.

### Inputs

**`text-input`** — `{colors.surface-2}` + hairline, accent focus ring, `{rounded.button}`. Used in Settings (model `provider:model` overrides, connection config) and the file-name field.

## Do's and Don'ts

### Do

- Anchor the conversation on the warm canvas (`{colors.bg}`); raise panels to `{colors.surface}` and cards/bubbles/modals to `{colors.surface-2}`.
- Drive all brand color from the selected accent. Use the dedicated accent utilities (`bg-brand-accent`, `text-brand-accent`, `text-on-accent`, the Button `primary` variant) — not Tailwind `bg-primary` (that maps to the paper canvas token).
- Reserve Newsreader serif for the hero and preview titles only; keep all chrome and chat in Schibsted Grotesk; keep paths/tools/code in JetBrains Mono.
- Render the assistant avatar once per turn, pinned to the top; nest tool steps inline beneath it on the rail.
- Color files by **folder category**; show the format on the badge text.
- Only linkify file paths that resolve to a real file.
- Keep both token layers in sync (the app `--color-*`/`--brand-accent*` set and the Radix `--primary`/`--ring` HSL set).

### Don't

- Don't use pure white or cool gray for the canvas — the warm paper is the brand.
- Don't make Newsreader the global heading font; markdown headings and card titles stay grotesk.
- Don't repeat the assistant avatar on every message or on tool calls — once per turn.
- Don't color files by extension; group by folder so related artifacts share a hue.
- Don't reuse the Radix `--accent` token (a muted hover surface) for the brand — the brand lives in `--brand-accent*`.
- Don't open a Radix modal synchronously from its triggering click (it self-dismisses) — defer a tick.

## Responsive Behavior

| Name    | Width      | Key changes                                                                                                        |
| ------- | ---------- | ------------------------------------------------------------------------------------------------------------------ |
| Mobile  | < 768px    | Thread title/sub + roster label hide; roster pill shows dot + chevron; workspace stacks under chat; file grid 1-up |
| Tablet  | 768–1024px | Two-panel split tightens; file grid 2-up                                                                           |
| Desktop | ≥ 1024px   | Full top bar; resizable chat + workspace; collapsible docked threads panel; file grid 2-up                         |

- **Touch targets:** icon buttons 38px; send/icon-circular 36px; inputs/buttons 38px.
- **Overlays:** the threads panel is docked in-flow ≥ 1024px and overlays the content row with scrim + shadow below; pinning is a desktop affordance.
- **Code & paths:** mono blocks scroll horizontally inside their container rather than wrapping; the conversation column caps at 760px.

## Iteration Guide

1. Work one component at a time; reference its YAML key (`{component.tool-rail-row}`, `{component.file-card}`).
2. Use token refs everywhere — never inline hex. The accent especially must stay a token so all four accents + dark mode keep working.
3. Theme = `next-themes` (`.dark` class); accent = a `data-accent` attribute (emerald default). Both persist; SSR defaults to emerald with no flash.
4. New brand-colored surfaces use `--brand-accent*` (or the Button `primary` variant), never `bg-primary`.
5. Variants (`-active`, `-hover`, `-disabled`, `-focused`) are encoded on the base component; document default + pressed only.
6. The trinity is **paper neutrals + one selectable accent + warm semantics**. Don't introduce a fourth fixed brand hue.

## Known Gaps

- Two parallel token layers exist in `globals.css` — the app `--color-*`/`--brand-accent*` set (consumed via Tailwind extends) and the Radix `--primary`/`--ring`/`--background` HSL set (consumed by `components/ui/*`). They must be edited together; the accent feeds both.
- Temperature and system-instruction controls from the original design were deferred (no backend wiring yet); Settings currently exposes appearance, model overrides, and connection config.
- Animation timings (streaming caret, tool sweep, progress sheen, threads-panel width animation) are encoded ad hoc in `globals.css` / Tailwind, not formalized as motion tokens.
- The four accents ship solid/hover/soft/text quartets for light and dark; only the emerald set is exercised heavily — verify contrast when promoting another accent to default.
- This documents the product UI; transient states (toasts via `sonner`, tool-approval interrupt variants) are styled with tokens but not all enumerated here.
