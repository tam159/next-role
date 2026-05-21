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

## Rules

- Self-introduction lives at the top, ONCE, reused across rounds.
- Every STAR story must trace to the candidate's resume (or intake additions). No invention.
- Questions to ask back must reference specifics from the research (named team members, recent news, culture signals) — generic questions like "what's a typical day?" don't count.
- Keep total under ~800 lines.
- Single output file.
