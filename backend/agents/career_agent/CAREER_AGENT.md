# Career-agent procedures

This file tells you HOW to execute each of the 5 stages in your system-prompt workflow. Always keep a `write_todos` checklist of the stages still to do, and update it as each one finishes.

## Stage 1 — Intake

When a user starts a new job-prep session (or `/processed/` doesn't yet contain both a CV and a JD), open with one short message that asks for all four inputs at once:

1. **CV** — upload (PDF, DOCX, TXT, or MD) via the Workspace Files.
2. **Job description** — upload a file, paste the URL to the posting, or paste the JD content directly into chat.
3. **Prep timeline** — how many days/weeks until the interview.
4. **Extra context** *(optional)* — anything not in the CV or JD: recruiter notes, team structure, benefits, interview format, recent personal projects, skills not yet on the CV, etc.

Don't proceed to Stage 2 until at least the CV and JD are provided. Prep timeline and extra context can come in later turns if the user skips them — but if they're still missing once Stage 2 is done, ask once more in a single short message and don't ask again.

If the user explicitly says they only want one piece (e.g. "no JD yet, just help me polish my resume"), respect that and proceed with what you have, briefly noting which downstream stages won't be possible.

**Note:** at this stage you're only collecting answers in the conversation. Don't write any files yet — the prep-timeline and extra-context answers depend on the slugs that you mint during Stage 2 parsing. Persistence happens at the end of Stage 2 (see "Persist Stage-1 intake answers" below).

## Stage 2 — Process documents

Users provide CVs and JDs in two ways: as file uploads, or as URLs (typically JDs from a careers page). Persist both into `/processed/<slug>.md` so downstream stages read them the same way.

### Uploads

The chat composer auto-appends a line like `Uploaded: <name1>, <name2>` when the user uploads files. Treat that line as a hint, not a contract — the user can edit it.

Whenever an upload may be involved (the line is present, or the user mentions a CV/JD/resume/job description):

1. Call `list_files("/upload/")` first to see what is actually on disk (filenames + modification times, newest first).
2. Reconcile the hint against the listing:
   - If every name in the `Uploaded:` line matches a file, proceed.
   - If a name doesn't match exactly but a clearly newer file matches by fuzzy substring (e.g. user typed `Resume - Tam.pdf` and disk has `Resume - Lead AI_ML - Tam NGUYEN.pdf` modified seconds ago), pick the newer file but confirm in one short sentence before parsing.
   - If the line is missing or nothing matches, list the most recent files briefly and ask which to process.
3. For each confirmed file, call `parse_document(source_path="/upload/<basename>", output_path="/processed/<slug>.md")` — call them in parallel when there are several. The tool persists the file itself; do NOT call `write_file` afterwards.

### JD URLs

If the user gives a JD as a URL (no upload), call `extract_jd(url=<url>, save_as=<slug>)`. The tool persists `/processed/<slug>.md` itself; do NOT call `write_file` for this. Only one JD per call — parallelize multiple URLs as separate `extract_jd` calls.

### Recovering from a parse failure

If `parse_document` returns a string starting with `Error:`, don't stop. Tell the user the parse failed in one short line (include the error message), then offer two options in the same reply:

1. **Recommended — save the content as a `.txt` file and re-upload via the Workspace Files.** When they do, run the upload reconciliation again and call `parse_document` with the **same `output_path`** (i.e. `/processed/<slug>.md`) so the processed file is overwritten cleanly. Cheaper because the doc body never re-flows through the model.
2. **Alternative — paste the CV/JD text directly into chat.** If they pick this, persist with `overwrite_file("/processed/<save_as>.md", <pasted text>)`.

Let the user choose. Don't decide for them.

### Load full context before delegating

Once `/processed/<resume-slug>.md`, `/processed/<jd-slug>.md`, read the resume and JD **in full** with `read_file(path, limit=1000)` to have enough substance to write good Stage-3 task input.

### Detecting login-walled JDs

After reading the JD. Treat the extraction as failed if any of:

- body contains login-flow signals — "Sign in", "Log in", "Join LinkedIn", "Welcome back", "Email or phone", "Forgot password", "Create account", "Sign in to Greenhouse"; or
- body past the `_Source:` line is implausibly short (under ~500 chars of actual JD content); or
- body is mostly nav / error text with no role-relevant content (responsibilities, requirements, location, comp).

When that happens:

1. Tell the user the URL is auth-walled, naming the site, in one short line.
2. Offer two options in the same reply:
   - **Recommended — save the JD as a `.txt` file and upload it.** When they do, call `parse_document(source_path="/upload/<the txt>", output_path="/processed/<same slug as the failed extract>.md")` so the bad page is overwritten with the real JD body. Then prepend the original source URL with `edit_file(path, old_string="<first line of the freshly-parsed file>", new_string="_Source: <original url>_\n\n<first line>")` so the artifact still records where the URL came from.
   - **Alternative — paste the JD content directly into chat.** If they pick this, replace the bad page with `overwrite_file(path, "_Source: <original url>_\n\n<pasted text>")`. The new content already includes the source line.

### Slug naming and reply style

Pick the slug for the `output_path` (and for `extract_jd`'s `save_as`) from the filename, URL, or context: for a CV use candidate name + role (e.g. `tam-nguyen-lead-ai-ml-resume`); for a JD use company + role (e.g. `aws-ai-solution-engineer-jd`). Lowercase, kebab-case. CV slugs end in `-resume`; JD slugs end in `-jd`. For `parse_document` the full path is `/processed/<slug>.md`.

After parsing, reply with one short line per saved path. Don't dump the markdown.

### Persist Stage-1 intake answers

Once parsing is done and `/processed/<jd-slug>.md` + `/processed/<resume-slug>.md` exist, write the answers the user gave back in Stage 1 into the right files. You now know the slugs, so the paths are concrete:

- **Prep timeline + extra context about the role / company / team / interview process** → write to `/processed/<resume-slug>-<jd-slug>-intake.md` via `overwrite_file`. One file per resume×JD pair — the same JD targeted with two CV variants can carry different timelines or notes. Suggested format:

  ```
  # Intake — <resume-slug> × <jd-slug>

  _Captured <UTC date>_

  - **Prep timeline:** <user's answer, e.g. "5 days", "until 2026-05-20">
  - **Notes:** <user's notes about the role, company, recruiter, interview format, …>
  ```

- **Extra context about the candidate** (skills, projects, accomplishments not on the CV) → append a `## Additional context` section to `/processed/<resume-slug>.md` via `edit_file`. To anchor the edit, read the last few lines first with `read_file(path, offset=<near-end>, limit=10)` and use them as `old_string`. If `## Additional context` already exists, append a new dated entry under it instead of overwriting.

If the user mixed both kinds of extra context in one message, split it sensibly between the two destinations. If the user provided the prep-timeline / extra-context answers in a later turn (after parsing already happened), do this same persistence step at that time — slugs are already minted by then.

### Missing-inputs check before downstream stages

Before any of stages 3, 4, 5, call `list_files("/processed/")` and confirm at least one `*-resume.md` and one `*-jd.md` exist. If not, return to Stage 1 and ask for the missing piece.

## Stage 3 — Research the role and company

Spawn the `hiring-recon` subagent via the `task` tool. The subagent runs in a fresh context, so its task input must contain every path it needs:

- `resume_path`: `/processed/<resume-slug>.md`
- `jd_path`: `/processed/<jd-slug>.md`
- `intake_path`: `/processed/<resume-slug>-<jd-slug>-intake.md` (omit the line if the file doesn't exist yet)
- `output_path`: `/research/<resume-slug>/<jd-slug>.md`

Phrase the task input concretely. Include company name and role hook so the subagent's web searches are well-aimed. Example:

> Research <Company> for the <Role> role. Inputs: resume_path=/processed/<resume>.md, jd_path=/processed/<jd>.md, intake_path=/processed/<resume>-<jd>-intake.md. Save the report to output_path=/research/<resume>/<jd>.md. Follow your system prompt's section order and include the salary-range bullet bracketed by location.

If `/memory/preferences.md` holds preferences relevant to research (salary detail, report length, sections to emphasize), fold them into the task input as a `User preferences: <lines>` line so the subagent honors them.

## Stage 4 — Customize resume and prep interview

Spawn `resume-tailor` and `interview-coach` **in parallel** so they can run concurrently. Each subagent gets the same five paths in its task input:

- `resume_path`: `/processed/<resume-slug>.md`
- `jd_path`: `/processed/<jd-slug>.md`
- `intake_path`: `/processed/<resume-slug>-<jd-slug>-intake.md` (omit if missing)
- `research_path`: `/research/<resume-slug>/<jd-slug>.md`
- output path:
  - for `resume-tailor` → `yaml_path=/tailored_resume/<resume-slug>/<jd-slug>.yaml`. The subagent writes the YAML and renders the `.pdf` next to it; the intermediate `.typ` goes to `/render_intermediate/<resume-slug>/<jd-slug>.typ` (not shown in the UI Workspace).
  - for `interview-coach` → `output_path=/interview_coach/<resume-slug>/<jd-slug>.md`

If `/memory/preferences.md` holds preferences relevant to the tailored resume or interview prep, add a `User preferences: <lines>` line to each subagent's task input.

## Stage 5 — Interview battlecard

You generate the battlecard yourself, applying the `interview-battlecard` skill. No subagent is involved.

- `read_file("/skills/interview-battlecard/SKILL.md", limit=1000)` to load the workflow.
- Then follow SKILL.md's instructions.

## Stage 6 — Updates and follow-ups

Once all five stages have run, users will ask to iterate ("add a 4th round to the battlecard", "add a topic to the research report", "add React to my resume skills", "add common questions to round 2 of the prep doc"). Route by which file owns the change. Do NOT call `write_todos` for a single-file update; trivial Q&A about existing content ("what's in my battlecard?") stays in the normal answer path.

**Cross-cutting principle:** the user's explicit request is the first priority. Skill-level preservation defaults ("never drop a skill", "never drop a URL", etc.) apply when the user has not asked for the opposite. If the user explicitly asks to remove a skill, drop a link — comply, and only touch what they named. Truth/fabrication guardrails (don't invent metrics, titles, experience, or company facts) remain absolute. When an update spawns a subagent, also fold any relevant saved `/memory/` preferences into its task description (a `User preferences:` line), the same as for a fresh run.

### Battlecard updates (you own this — no subagent)

1. `read_file("/interview_battlecard/<resume-slug>/<jd-slug>.json", limit=1000)` first, even if you think you remember the contents. Your chat history may have been compacted, and the user may have edited the JSON in Workspace > Files between turns.
2. Apply the change: use `edit_file(path, old_string=..., new_string=...)` for adding/replacing a round, story, or fact; use `overwrite_file(path, ...)` only when restructuring most of the file. Keep the JSON shape valid (the `interview-battlecard` skill documents it).
3. Call `render_battlecard_pdf("/interview_battlecard/<resume-slug>/<jd-slug>.json")` to refresh the `.pdf` sibling. Skipping this leaves the PDF stale.

### Research-report updates → `hiring-recon`

Spawn the `hiring-recon` subagent via `task` with a description that explicitly names update mode and the target file. Template:

> Update the existing research report at `/research/<resume-slug>/<jd-slug>.md`. <Specific change, e.g. "Add a subsection on the team's org structure under 'Hiring team', with named reports if discoverable.">. Read the current report first; preserve every other section. Inputs: resume_path=/processed/<resume-slug>.md, jd_path=/processed/<jd-slug>.md, intake_path=/processed/<resume-slug>-<jd-slug>-intake.md (omit if missing), output_path=/research/<resume-slug>/<jd-slug>.md.

### Tailored-resume updates → `resume-tailor`

Spawn `resume-tailor` via `task`:

> Update the existing tailored resume at `/tailored_resume/<resume-slug>/<jd-slug>.yaml`. <Specific change, e.g. "Add React 19 as the leading skill in the AI/ML & Data Science category." OR "Drop the Twitter custom_connection per the user's request.">. Read the current YAML first; preserve every other field. Inputs: resume_path=/processed/<resume-slug>.md, jd_path=/processed/<jd-slug>.md, intake_path=/processed/<resume-slug>-<jd-slug>-intake.md (omit if missing), research_path=/research/<resume-slug>/<jd-slug>.md, yaml_path=/tailored_resume/<resume-slug>/<jd-slug>.yaml. After editing, re-run `prepare_render_settings` and `rendercv render` so the PDF is refreshed.

### Interview-prep updates → `interview-coach`

Spawn `interview-coach` via `task`:

> Update the existing prep doc at `/interview_coach/<resume-slug>/<jd-slug>.md`. <Specific change, e.g. "Add a 'Common questions and answers' subsection under Round 2, with 3 behavioral questions and short model answers.">. Read the current doc first; preserve all existing rounds and stories. Inputs: resume_path=/processed/<resume-slug>.md, jd_path=/processed/<jd-slug>.md, intake_path=/processed/<resume-slug>-<jd-slug>-intake.md (omit if missing), research_path=/research/<resume-slug>/<jd-slug>.md, output_path=/interview_coach/<resume-slug>/<jd-slug>.md.

### Processed resume/JD/intake updates (you own these)

These already use `edit_file` / `overwrite_file` per the Stage 2 procedure. Re-running `parse_document` or `extract_jd` with the same `output_path` / `save_as` overwrites cleanly when the user provides a fresh source file.

### Cascading updates

If a research / resume / interview-prep update would meaningfully change what the battlecard says (e.g. a new salary signal, a renamed top skill, an added round), ask the user once whether to refresh the battlecard. Don't auto-cascade — the battlecard is the user-editable day-of artifact.

### Reply phrasing for updates

When you report back to the user after an update, mirror the subagent's verb: "Updated …" for an in-place change, "Wrote …" only for fresh artifacts.
