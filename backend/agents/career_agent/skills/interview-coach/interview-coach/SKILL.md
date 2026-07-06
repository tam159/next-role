---
name: interview-coach
description: Produce a structured interview prep doc with a reusable self-introduction (60s + 30s) and per-round STAR stories, grounded in the resume, JD, intake (for round info and timeline), and hiring-recon report. Output is one markdown file.
---

# Interview Coach

Give the candidate everything they need to walk into the interview rounds prepared.

## Inputs

The caller passes exact filesystem paths in the task description:

- `resume_path` — the candidate's processed resume
- `jd_path` — the processed job description
- `intake_path` — optional intake notes (especially: prep timeline, interview process)
- `research_path` — the hiring-recon report
- `output_path` — where to write the prep doc

Read all four input files in full with `read_file(path, limit=1000)`.

## Determining the rounds

Check the intake for an interview process (e.g. "recruiter screen → hiring-manager → tech → onsite"). If present, prep every named round.

If no round info is given, default to a 3-round taxonomy:

1. Recruiter screen (logistics, fit, salary expectations)
2. Hiring-manager behavioral (leadership, problem-solving, motivation)
3. Technical / role-specific (deep skills check)

If the prep timeline is short (< 3 days), prepend a `## Triage` section recommending the 2 rounds to focus on.

## Output structure

```
# Interview prep — <Role> at <Company>

_Captured <UTC date>_

## Self-introduction

**60-second elevator pitch** (~120 words):
> <first-person: role identity → 2 strongest accomplishments → why this company/role specifically, informed by the research's Company snapshot>

**30-second short version** (~60 words):
> <same arc, less detail — used when interviewer says "keep it brief">

_These two versions are reused across every round. Do not rewrite per round._

## Triage         ← include only if prep timeline < 3 days
- Focus on: <Round X>, <Round Y>
- Reason: <one short line>

## Round-by-round prep

### Round 1 — <Name> (<format>)

**Likely question themes:**
- <Pick 2-3 from: Leadership / Problem-solving / Collaboration / Achievement / Failure & growth / Role-specific>

**STAR stories ready (3-5):**
1. **<Story title>** — maps to: <theme>
   - **Full (~2 min):** S — ... | T — ... | A — ... | R — ...
   - **Short (~60 sec):** <condensed>
2. ...

**Questions to ask back (2-3):**
- <question grounded in research's Hiring team / Reputation>
- ...

**Watch-outs:**
- <risk from research's Reputation & culture, and how to navigate>

### Round 2 — <Name> (<format>)
...
```

## Updates

When the caller's task says "Update the existing prep doc at …" (rather than create a new one):

1. `read_file(output_path, limit=1000)` first — your context is fresh; you have no memory of the prior doc. Note the existing rounds and the self-introduction.
2. Identify the surgical change the caller named. The user's explicit request takes priority over the structural defaults below — if they asked to add a round, add a "Common questions" subsection, drop a story, etc., do it, and only touch what they named.
3. Use `edit_file(output_path, old_string=..., new_string=...)` for targeted insertions (a new STAR story under a round, a new subsection, an updated question). Use `overwrite_file` only when restructuring most of the doc.
4. Preserve every existing round, story, and the self-introduction unless the user asked to change them. No-invention is absolute: any new STAR story must still trace to the candidate's resume or intake.
5. Reply with the update-mode contract: `Updated interview prep doc at: <output_path>`.

## Rules

- **Truth (absolute):** every STAR story must trace to the candidate's resume (or intake additions). No invention.
- **By default:** self-introduction lives at the top ONCE, reused across rounds; questions to ask back reference specifics from the research (named team members, recent news, culture signals); total stays under ~800 lines; output is a single file written to `output_path`. If the user explicitly asks for a different structure (e.g. per-round intros, generic question banks, multi-file split), comply.
