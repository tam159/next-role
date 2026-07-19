---
name: write-prd
description: Generate a concise PRD for the feature built in the current session and save it to docs/prd/. Use this skill whenever the user says "write a PRD", "generate a PRD", "/write-prd", asks to document the feature just shipped, or otherwise requests a product/feature spec for work done in this session — even if they don't name the docs/prd/ folder.
---

Write a PRD that captures the feature implemented in the current session, in the house style of `docs/prd/`. That folder is an **OKF (Open Knowledge Format) knowledge bundle** — see `docs/prd/README.md` — so every PRD is a markdown concept document with YAML frontmatter, and the relative links between PRDs draw the edges of the bundle's knowledge graph.

The audience is a future developer or AI agent reading the file cold — months later, after the code has drifted. The point is not to restate what the diff shows; it is to capture the *why* and the *non-obvious decisions* that the code alone can't explain. The frontmatter `description` doubles as the PRD's one line in `docs/prd/index.md` — it is what an AI agent reads to decide whether to open the file at all, so make it carry the feature's essence.

## Workflow

1. **Anchor on the existing style.** Read at least one existing PRD in `docs/prd/` (e.g. `10_print_file_as_pdf.md` or `03_jd_url_extraction.md`) before writing. Match its tone, density, and section order. Do not invent new sections.

2. **Mine the session for content.** The conversation history is your source material — pull from:
   - The user's original requirement (becomes the *Why*).
   - Approach choices made during planning, especially ones where you considered alternatives and picked one. These are the *Decisions worth remembering*.
   - The actual files touched (use `git diff` / `git status` if needed to be precise) — these populate *Files of interest*.
   - User corrections and "no, do it this way" moments — these are usually the most valuable decisions to record.
   - Verification steps you ran (curl, browser checks, tests) — these become *How to verify end-to-end*.

3. **Propose a filename and version, then confirm.** Filename is `NN_snake_case.md` — `NN` is the zero-padded next number in sequence (`ls docs/prd/` for the current highest; e.g. `28_feature_name.md`). Version is `v1` for a new feature, `v2` etc. if extending one — check whether a related PRD already exists. Briefly tell the user the proposed path and version in one line, then write it. Don't block on a long confirmation cycle; the user can rename if needed.

4. **Write the PRD using the template below.** Save to `docs/prd/NN_<name>.md`.

5. **Update the bundle indexes.** Add the PRD's line to `docs/prd/index.md` under `# PRD` — title-alphabetical order, format `* [Title](NN_name.md) - <description from frontmatter>` — then regenerate `docs/prd/viz.html` (commands live in `docs/prd/README.md` → "Adding or updating a PRD"; expect the reported concept count to go up by one).

6. **Show the user the path and a one-line summary** of what was written. Don't dump the PRD back into chat — they can open the file.

## PRD template

Use this exact structure. Section names and order are not negotiable — they're the contract that lets future readers skim across PRDs. The `**Extends:**` line is included only when the feature builds on an earlier PRD. (Example links here use placeholder targets like `NN_other.md` deliberately — never put real `NN_*.md` targets inside a code fence; the graph's edge scanner reads fences too.)

```markdown
---
type: PRD
title: "<Feature Name — no 'PRD:' prefix, no version suffix>"
description: "<One sentence distilled from Why — becomes the index.md line. Ends with a period.>"
tags: [<2-4 from the taxonomy below>]
timestamp: '<now, ISO 8601 with offset, e.g. 2026-07-18T12:00:00+07:00>'
status: "shipped"
scope: "<which app/area>"
version: v<N>
---

**Extends:** [<other PRD title>](NN_other.md)

# Why

<1–2 paragraphs. What problem did this solve? What was painful or impossible before? Why now? Reference the README or upstream PRD if the feature is part of a larger flow.>

# What the user sees

<The user-visible surface, in plain language. Where the button is, what gets typed, what file appears. Include accepted formats / limits / edge UI behavior if they exist. If a surface was *deliberately not added* (e.g. "no paperclip in the chat composer"), say so and why — negative space is often the most valuable thing to record.>

# How — the key architectural choice[s]

<The 1–3 design decisions that shaped the implementation. For each: state the decision in bold, then explain why this shape and not the obvious alternative. This is the heart of the PRD — if a future reader skims only one section, it's this one.>

# Files of interest

| Concern | Path |
|---|---|
| <what this file does> | `<path>` [(`<symbol>`, lines ~A–B)] |
| ... | ... |

# Decisions worth remembering

- **<Short decision title>.** <One paragraph explaining what was chosen, what was rejected, and why. Include the failure mode the rejected option would have caused if non-obvious.>
- ...

# Deferred (intentional non-goals for v<N>)

- **<Thing we explicitly didn't do>.** <Why it's out of scope, and what would trigger revisiting it.>
- ...

# How to verify end-to-end

1. <Concrete step a human can run, starting from a fresh `docker compose up -d` if relevant.>
2. <...>
```

## OKF rules

- **Frontmatter quoting is mechanical, no judgment**: `type: PRD` and `version: vN` plain; `title`, `description`, `status`, `scope` always double-quoted (titles can start with `@` or contain colons — unquoted they break YAML parsing); `timestamp` always single-quoted (unquoted, YAML coerces it into a datetime object); `tags` a flow list of plain lowercase words. Key order: `type, title, description, tags, timestamp, status, scope, version`.
- **`type`, `title`, `description`, `timestamp` are required non-empty** — the OKF tooling validates all four. `timestamp` is the moment of writing, ISO 8601 with UTC offset.
- **Tag taxonomy** — pick 2–4: area `frontend` `backend` `agent` `infra` · domain `files` `pdf` `streaming` `subagents` `workflow` `auth` `ui` `memory` `testing` `llm` `storage` · lifecycle `superseded` `partially-superseded` (only on PRDs that have been superseded).
- **Cross-PRD links MUST be relative** (`[Title](NN_name.md)`) — they are the graph edges. Linkify inline mentions of other PRDs in prose too ("see [PRD 07](NN_other.md)"), not just the relations line. Links to files elsewhere in the repo use repo-relative paths (e.g. `../../backend/README.md`) — valid on GitHub, ignored by the graph. Don't manufacture edges: a standalone feature with no relations is honest.
- **No body H1 title** — the title lives in frontmatter; body sections are H1 (`# Why`).
- **Never create `docs/prd/log.md`**, never add frontmatter to `docs/prd/index.md` (both filenames are OKF-reserved), and never drop a non-PRD `.md` into the folder — every non-reserved file becomes a graph node.

## Superseding an earlier PRD

When the new feature replaces an old one, both files change:

- New PRD: a `**Supersedes:** [NN_old](NN_old.md)` relations line (append `, partially [NN_other](NN_other.md)` where only part of it is replaced).
- Old PRD: set `status: "superseded by NN_new_name"`, add the `superseded` (or `partially-superseded`) tag, and open its body with a blockquote banner — `> **Superseded by [NN_new](NN_new.md)** — <what replaced it and what, if anything, survives>`.

The worked pair to copy: `16_disable_subagent_streaming.md` / `18_langchain_react_migration.md`.

## Writing style — match the existing PRDs

- **Dense, not exhaustive.** Each existing PRD is ~50–70 lines of body. Don't pad. If a section would only contain "N/A", omit it (except *Why*, *What*, *How* — those are always present).
- **Explain by contrast.** "We did X *instead of* Y because Y would have ..." is the dominant pattern. Readers learn the constraints from the rejected paths.
- **File paths with backticks**, and add `(symbol, lines ~A–B)` for files where the relevant code is a small slice of a larger file.
- **Concrete artifacts over abstractions.** Name the actual function, route, env var, table — not "the relevant module".
- **No section headers we don't have.** No "Background", "Goals", "Success metrics", "Open questions". Keep the surface flat.
- **Capture the user's corrections.** If during the session the user pushed back on an approach ("no, use an iframe not a new tab"), that becomes a Decision — these are exactly the things the code won't reveal later.

## Do not

- Don't restate code logic that a reader can derive from a file you're already pointing to. The PRD complements the code; it doesn't duplicate it.
- Don't write aspirational content. If something wasn't built this session, it goes under *Deferred* (or is omitted), not the body.
- Don't add a "Changelog" or "Authors" section. Git history covers that.
- Don't include the conversation transcript or quoted user messages — translate them into design statements.
