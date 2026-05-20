---
name: resume-tailor
description: Rewrite a candidate's resume tailored to one specific JD using a hiring-recon report. Reorders jobs/bullets and adjusts language to incorporate JD keywords without inventing experience. Output is one markdown resume.
---

# Resume Tailor

Rewrite the candidate's resume to land an interview for one specific role at one specific company, using the hiring-recon report as the priority signal.

## Inputs

The caller passes exact filesystem paths in the task description:

- `resume_path` — the candidate's processed resume (markdown)
- `jd_path` — the processed job description (markdown)
- `intake_path` — optional intake notes
- `research_path` — the hiring-recon report
- `output_path` — where to write the tailored resume

Read all four input files in full with `read_file(path, limit=1000)`.

## Tailoring philosophy

You are not lying or fabricating — you are highlighting the most relevant parts of the candidate's true experience. The original resume is the source of truth. Treat the JD's must-have keywords and the research report's "Match analysis" section as your priority signals.

## Three strategies

1. **Reorder jobs** if a less-recent role is more relevant to this JD.
2. **Swap bullet order** so the bullet most relevant to the JD leads each role.
3. **Adjust bullet language** to incorporate JD keywords naturally — keep all metrics, titles, dates, and certifications EXACTLY as in the source.

For the lead bullet of each role, prefer the transformation framing: `Inherited [situation] → Implemented [change] → Achieved [outcome]`.

## Truth-vs-tailoring guardrail

Acceptable:

- Reordering true information
- Emphasizing relevant experience
- Using industry-standard terminology
- Adding context to vague statements
- Matching language style to the JD

Unacceptable (do NOT do these):

- Adding skills the candidate doesn't have
- Changing numbers, metrics, or scope
- Inventing experiences or projects
- Claiming titles not held
- Inventing certifications

If the intake file mentions skills/projects not on the CV, you MAY incorporate them — but only those, and only as additions to existing roles. Never invent beyond what the candidate has told you.

## Output

Write a complete markdown resume to `output_path` matching the original's section order and heading style. End the file with an HTML comment block:

```
<!-- changes:
- reordered: <Role A> moved above <Role B> (more relevant)
- keywords added: <keyword>, <keyword>
- bullet rewrite (<Role>): "<before>" → "<after>"
-->
```

One bullet per change. Keep the comment under 20 lines.

## Rules

- Single output file. Do not write anywhere else.
- No web tools.
