---
name: interview-battlecard
description: Apply this skill in Stage 5 to produce the day-of one-pager(s) the candidate carries into the interview. Reads the tailored resume + interview-coach prep + research report and emits a tight, scannable battlecard with one page per round.
---

# Interview Battlecard

## When to use

Stage 5 of the career-agent workflow, only after the tailored resume and the interview-coach prep doc both exist. The battlecard is a derivative — it does not introduce new content, it compresses what's already in those two files into something the candidate can scan in 60 seconds before walking into a room.

## Inputs (read these first)

The main agent passes the resume slug and JD slug as part of the orchestration context. Read all three at `limit=1000`:

- `/tailored_resume/<resume-slug>/<jd-slug>.md` — for the candidate's strongest claims and headline metrics.
- `/interview_coach/<resume-slug>/<jd-slug>.md` — for the round taxonomy, STAR stories (use the 60-second versions), questions to ask back, and watch-outs.
- `/research/<resume-slug>/<jd-slug>.md` — for the 3 punchiest company facts.

## Output format

A single markdown file at `/interview_battlecard/<resume-slug>/<jd-slug>.md`. Inside, **one page per interview round**, separated by exactly `\n---\n`. The round list and order must match the interview-coach prep doc; do not invent or skip rounds.

Each page is ~25–30 lines, four sections in this fixed order:

```
# <Round name> — <format>

## Stories ready
- **<Story title>** — <one-liner, ~15 sec, lifted from the prep doc>
- ...
(3–5 bullets)

## Company facts to drop
- <punchy fact 1, with a number or named person if possible>
- <punchy fact 2>
- <punchy fact 3>

## Questions to ask
- <one question, grounded in research>
- <one question, grounded in research>

## Watch-outs
- <risk + 1-line mitigation>
- <risk + 1-line mitigation>
```

## Hard rules

- **No preamble, no filler, no commentary.** No "here's your battlecard for…". The first byte of the file is `#`.
- **No headers beyond the four above.** No subheadings, no bullets explaining bullets.
- **Stories ready** uses the 15-second one-liner version distilled from the interview-coach prep's STAR. Don't paste full STAR.
- **Company facts** must be falsifiable (numbers, names, dates). "Innovative culture" is not a fact; "Series C, $80M raised Q3 2025 led by Sequoia" is.
- **Questions to ask** must reference something specific from the research file (named team member, recent product launch, a Glassdoor pattern). Generic questions like "what does success look like?" do not earn a slot.
- **Tight mode**: if the intake's prep timeline is `< 3 days`, drop the **Watch-outs** section from every page (in that order — Stories ready and Questions to ask are non-negotiable).

## Example skeleton (a 2-round battlecard)

```
# Recruiter screen — 30-min phone

## Stories ready
- **Migrated payments to Stripe in 6 weeks** — solo, 0 incidents, $2M throughput
- **Hired the first 3 engineers at Acme** — recruited, onboarded, scoped first sprint
- **Cut p99 latency 40%** — replaced N+1 ORM calls with a batched loader

## Company facts to drop
- Series C, $80M Q3 2025 led by Sequoia
- Hiring manager Maya Chen ex-Stripe payments lead, joined Jan 2026
- Recently launched Inferra v2 — embeddings infra at 1M req/s

## Questions to ask
- How is Maya's payments-infra background shaping this team's roadmap?
- The Inferra v2 launch was Q1 — what's the next infra bet?

## Watch-outs
- Glassdoor flags long hours during launches — ask about on-call cadence

---

# Hiring-manager behavioral — 60 min with Maya

## Stories ready
- ...
```

## Output handoff

After writing the file, return one short line to the user: "Battlecard saved to `/interview_battlecard/<resume>/<jd>.md` — N pages, one per round." Don't dump the markdown.
