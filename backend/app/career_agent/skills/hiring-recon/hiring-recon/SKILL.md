---
name: hiring-recon
description: Pre-interview reconnaissance. Given a processed resume, JD, and optional intake, gather public intel on the company (size, financials, reputation, hiring signals) and the role (market context, salary range bracketed by location), then produce a match analysis as a single markdown report.
---

# Hiring Recon

Pre-interview reconnaissance. After a CV and JD are processed, gather public intelligence on the company and the role and produce a match analysis the candidate can use to prepare.

## Inputs

The caller passes exact filesystem paths in the task description:

- `resume_path` — the candidate's processed resume (markdown)
- `jd_path` — the processed job description (markdown)
- `intake_path` — optional intake notes (may not exist)
- `output_path` — where to write the final report

Read every input file in full with `read_file(path, limit=1000)`. Do not skim. Note the candidate's location (from the resume) and the JD's location up front — both matter for salary calibration.

## Tools

- `web_search(query, max_results=5, topic="general"|"news"|"finance")` — primary research
- `web_extract(urls, content_format="markdown")` — pull a single high-value page (careers, About, press release, levels.fyi or local equivalent) when a search snippet is too thin
- filesystem tools (`read_file`, `overwrite_file`, `edit_file`, `ls`, `glob`, `grep`)

Plan 3-5 targeted searches, then optionally one or two `web_extract` calls. Quality over quantity.

## Research axes

1. **Company snapshot** — size, stage (startup / scale-up / public), business model (B2B vs B2C, product vs services/outsourcing), main products, recent news.
2. **Financial & hiring signals** — funding rounds, revenue/profitability hints, layoffs, headcount trend, recent leadership changes. Use `topic="news"` or `topic="finance"` for these.
3. **Reputation & culture** — Glassdoor patterns: report repeated themes in 1-2 star reviews (overwork, attrition, leadership) rather than just the average rating. Tenure signals, controversies.
4. **Hiring team** — if discoverable, who the hiring manager / team lead is and their background. Omit this section entirely if no signal.
5. **Role market context** — typical skills expected in this role family; JD-language red flags using the linguistic taxonomy ("wear many hats" → workload risk; "rockstar/ninja" → culture risk; "competitive salary" → likely below market). Include a **Salary range** bullet bracketed by location: `Senior <Role>, <region>: ~$X-$Y per <source>`. If candidate location and JD location differ, list both. Never quote a generic global number.
6. **Match analysis** — given the resume vs. the JD vs. company priorities, strengths to emphasize, gaps to address with adjacent experience. 3-5 bullets each.

## Output format

Write a single markdown file to `output_path` via `overwrite_file`, with these section headings, in this exact order:

```
# <Company> — <Role> recon

_Captured <UTC date>_

## Company snapshot
## Financial & hiring signals
## Reputation & culture
## Hiring team
## Role market context
- ...
- **Salary range** — <region>: ~$X-$Y/yr per [source](url)
## Match analysis
**Strengths to emphasize:**
- ...
**Gaps / mitigations:**
- ...
**Suggested emphasis (top 3-5):**
- ...
## Sources
- [Title](url)
```

Omit the "Hiring team" section entirely (no header) if you found no signal.

## Rules

- Cite URLs inline next to non-trivial claims. End with a `## Sources` list.
- If a fact is not findable, write `Unknown — no public signal` rather than fabricating.
- Keep total under ~600 lines. Punchy beats exhaustive.
- One file output only.
