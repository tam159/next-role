# Career Agent

**Define an agent through three filesystem primitives:**

- **Memory** (`AGENTS.md`) – persistent context like brand voice and style guidelines
- **Skills** (`skills/*/SKILL.md`) – workflows for specific tasks, loaded on demand
- **Subagents** (`subagents.yaml`) – specialized agents for delegated tasks like research

## How It Works

The agent is configured by files:

```
career_agent/
├── AGENTS.md                    # Per-stage procedure (loaded as memory)
├── subagents.yaml               # Subagent definitions (hiring-recon, resume-tailor, interview-coach)
├── skills/                      # Per-consumer skill grouping (one source path per agent)
│   ├── career-agent/            # main agent
│   │   └── interview-battlecard/
│   │       └── SKILL.md         # Day-of one-pager workflow
│   ├── hiring-recon/            # hiring-recon subagent
│   │   └── hiring-recon/
│   │       └── SKILL.md
│   ├── resume-tailor/           # resume-tailor subagent
│   │   └── resume-tailor/
│   │       └── SKILL.md
│   └── interview-coach/         # interview-coach subagent
│       └── interview-coach/
│           └── SKILL.md
├── tools.py                     # Tools for the agents and subagents
└── utils.py                     # Utilities
```

The outer folder under `skills/` is the consumer grouping (a deepagents source path); the inner folder is the skill name (matches `name:` in frontmatter). Each consumer points at its own outer folder so its `SkillsMiddleware` only loads the skills it actually uses.


| File                | Purpose                              | When Loaded                  |
| ------------------- | ------------------------------------ | ---------------------------- |
| `AGENTS.md`         | Brand voice, tone, writing standards | Always (system prompt)       |
| `subagents.yaml`    | Research and other delegated tasks   | Always (defines `task` tool) |
| `skills/*/SKILL.md` | Content-specific workflows           | On demand                    |


## Architecture

The `memory` and `skills` parameters are handled natively by deepagents middleware. Tools are defined in the script and passed directly.

**Note on subagents:** Unlike `memory` and `skills`, subagents must be defined in code. We use a small `load_subagents()` helper to externalize config to YAML. Each subagent can also declare its own `skills:` paths — deepagents constructs a dedicated `SkillsMiddleware` per subagent, so skills do not propagate from the main agent. You can also define a subagent inline:

```python
subagents=[
    {
        "name": "researcher",
        "description": "Research topics before writing...",
        "model": "anthropic:claude-sonnet-4-6",
        "system_prompt": "You are a research assistant. Read the `web-research` skill for the full workflow, then execute it using user inputs.",
        "tools": [web_search],
        "skills": ["skills/researcher/"],
    }
],
```

**Flow:**

1. Agent asks user for resume, JD, prep timeline, and any extra context.
2.1. User uploads resume and optional JD → saves to `/upload/`.
2.2. Agent processes the uploaded documents → saves to `/processed/<slug>.md`. Persists intake answers to `/processed/<resume>-<jd>-intake.md`. Reads both processed files in full so it has substance for delegating downstream stages.
3. Delegates to `hiring-recon` subagent → company + role intel + match analysis → saves to `/research/<resume>/<jd>.md`.
4.1. Delegates to `resume-tailor` subagent → tailored resume → saves to `/tailored_resume/<resume>/<jd>.md`.
4.2. In parallel with 4.1, delegates to `interview-coach` subagent → structured prep doc with self-introduction + per-round STAR stories → saves to `/interview_coach/<resume>/<jd>.md`.
5. Agent loads `/skills/interview-battlecard/SKILL.md`, reads the tailored resume + interview-coach prep + research report, then writes a one-page-per-round battlecard → saves to `/interview_battlecard/<resume>/<jd>.md`.


## File Upload (v1)

Users upload raw resume / JD files (PDF, DOC, DOCX, TXT, MD; up to 10 MB each) from
the frontend in two places:

- **Chat composer** — paperclip icon in the message input.
- **Workspace > Files** — Upload button in the section header.

Both surfaces POST `multipart/form-data` to the Next.js route
`/api/files/upload`, which validates the path against the
`AGENT_FILE_SOURCES.career_agent.disk` allowlist
(`frontend/src/app/config/agentFiles.ts`) and writes bytes directly to
`backend/app/career_agent/upload/<filename>` via the shared `.:/deps/next-role`
volume mount in `docker-compose.yml`. The agent's `FilesystemBackend(root_dir=CAREER_AGENT_DIR)`
picks files up on the next tool call — no Python-side endpoint is involved.

Re-uploading the same filename overwrites. Scoping is global per the layout above
(no per-thread subdirectories).

## File Structure

```
/upload/                                                      # FilesystemBackend
└── Senior AI Engineer - Tam NGUYEN.pdf                       # Uploaded resume
└── AWS AI Solution Engineer.pdf                              # Uploaded JD

/processed/                                                   # StoreBackend
└── tam-nguyen-senior-ai-engineer-resume.md                   # Processed resume
└── aws-ai-solution-engineer-jd.md                            # Processed JD
└── tam-nguyen-senior-ai-engineer-resume-aws-ai-solution-engineer-jd-intake.md  # Intake (per resume×JD pair)

/research/                                                    # StoreBackend
└── tam-nguyen-senior-ai-engineer-resume/
    └── aws-ai-solution-engineer-jd.md                        # hiring-recon report

/tailored_resume/                                             # FilesystemBackend
└── tam-nguyen-senior-ai-engineer-resume/
    └── aws-ai-solution-engineer-jd.md                        # resume-tailor output

/interview_coach/                                             # StoreBackend
└── tam-nguyen-senior-ai-engineer-resume/
    └── aws-ai-solution-engineer-jd.md                        # interview-coach output

/interview_battlecard/                                        # FilesystemBackend
└── tam-nguyen-senior-ai-engineer-resume/
    └── aws-ai-solution-engineer-jd.md                        # day-of cheat sheet
```

Note: `.md` outputs reflect the current dev phase. PDF rendering for the tailored resume and battlecard is a future phase, which is also why those two artifacts use FilesystemBackend (so the eventual binary outputs land on disk, not in Postgres).
