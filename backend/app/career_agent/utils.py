"""Utils for the career agent."""

from pathlib import Path

import yaml
from langchain_core.tools import BaseTool


def load_subagents(
    config_path: Path,
    tools: dict[str, BaseTool],
    default_tools: list[BaseTool] | None = None,
) -> list:
    """Load subagent definitions from YAML and wire up tools by name.

    Each subagent's final tool list is `default_tools + <tools declared in YAML>`,
    name-resolved against the `tools` mapping (a `KeyError` surfaces on typos).
    The resulting spec always carries an explicit `tools` key so deepagents
    does not silently inherit the main agent's full tool set for subagents
    that don't declare any of their own.

    The tool pool is owned by the caller (typically `agents.py`) so backend-bound
    closures stay near their owner and the same instance can be shared between
    the main agent and any subagent that needs it.

    Args:
        config_path: Path to the YAML file defining subagents.
        tools: Name -> BaseTool pool that subagents may opt into via their
            `tools:` list in YAML.
        default_tools: BaseTools every subagent gets unconditionally — typically
            generic filesystem utilities (`list_files`, `overwrite_file`) that
            most subagents need but shouldn't have to re-declare per-entry.

    """
    with config_path.open() as f:
        config = yaml.safe_load(f)

    base: list[BaseTool] = list(default_tools or [])

    subagents = []
    for name, spec in config.items():
        opt_in = [tools[t] for t in spec.get("tools") or []]
        subagent = {
            "name": name,
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
            "tools": [*base, *opt_in],
        }
        if "model" in spec:
            subagent["model"] = spec["model"]
        if "skills" in spec:
            subagent["skills"] = spec["skills"]
        subagents.append(subagent)

    return subagents
