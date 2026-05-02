# Career Agent

**Define an agent through three filesystem primitives:**

- **Memory** (`AGENTS.md`) – persistent context like brand voice and style guidelines
- **Skills** (`skills/*/SKILL.md`) – workflows for specific tasks, loaded on demand
- **Subagents** (`subagents.yaml`) – specialized agents for delegated tasks like research

## How It Works

The agent is configured by files:

```
career_agent/
├── AGENTS.md                    # Brand voice & style guide
├── subagents.yaml               # Subagent definitions
├── skills/
│   ├── custom-resume/
│   │   └── SKILL.md             # Customize resume
│   └── interview-prep/
│       └── SKILL.md             # Interview preparation
└── tools.py                     # Tools for the agents and subagents
└── utils.py                     # Utilities
```


| File                | Purpose                              | When Loaded                  |
| ------------------- | ------------------------------------ | ---------------------------- |
| `AGENTS.md`         | Brand voice, tone, writing standards | Always (system prompt)       |
| `subagents.yaml`    | Research and other delegated tasks   | Always (defines `task` tool) |
| `skills/*/SKILL.md` | Content-specific workflows           | On demand                    |


## Architecture

The `memory` and `skills` parameters are handled natively by deepagents middleware. Tools are defined in the script and passed directly.

**Note on subagents:** Unlike `memory` and `skills`, subagents must be defined in code. We use a small `load_subagents()` helper to externalize config to YAML. You can also define them inline:

```python
subagents=[
    {
        "name": "researcher",
        "description": "Research topics before writing...",
        "model": "anthropic:claude-sonnet-4-6",
        "system_prompt": "You are a research assistant...",
        "tools": [web_search],
    }
],
```

**Flow:**

1. User uploads CV and optional JD → saves to `upload/`
2. Agent processes the uploaded documents → saves to `/processed/`
3. Agent uderstands task → loads relevant skill
4. Agent plans tasks → uses `write_todos` tool
5. Delegates research to the subagent → saves to `/research/`
6. Generates custom resume → saves to `custom_resume/`
7. Generates interview preparation → saves to `/interview_prep/`
8. Generates interview cheat sheet → saves to `interview_cheat_sheet/`

## File Upload (v1)

Users upload raw CV / JD files (PDF, DOC, DOCX, TXT, MD; up to 10 MB each) from
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
upload/
└── Senior AI Engineer - Tam NGUYEN.pdf     # Uploaded resume
└── AWS AI Solution Engineer.pdf            # Uploaded JD

/processed/
└── tam-nguyen-senior-ai-engineer.md        # Processed resume
└── aws-ai-solution-engineer.md             # Processed JD

/research/
└── aws-ai-solution-engineer.md             # Research note

custom_resume/
└── tam-nguyen-senior-ai-engineer/
    └── aws-ai-solution-engineer.pdf        # tailored resume for specific job

/interview_prep/
└── tam-nguyen-senior-ai-engineer/
    ├── aws-ai-solution-engineer.md         # interview preperation

interview_cheat_sheet/
└── tam-nguyen-senior-ai-engineer/
    ├── aws-ai-solution-engineer.pdf        # interview cheat sheet
```
