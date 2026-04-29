# Career Agent

**This example demonstrates how to define an agent through three filesystem primitives:**

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
        "model": "anthropic:claude-haiku-4-5-20251001",
        "system_prompt": "You are a research assistant...",
        "tools": [web_search],
    }
],
```

**Flow:**

1. Agent receives task → loads relevant skill (custom-resume or interview-prep)
2. Delegates research to `researcher` subagent → saves to `research/`
3. Generates custom resume → saves to `custom-resume/`
4. Generates interview preparation → saves to `interview-prep/`
5. Generates interview cheat sheet → saves to `interview-cheat-sheet/`

## Output

```
research/
└── ai-engineer-role.md             # Research notes

custom-resume/
└── tam/
    ├── ai-engineer.md              # custom resume in md
    └── ai-engineer.pdf             # custom resume in pdf

interview-prep/
└── tam/
    ├── interview-preparation.md    # interview prep

interview-cheat-sheet/
└── tam/
    ├── interview-cheat-sheet.md    # interview cheat sheet
```
