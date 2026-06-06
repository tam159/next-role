"""Unit tests for the career-agent prompt overrides.

Pure-string assertions — no model is constructed and no network is hit. The
`MEMORY` prompt is injected via `str.format(agent_memory=...)` by deepagents'
`MemoryMiddleware`, so any stray `{`/`}` in it would raise at runtime; the
format guard below catches that permanently.
"""

import ast
from pathlib import Path

_AGENTS_PY = Path(__file__).resolve().parents[2] / "app" / "career_agent" / "agents.py"


def test_memory_prompt_formats_with_only_agent_memory_placeholder() -> None:
    """`MEMORY` must format cleanly with just `agent_memory` — no stray braces."""
    from backend.app.career_agent import prompts

    rendered = prompts.MEMORY.format(agent_memory="SENTINEL_MEMORY_BODY")

    assert "SENTINEL_MEMORY_BODY" in rendered
    assert "<agent_memory>" in rendered
    assert "</agent_memory>" in rendered


def test_memory_prompt_keeps_required_placeholder() -> None:
    """`_format_agent_memory` calls `.format(agent_memory=...)`, so it must stay."""
    from backend.app.career_agent import prompts

    assert "{agent_memory}" in prompts.MEMORY


def test_memory_index_wired_as_second_memory_source() -> None:
    """`create_deep_agent(memory=[...])` must load both AGENTS.md and the index.

    Parsed from source via AST so the check needs no model/API key and runs no
    agent code.
    """
    tree = ast.parse(_AGENTS_PY.read_text())

    sources: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and getattr(node.func, "id", None) == "create_deep_agent"
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "memory" and isinstance(kw.value, ast.List):
                sources = [
                    el.value
                    for el in kw.value.elts
                    if isinstance(el, ast.Constant) and isinstance(el.value, str)
                ]

    from backend.app.career_agent.middleware import PREFERENCES_PATH

    assert sources, "create_deep_agent(memory=[...]) with string entries not found"
    assert "AGENTS.md" in sources
    # The literal wired in agents.py must match the path the ensure-middleware seeds.
    assert PREFERENCES_PATH in sources


def test_memory_prompt_override_reaches_the_middleware(monkeypatch) -> None:
    """`_apply_prompt_overrides` must make deepagents use our MEMORY prompt.

    Importing the agent applies the overrides. deepagents 0.6.1 reads
    `MEMORY_SYSTEM_PROMPT` as a bare module global, but 0.6.3+ also freezes it
    into `MemoryMiddleware.__init__`'s keyword-only `system_prompt` default —
    where a plain module-level `setattr` no longer reaches it. Whichever path
    the installed version uses, the effective prompt must be ours; otherwise the
    agent silently falls back to deepagents' generic memory guidelines (the
    regression this guards).
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")  # model-client construction needs a key string

    import backend.app.career_agent.agents  # noqa: F401  # import triggers _apply_prompt_overrides
    import deepagents.middleware.memory as mem
    from backend.app.career_agent import prompts

    kwdefaults = mem.MemoryMiddleware.__init__.__kwdefaults__ or {}
    if "system_prompt" in kwdefaults:  # deepagents 0.6.3+
        assert kwdefaults["system_prompt"] == prompts.MEMORY
    else:  # deepagents <= 0.6.1
        assert mem.MEMORY_SYSTEM_PROMPT == prompts.MEMORY
