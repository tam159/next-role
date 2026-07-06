"""Tests for `backend.agents.career_agent.utils.load_subagents`."""

from pathlib import Path
from typing import Any

import pytest
from backend.agents.career_agent.utils import load_subagents
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import tool


class _NoopMiddleware(AgentMiddleware):
    """Stub middleware for verifying threading into subagent specs."""

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        return handler(request)


@tool
def _fake_search(q: str) -> str:
    """Stub web_search."""
    return q


@tool
def _fake_extract(u: str) -> str:
    """Stub web_extract."""
    return u


@tool
def _fake_list_files(path: str) -> str:
    """Stub list_files (a default tool)."""
    return path


@tool
def _fake_overwrite(path: str, content: str) -> str:
    """Stub overwrite_file (a default tool)."""
    return f"{path}:{content}"


_TOOL_POOL = {
    "web_search": _fake_search,
    "web_extract": _fake_extract,
}

_DEFAULTS = [_fake_list_files, _fake_overwrite]


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

    [spec] = load_subagents(yaml_path, tools=_TOOL_POOL)

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

    [spec] = load_subagents(yaml_path, tools=_TOOL_POOL)

    assert "skills" not in spec


def test_tools_default_to_empty_list_without_defaults(tmp_path: Path) -> None:
    """No `tools:` in YAML and no `default_tools` -> explicit empty list.

    Guards against deepagents silently inheriting the main agent's tools
    (parse_document, extract_jd, …) for subagents that didn't ask for them.
    """
    yaml_path = _write_yaml(
        tmp_path,
        """\
no-tools:
  description: just text
  system_prompt: skill-only
""",
    )

    [spec] = load_subagents(yaml_path, tools=_TOOL_POOL)

    assert "tools" in spec
    assert spec["tools"] == []


def test_default_tools_included_for_every_subagent(tmp_path: Path) -> None:
    """`default_tools` get prepended to every subagent's resolved tools list."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
no-tools:
  description: skill-only
  system_prompt: do
with-tools:
  description: has opt-ins
  system_prompt: do
  tools:
    - web_search
""",
    )

    specs = load_subagents(yaml_path, tools=_TOOL_POOL, default_tools=_DEFAULTS)
    by_name = {s["name"]: s for s in specs}

    # Subagent with no `tools:` still gets the defaults.
    assert by_name["no-tools"]["tools"] == [_fake_list_files, _fake_overwrite]
    # Defaults come first; opt-ins concatenate after.
    assert by_name["with-tools"]["tools"] == [
        _fake_list_files,
        _fake_overwrite,
        _fake_search,
    ]


def test_existing_fields_still_mapped(tmp_path: Path) -> None:
    """`model`, `tools`, and `skills` are all mapped together."""
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

    [spec] = load_subagents(yaml_path, tools=_TOOL_POOL)

    assert spec["name"] == "hiring-recon"
    assert spec["description"] == "recon"
    assert spec["system_prompt"] == "do the thing"
    assert spec["model"] == "openai:gpt-5.4-mini"
    assert len(spec["tools"]) == 2
    assert spec["tools"] == [_fake_search, _fake_extract]
    assert spec["skills"] == ["skills/hiring-recon/"]


def test_default_middleware_threaded_into_every_subagent(tmp_path: Path) -> None:
    """`default_middleware` is attached to every subagent.

    The middleware runs inside the subagent's own model call. Without this,
    the main agent's middleware list is not inherited — declarative subagents
    get only what's in their spec.
    """
    yaml_path = _write_yaml(
        tmp_path,
        """\
a:
  description: a
  system_prompt: a
b:
  description: b
  system_prompt: b
""",
    )

    mw = _NoopMiddleware()
    specs = load_subagents(yaml_path, tools=_TOOL_POOL, default_middleware=[mw])

    for spec in specs:
        assert spec["middleware"] == [mw]


def test_no_middleware_key_when_default_middleware_absent(tmp_path: Path) -> None:
    """When `default_middleware` is omitted, no `middleware` key is added."""
    yaml_path = _write_yaml(
        tmp_path,
        """\
a:
  description: a
  system_prompt: a
""",
    )

    [spec] = load_subagents(yaml_path, tools=_TOOL_POOL)

    assert "middleware" not in spec


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
        load_subagents(yaml_path, tools=_TOOL_POOL)
