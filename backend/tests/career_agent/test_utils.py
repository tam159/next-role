"""Tests for `backend.app.career_agent.utils.load_subagents`."""

from pathlib import Path

import pytest
from backend.app.career_agent.utils import load_subagents


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "subagents.yaml"
    path.write_text(text)
    return path


def test_skills_passthrough(tmp_path: Path) -> None:
    """A `skills:` list on a subagent is passed through verbatim to the spec."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
hiring-recon:
  description: recon
  system_prompt: do the thing
  skills:
    - skills/hiring-recon/
    - skills/shared/
""",
    )

    [spec] = load_subagents(yaml_path)

    assert spec["skills"] == ["skills/hiring-recon/", "skills/shared/"]


def test_skills_absent_when_omitted(tmp_path: Path) -> None:
    """No `skills:` key in YAML -> no `skills` key in the resulting spec."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
plain-subagent:
  description: plain
  system_prompt: just be helpful
""",
    )

    [spec] = load_subagents(yaml_path)

    assert "skills" not in spec


def test_existing_fields_still_mapped(tmp_path: Path) -> None:
    """`model` and `tools` are still mapped alongside the new `skills` field."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
hiring-recon:
  description: recon
  model: openai:gpt-5.4-mini
  tools:
    - web_search
    - web_extract
  skills:
    - skills/hiring-recon/
  system_prompt: do the thing
""",
    )

    [spec] = load_subagents(yaml_path)

    assert spec["name"] == "hiring-recon"
    assert spec["description"] == "recon"
    assert spec["system_prompt"] == "do the thing"
    assert spec["model"] == "openai:gpt-5.4-mini"
    assert len(spec["tools"]) == 2
    assert spec["skills"] == ["skills/hiring-recon/"]


def test_unknown_tool_raises(tmp_path: Path) -> None:
    """An unknown tool name in YAML fails loud rather than silently dropping."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
bad:
  description: bad
  system_prompt: bad
  tools:
    - not_a_real_tool
""",
    )

    with pytest.raises(KeyError):
        load_subagents(yaml_path)
