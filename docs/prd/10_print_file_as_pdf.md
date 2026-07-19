---
type: PRD
title: "Print Workspace File as PDF"
description: "A Print button in the file viewer renders markdown/code/DOCX into a hidden iframe and calls window.print() for a save-as-PDF flow with no PDF library."
tags: [frontend, pdf, files]
timestamp: '2026-05-23T10:16:32+07:00'
status: "shipped"
scope: "Workspace > Files viewer"
version: v1
---

# Why

The Workspace > Files viewer lets users **Download** a file as its raw source. For markdown, code, and DOCX files the viewer already renders into formatted HTML (headings, links, syntax-highlighted code, tables), raw source is rarely what the user actually wants — they want a polished, shareable artifact (e.g. send a rendered interview battlecard to a recruiter). Without this, "save a nice copy of this file" is a manual copy-paste + reformat job.

# What the user sees

A new **Print** button next to **Download** in the file preview dialog (`FileViewDialog`). One click opens the browser's native print dialog overlaid on the current tab — the workspace and the file preview stay put. The user picks "Save as PDF" (or a physical printer) from the OS dialog. The default filename is the file's path with slashes turned to dashes and the extension stripped, e.g. `/interview_battlecard/tam-nguyen-lead-ai-ml-resume/within-ai-engineer-jd.md` → `interview_battlecard-tam-nguyen-lead-ai-ml-resume-within-ai-engineer-jd.pdf`.

The button is shown for **markdown, text/code, and DOCX**. It's hidden for already-PDF files (Download is enough), images, and unknown binaries. For DOCX it stays disabled until `mammoth` finishes the in-browser conversion.

# How — the key architectural choice

**Render the file content into a hidden, same-origin iframe pointing at a dedicated `/print/file` route, then auto-call `window.print()`.**

Why this shape rather than a PDF library or printing the dialog directly:

- **No PDF dependency.** Adding `jspdf`/`@react-pdf` etc. would require a container restart per `frontend/CLAUDE.md` and pull a heavyweight dep for what `window.print()` already does. Hash through the OS's "Save as PDF" instead.
- **Iframe, not new tab.** A new tab loses focus from the workspace; the user said so explicitly during review. A 0×0 hidden iframe keeps the user in place — the browser's print dialog overlays the current tab. Same-origin iframes share `sessionStorage` with the parent, so passing the rendered content needs no API call.
- **Dedicated print route.** Radix `Dialog` lives in a portal inside a `max-h-[80vh]` ScrollArea — printing the dialog itself would clip content. The `/print/file` route reuses `MarkdownContent` and `SyntaxHighlighter` so we don't duplicate rendering; it applies its own print CSS in isolation.

# Files of interest

| Concern | Path |
|---|---|
| Print page (iframe target) | `frontend/src/app/print/file/page.tsx` |
| Print button + iframe handler | `frontend/src/app/components/FileViewDialog.tsx` (`handlePrint`, lines ~189–227) |
| Reused markdown renderer | `frontend/src/app/components/MarkdownContent.tsx` |
| Reused DOCX → HTML conversion (already done in dialog state) | `FileViewDialog.tsx` (`docxHtml` via `mammoth`) |
| Theme CSS variables overridden for print | `frontend/src/app/globals.css` (light values mirrored under `.print-root` in `page.tsx`) |

# Decisions worth remembering

- **`sessionStorage` for the iframe payload, not query string or API.** Same-origin iframes share the parent's `sessionStorage`; the payload (`{path, content, kind, language?}`) lands under a fixed key (`nextrole:print-file`) and the print page clears it immediately after read. No server round-trip, no URL size limit, no leakage across origins.
- **Light theme is *redeclared*, not class-toggled.** `globals.css` flips theme via `@media (prefers-color-scheme: dark)` on `:root`. There is no `.light`/`.dark` class wired up. A `.light` wrapper would do nothing. Instead the print page re-declares the light values on `.print-root` — a more-specific selector — so dark-mode users still get a printable light PDF.
- **DOCX HTML is passed through, not re-rendered.** `mammoth` already ran in the dialog; the payload carries the rendered HTML for the DOCX case and the print page drops it in via `dangerouslySetInnerHTML` (same trust boundary as the dialog — the user's own uploaded document).
- **`document.title` swap on the *parent* for the Save-as filename.** Browsers use the top-level document's title — not the iframe's — as the default print job name. The handler temporarily overwrites `document.title` and restores it on `afterprint`. The tab title flickers for a moment; acceptable.
- **Path → filename mangling.** `/folder_a/folder_b/file.md` becomes `folder_a-folder_b-file.pdf` so nested files keep their folder context in the saved file. Slashes → dashes; extension stripped; leading slash dropped.
- **`await document.fonts.ready` + one `requestAnimationFrame` before `window.print()`.** Without these the print preview can fire mid-paint and render the first page un-fonted.
- **iframe cleanup on `afterprint`.** The parent's listener on `iframe.contentWindow` removes the iframe whether the user saved or cancelled. The print page's own `window.close()` on `afterprint` is a no-op inside an iframe (browsers block `close()` on windows the script didn't open) — kept as belt-and-suspenders for the case someone navigates to `/print/file` directly.

# Deferred (intentional non-goals for v1)

- **Server-side / headless PDF rendering** (Playwright, Puppeteer). Browser print is good enough; revisit only if WYSIWYG drift across browsers becomes a problem.
- **Custom cover page / header / footer / page numbers.** Out of scope; not requested.
- **Bulk export** (multiple files → one combined PDF, e.g. all files in `/interview_battlecard/<role>/`). Possible follow-up if users start asking.
- **PDF export for already-PDF files** — existing Download already saves the PDF bytes; no value in re-printing.
- **PDF export for images / unrecognized binaries** — out of scope.

# How to verify end-to-end

1. `docker compose up -d` and grab the frontend host port from `docker ps`.
2. Open Workspace > Files, click a `.md` file. **Print** appears next to **Download**.
3. Click Print → the OS print dialog appears over the current tab (no new tab). The preview shows the rendered markdown — headings, links, code blocks, tables, light backgrounds.
4. The Save-as filename defaults to `<folder_a>-<folder_b>-<basename>.pdf`. Save and open the resulting PDF.
5. Cancel the dialog on a separate try — the hidden iframe disappears, the original tab title is restored.
6. Repeat for a code file (`.py`/`.ts`/`.json`) — syntax-highlighted with the `oneLight` Prism theme, no dark backgrounds in the PDF.
7. Repeat for a `.docx` — the mammoth-rendered HTML matches the dialog preview.
8. Open a `.pdf` file or an image — Print button is hidden (only Download).
9. Toggle the OS to dark mode and repeat the markdown case — the PDF must still be light-on-dark text on a white background.
