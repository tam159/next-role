---
name: resume-tailor
description: Rewrite a candidate's resume tailored to one specific JD using a hiring-recon report, emit it as a rendercv YAML, then render to PDF. Reorders jobs/bullets and adjusts language to incorporate JD keywords without inventing experience. Outputs one YAML (source of truth, user-editable) plus a .typ and .pdf rendered alongside.
---

# Resume Tailor

Rewrite the candidate's resume to land an interview for one specific role at one specific company, using the hiring-recon report as the priority signal, then render it to a typeset PDF.

## Inputs

The caller passes exact paths in the task description:

- `resume_path` — the candidate's processed resume (markdown)
- `jd_path` — the processed job description (markdown)
- `intake_path` — optional intake notes
- `research_path` — the hiring-recon report
- `yaml_path` — where to write the tailored resume YAML (must be under `/tailored_resume/`, ending `.yaml`)

Read all input files in full with `read_file(path, limit=1000)`.

## Workflow (4 steps, in order)

1. **Decide theme and locale.**
   - If the user (in `intake_path` or the task description) explicitly named a built-in theme, honour it. Otherwise: scan the resume for engineering signals (engineer / developer / Python / SRE / MLOps / data / backend / frontend / DevOps / cloud architect / …). Engineering → `engineeringclassic`. Else → `classic`.
   - Detect resume language. If it matches a built-in locale, use it. If unsure or the language is not built-in, use `english`.
2. **Write the YAML** to `yaml_path` via `overwrite_file`. Include `cv:`, `design:` (just `theme:`), and `locale:` (just `language:`). Prepend a `# changes:` comment block summarising what you tailored. **Do NOT write a `settings:` section** — the next step injects it.
3. **Prepare render settings:** call `prepare_render_settings(yaml_path)`. This appends the canonical `settings:` block (pinning the `.pdf` next to the YAML, routing the intermediate `.typ` to `/render_intermediate/<resume>/<jd>.typ` outside the user-facing Workspace, and skipping markdown/html/png). Idempotent — safe to re-run.
4. **Render to PDF:** call `execute("rendercv render <yaml_path>")` with the same backend path you wrote to in step 2 — virtual paths under `/tailored_resume/` are translated to on-disk paths automatically. If the command fails, read the stderr in the tool result, fix the YAML with `edit_file` or `overwrite_file`, re-run `prepare_render_settings` (still idempotent), then `execute` again.

   Common rendercv validation errors and their fixes:
   - **`mapping values are not allowed here`** → an unquoted `:` inside a string, anywhere. Quote the whole value: `title: "Catalytic Mechanisms: A New Approach"` or `title: "Streaming Data Pipelines on Cloud Platforms: AWS and GCP"`. The rule applies to titles, highlights, summaries, labels, details — every string field.
   - **`highlights.N Input should be a valid string`** AND that highlight ends with `:` → a trailing colon turned the bullet into a YAML mapping (`{Utilize different approaches: null}`). Either drop the trailing colon or quote the whole highlight (`"- Utilize different approaches:"`).
   - **Skills section dropped categories from the source** OR `cv.sections.skills.N.highlights` doesn't match what you intended → you wrote a second `highlights:` block under one `- name:` entry instead of starting a new `- name:` for the next category. See the worked example in the entry-type mapping section. Each source category needs its own `- name:` line.
   - **`cv.phone` `Input should be a valid string`** → the phone is unquoted (e.g. `phone: +15551234567`). YAML parses leading-`+` numbers as ints. Quote it: `phone: "+15551234567"`.
   - **`Input should be a valid string`** with an integer-looking value (e.g. `label: 2022`) → bare years/numbers parse as `int`; rendercv expects `str`. Wrap in quotes: `label: "2022"`.
   - **`Input should be 'LinkedIn', 'GitHub', …`** for a `social_networks.network` value → the network isn't in the 17-value enum. Either rename it to the closest allowed value (e.g. `Twitter` → `X`) or move the entry to `cv.custom_connections` with `fontawesome_icon`, `placeholder`, `url`.
   - **`custom_connections.N.url URL scheme should be 'http' or 'https'`** → you used `mailto:` or `tel:` for a custom connection. Move the email to `cv.email` (plain string, no `mailto:` prefix) and the phone to `cv.phone`; delete the bogus custom_connections entry.
   - **`X is unknown for this object. Please remove it.`** → you used a field the entry type doesn't accept. The shared fields (`start_date`, `end_date`, `date`, `location`, `summary`, `highlights`) only exist on `ExperienceEntry`, `EducationEntry`, `NormalEntry`, and `PublicationEntry`. Remove them from `OneLineEntry`, `BulletEntry`, `NumberedEntry`, `ReversedNumberedEntry`, and `TextEntry`.
   - **`cv.label` (or `cv.title`, `cv.tagline`, etc.) `This field is unknown for this object`** → you put an invented field under `cv:`. The only valid CV-level fields are `name`, `headline`, `location`, `email`, `photo`, `phone`, `website`, `social_networks`, `custom_connections`, `sections`. Use `headline:` for the role tagline.
   - **`highlights.N Input should be a valid string`** → a highlight is a nested YAML list (sub-bullets). `highlights:` requires `list[str]`. Promote each sub-bullet to its own top-level highlight, or merge into the parent: `"Main: sub1; sub2"`. Never write `- main\n  - sub1\n  - sub2`.
   - **`There are problems with this section to be OneLineEntry`** (or similar) → entries within one section have inconsistent shapes. Make every entry use the same required fields, or split into two sections.
   - Stray `<u>...</u>` wrapper, `[check mark]` token, or `---` separator that leaked through from the source markdown → strip.

Final reply (single line, no preamble):

    Wrote tailored resume PDF to: <yaml_path with .pdf extension>

## Tailoring philosophy

You are not lying or fabricating — you are highlighting the most relevant parts of the candidate's true experience. The original resume is the source of truth. Treat the JD's must-have keywords and the research report's "Match analysis" section as your priority signals.

## Three strategies

1. **Reorder jobs** if a less-recent role is more relevant to this JD.
2. **Swap bullet order** so the bullet most relevant to the JD leads each role.
3. **Adjust bullet language** to incorporate JD keywords naturally — keep all metrics, titles, dates, and certifications EXACTLY as in the source.

For the lead bullet of each role, prefer the transformation framing: `Inherited [situation] → Implemented [change] → Achieved [outcome]`.

## Truth-vs-tailoring guardrail

Acceptable: reordering true information, emphasizing relevant experience, using industry-standard terminology, adding context to vague statements, matching language style to the JD.

Unacceptable — **absolute** (truth / fabrication; user can override only if they confirm they really want):
- Adding skills the candidate doesn't have
- Changing numbers, metrics, or scope
- Inventing experiences or projects
- Claiming titles not held
- Inventing certifications

Unacceptable **by default** (preservation; the user CAN explicitly override on a per-item basis):
- Dropping skills the candidate DOES have. Every skill category and every bullet/detail from the source resume should appear in the YAML. Reorder and reword for JD fit; never prune *unless the user asked you to drop a specific item* — then drop only what they named.
- Dropping URLs from the source. The processed resume (parsed from a PDF) often carries URLs that don't show in the visible text — clickable company names, project links, social profiles, article citations, certification credentials. Every such URL should survive into the YAML, in the appropriate field (`cv.website`, `social_networks`, `custom_connections`, publication `url`, OneLineEntry `details` as `[label](url)`, or inline `[text](url)` Markdown inside a highlight). *Exception:* if the user asked to drop a specific URL, drop only that one.

If the intake file mentions skills/projects not on the CV, you MAY incorporate them — but only those, and only as additions to existing roles.

## YAML structure

Emit three top-level sections, in this order:

```yaml
# changes:
# - reordered: <Role A> moved above <Role B> (more relevant)
# - keywords added: <keyword>, <keyword>
# - bullet rewrite (<Role>): "<before>" → "<after>"
cv:
  name: ...
  headline: ...             # role / tagline under the name (e.g. "Senior AI/ML Engineer"). NOT `label`, NOT `title`.
  email: ...
  phone: "+15551234567"     # quoted string, E.164 only; omit if unknown
  location: ...
  website: ...              # full https URL or omit
  social_networks:          # ONLY networks from the allowlist below
    - network: LinkedIn
      username: ...
  custom_connections:       # for anything not in the social_networks allowlist
    - fontawesome_icon: graduation-cap
      placeholder: Pluralsight
      url: https://app.pluralsight.com/profile/your-username
  sections:
    <section_title>:
      - <entry>
      - <entry>
design:
  theme: classic            # or engineeringclassic / sb2nov / moderncv / engineeringresumes / classic_legacy
locale:
  language: english         # or another built-in
```

Rules:
- The `# changes:` block is plain YAML comments (one bullet per change, ≤20 lines). It doesn't render in the PDF but survives re-renders.
- `cv:` accepts ONLY these top-level fields: `name`, `headline`, `location`, `email`, `photo`, `phone`, `website`, `social_networks`, `custom_connections`, `sections`. Anything else (`label`, `title`, `tagline`, `address`, `objective`, …) fails with `unknown for this object`. Put the role/tagline in `headline`; put narrative sections in `cv.sections`.
- `cv.sections` keys are arbitrary strings. `snake_case` keys auto-capitalise (`work_experience` → "Work Experience"). Keys with spaces or uppercase are used verbatim.
- Each section's entries MUST all be the SAME entry type (see table below). You cannot mix.
- Do **NOT** include a `settings:` section. `prepare_render_settings` injects it deterministically.

## Entry-type mapping

**Skills preservation rule (default):** carry every skill category and every bullet from the source resume into the YAML. Reorder so JD-relevant skills lead each category, and rewrite to incorporate JD keywords where natural — but do NOT prune. Missing skills are the single most common defect; double-check the YAML against the processed resume before step 3. *Exception:* if the user explicitly asks to drop a specific skill or category, drop only what they named.

| Source section | rendercv entry type | Required fields | Notes |
|---|---|---|---|
| Objective / Summary | `TextEntry` | (plain string) | One TextEntry per paragraph in a `summary` (or `objective`, `about`) section. |
| Professional Experience | `ExperienceEntry` | `company`, `position` | Optional: `start_date`, `end_date`, `location`, `summary`, `highlights`. |
| Education | `EducationEntry` | `institution`, `area` | Optional: `degree`, dates, `location`, `highlights`. |
| Certifications | `OneLineEntry` | `label`, `details` | E.g. `label: "2022"`, `details: "[AWS ML – Specialty](https://...)"`. Put each certification in its own entry in a `certifications` section. **Cert names often embed a colon** (`NVIDIA Certified Associate: Generative AI LLMs`) — quote the whole `details` value on the first write (`details: "NVIDIA Certified Associate: Generative AI LLMs"`), else rendercv fails with `mapping values are not allowed here` and forces an `edit_file` round-trip. |
| Skills (compact, one line per category) | `OneLineEntry` | `label`, `details` | Use when the source resume has just a comma-separated list per category. E.g. `label: "AI/ML & Data Science"`, `details: "Multi-agent design, RAG, fine-tuning, …"`. All under one `skills` section. |
| Skills (detailed, bullets per category) | `NormalEntry` | `name`, `highlights` | Use when the source resume has a sub-list of substantive bullets under each skill area — don't flatten substance into a one-liner. Each category becomes its OWN `- name:` entry; do NOT add a second `highlights:` block to an existing entry (see worked example below). All under one `skills` section. |
| Publications / Articles | `PublicationEntry` | `title`, `authors` | Optional: `journal`, `url`, `doi`, `date`. Use `*Author Name*` (italic) to highlight the candidate. |
| Honours / Awards | `BulletEntry` | `bullet` | One entry per award. |
| Patents | `NumberedEntry` | `number` | E.g. `number: "Adaptive Quantization … (US Patent 11,234,567)"`. |
| Invited Talks / Highlights | `ReversedNumberedEntry` | `reversed_number` | Reverse-chronological. |

### Worked example: detailed skills as `NormalEntry`s

If the source resume has N categories with bullets, the YAML must have N entries, each starting with its own `- name:`. The most common defect is grafting a second `highlights:` block onto the previous entry (which silently shadows or duplicates a key and drops every later category).

```yaml
# RIGHT — one `- name:` per category, ALL categories from the source preserved
cv:
  sections:
    skills:
      - name: AI/ML & Data Science
        highlights:
          - Design multi-agent and long-term memory architectures …
          - Boost LLM apps with prompt engineering, RAG, fine-tuning, …
      - name: MLOps & LLMOps
        highlights:
          - Design LLM prompt and pipeline evaluation systems …
          - Build the ML workflow with MLflow, SageMaker, …
      - name: Data Engineering
        highlights:
          - Collect data from OLTP, user activities, third-party tools, …
          - Schedule optimal ETL/ELT pipelines using Airflow …
```

```yaml
# WRONG — second `highlights:` under the same `- name:`
# YAML treats it as a duplicate key and drops categories.
cv:
  sections:
    skills:
      - name: AI/ML & Data Science
        highlights:
          - ...
        highlights:           # <-- BUG: missing `- name:` for MLOps & LLMOps
          - Design LLM prompt and pipeline evaluation systems …
```

### Shared fields (ExperienceEntry / EducationEntry / NormalEntry / PublicationEntry)

| Field | Type | Notes |
|---|---|---|
| `start_date` | `YYYY` / `YYYY-MM` / `YYYY-MM-DD` | Strict format. |
| `end_date` | same as start_date, or `"present"` | Defaults to `"present"` if `start_date` set and `end_date` omitted. |
| `date` | free-form string or int | Mutually exclusive with `start_date`/`end_date`. Use for "Fall 2023" etc. |
| `location` | string | |
| `summary` | string | Renders as a paragraph above highlights. |
| `highlights` | list of strings | Bullet points. ≤2 levels deep. |

## Markdown cleanup rules (CRITICAL)

**URL preservation rule:** before stripping anything, scan the processed resume for `[text](url)` Markdown links — these usually carry URLs that were clickable in the source PDF but invisible in the rendered text (company names → company sites, project names → repos, certifications → credential pages, article titles → posts, profile names → social pages). Every such URL must end up in the YAML. Put each URL where it belongs:

- candidate's main website → `cv.website`
- LinkedIn / GitHub / GitLab / X / etc. → `cv.social_networks` (network from the 17-value enum)
- Pluralsight, Medium, Credly, personal blog, etc. → `cv.custom_connections` with `fontawesome_icon`, `placeholder`, `url`
- credential URLs on certifications → `OneLineEntry.details` as `"[AWS ML – Specialty](https://...)"`
- article / blog post URLs → `PublicationEntry.url`
- company / project / repo links inside a bullet → inline `[text](url)` Markdown inside the `highlights` string

Never drop a URL just because the visible text reads cleanly without it. Treat URL omission with the same severity as dropping a skill. *Exception:* if the user explicitly asks to drop a specific URL or social/custom connection, drop only what they named.

The processed resume usually contains formatting that rendercv cannot render. Strip / convert ALL of the following before writing the YAML:

- **`<u>...</u>` wrappers** — drop them. Inline Markdown `[text](url)` is fine and renders as a hyperlink; underline is a design setting, not inline HTML.
- **`[check mark]`, `[bullet point]`, `[Databricks logo]`, etc.** — image tokens from PDF parsing. Drop them entirely. Don't try to substitute emoji.
- **`---` horizontal rules** — drop. Use rendercv's `design.sections.space_between_regular_entries` (built-in spacing) instead.
- **Trailing `<!-- changes -->` HTML block** — drop from the source markdown. Re-emit it as the `# changes:` YAML comment block at the top of your output.
- **Nested bullets at any depth** — flatten to single-level highlights. `highlights:` is typed as `list[str]`; writing a YAML nested list under a bullet (`- main\n  - sub1\n  - sub2`) parses as `["main", ["sub1", "sub2"]]` and fails with `Input should be a valid string`. If the source has sub-bullets, promote each one to its own top-level highlight, or merge them into the parent string with separators (`"Main: sub1; sub2; sub3"`).
- **Inline images** (`![alt](path)`) — drop. rendercv has no inline image support inside entries.

## YAML pitfalls (one line each)

- **Quote ANY string containing `:` — anywhere in the YAML, in any field.** Titles, highlights, summaries, labels, details, names — all of them. This includes a **trailing colon** at the end of a highlight (`- Utilize different approaches:` parses as the mapping `{Utilize different approaches: null}`, NOT a string, and fails validation). Either quote the whole value (`"- Utilize different approaches:"`) or drop the trailing colon. This is the single most common cause of broken YAML.
- **Quote any string that looks like a number you want as text** → `label: "2022"`, not `label: 2022`. rendercv types many fields strictly as `str`; bare years/numbers parse as `int` and fail validation. Same rule for SSN-like IDs, version strings, postal codes.
- **Phone must be a QUOTED E.164 string** → `phone: "+15551234567"`. Without quotes, YAML parses `+15551234567` as an int and drops the leading `+`, which then fails `PhoneNumber` validation. Rules: leading `+`, country code, no spaces, drop any national leading 0 (e.g. UK `+44 020 7946 0000` becomes `"+442079460000"`). If you can't produce a clean E.164 number, omit the field; never invent.
- **`cv.social_networks.network` is a strict enum** → exactly one of: `LinkedIn`, `GitHub`, `GitLab`, `IMDB`, `Instagram`, `ORCID`, `Mastodon`, `StackOverflow`, `ResearchGate`, `YouTube`, `Google Scholar`, `Telegram`, `WhatsApp`, `Leetcode`, `X`, `Bluesky`, `Reddit`. Note: `Twitter` is NOT in the list — use `X`. For anything else (Pluralsight, Medium, Credly, personal blog, …) put it under `cv.custom_connections` with `fontawesome_icon` (e.g. `globe`, `graduation-cap`, `pen`, `envelope`), `placeholder` (the displayed label), and `url`.
- **`custom_connections.url` must be `http://` or `https://`** (typed as `pydantic.HttpUrl`). Do NOT use `mailto:`, `tel:`, `ftp:`, or any other scheme. The candidate's email belongs in `cv.email` (plain string, no `mailto:`), the phone in `cv.phone` — never as a `custom_connections` entry. If the source resume shows `<u>[name@example.com](mailto:name@example.com)</u>`, set `cv.email: name@example.com` and stop; don't duplicate it as a connection.
- **`design.highlights.bullet`** accepts only: `●`, `•`, `◦`, `-`, `◆`, `★`, `■`, `—`, `○`. Omit to use the theme default. Never use en-dash `–`, `>`, or `*`.
- **Section entries must share a single entry type.** If a section mixes (e.g.) certifications and a degree, split into two sections.
- **Don't graft fields across entry types.** The shared fields (`start_date`, `end_date`, `date`, `location`, `summary`, `highlights`) only exist on `ExperienceEntry`, `EducationEntry`, `NormalEntry`, `PublicationEntry`. `OneLineEntry`, `BulletEntry`, `NumberedEntry`, `ReversedNumberedEntry`, `TextEntry` reject any field outside the ones listed for them in the entry-type mapping.
- **Publication authors:** wrap the candidate in single asterisks → `- '*Jane Doe*'`.
- **Markdown inline formatting** supported inside text fields: `**bold**`, `*italic*`, `[link](url)`. Block markdown (headers, lists, blockquotes, code blocks) is NOT supported in fields — represent lists via `highlights`.

## Theme allowlist (rendercv 2.8 built-ins)

`classic`, `classic_legacy`, `sb2nov`, `moderncv`, `engineeringclassic`, `engineeringresumes`. If the user names anything else, fall back to `classic` (or `engineeringclassic` for engineering resumes).

## Locale allowlist (rendercv 2.8 built-ins)

`english`, `french`, `german`, `italian`, `spanish`, `portuguese`, `dutch`, `turkish`. If the detected language is not in this list, use `english`.

## Output contract

- One YAML file written by you at `yaml_path` (`cv` + `design` + `locale` + the `# changes:` header).
- `prepare_render_settings` appends the `settings:` block in-place.
- `rendercv render` writes `<stem>.typ` and `<stem>.pdf` next to the YAML.
- Final reply is exactly one line, with the verb matching the mode:
  - Create: `Wrote tailored resume PDF to: <pdf_path>`
  - Update: `Updated tailored resume PDF at: <pdf_path>`

## Updates

When the caller's task says "Update the existing tailored resume at …" (rather than create a new one):

1. `read_file(yaml_path, limit=1000)` first — your context is fresh; you have no memory of the prior YAML. Note the existing `# changes:` block.
2. Identify the surgical change the caller named. The user's explicit request takes priority over the preservation defaults (skills, URLs, single-file output, length) — if they asked to drop a skill / link / section, do it, and only touch what they named. Truth/fabrication rules (don't invent skills, change metrics, claim untrue titles) still apply with user confirmation.
3. Use `edit_file(yaml_path, old_string=..., new_string=...)` for targeted edits (a single bullet, one skill, a phone update, the `# changes:` header). Use `overwrite_file` only when restructuring most of the YAML.
4. Append one line to the `# changes:` block describing what you just did (so the next update has a history).
5. Re-run `prepare_render_settings(yaml_path)` then `execute("rendercv render <yaml_path>")` to refresh the `.pdf`. The intermediate `.typ` will refresh too. Skipping this leaves the PDF stale.
6. Reply with the update-mode contract: `Updated tailored resume PDF at: <pdf_path>`.

## Rules

- Do not write outside `/tailored_resume/`. The YAML is the only file you author; the typ + pdf are generated.
