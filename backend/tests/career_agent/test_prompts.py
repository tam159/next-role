"""Unit tests for the career-agent prompt wiring.

deepagents 0.7 replaced the old monkey-patched prompt constants with
constructor parameters (middleware override-by-name); these tests pin that
wiring. The assembled-prompt snapshot test builds the real graph with a
recording fake chat model — no network, no API calls — and asserts on the
exact system message and tool set the model would receive.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


def test_memory_prompt_formats_with_only_agent_memory_placeholder() -> None:
    """`MEMORY` must format cleanly with just `agent_memory` — no stray braces."""
    from backend.agents.career_agent import prompts

    rendered = prompts.MEMORY.format(agent_memory="SENTINEL_MEMORY_BODY")

    assert "SENTINEL_MEMORY_BODY" in rendered
    assert "<agent_memory>" in rendered
    assert "</agent_memory>" in rendered


def test_memory_prompt_keeps_required_placeholder() -> None:
    """`MemoryMiddleware` validates and formats `{agent_memory}`, so it must stay."""
    from backend.agents.career_agent import prompts

    assert "{agent_memory}" in prompts.MEMORY


def test_memory_sources_and_prompt_wired_via_constructor(monkeypatch) -> None:
    """The override MemoryMiddleware must carry our prompt and the `memory=` sources.

    Since deepagents 0.7 the memory prompt is a constructor parameter on the
    replacement instance (no kwdefaults patching). The instance's `sources`
    must stay identical to `_MEMORY_SOURCES` — that list also feeds `memory=`,
    whose only remaining job is making create_deep_agent construct the default
    middleware (at the cache-friendly tail) for ours to replace by name.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")  # model-client construction needs a key string

    from backend.agents.career_agent import agents, prompts
    from backend.agents.career_agent.middleware import PREFERENCES_PATH

    assert agents._MEMORY_SOURCES == ["CAREER_AGENT.md", PREFERENCES_PATH]  # noqa: SLF001
    assert agents._memory_middleware.sources == agents._MEMORY_SOURCES  # noqa: SLF001
    assert agents._memory_middleware.system_prompt == prompts.MEMORY  # noqa: SLF001


def test_task_prompt_override_reaches_subagent_middleware(monkeypatch) -> None:
    """The one remaining kwdefaults patch must keep feeding SubAgentMiddleware.

    create_deep_agent constructs SubAgentMiddleware internally without a
    `system_prompt` (its keyword-only default is None → no section at all), so
    the patched default is the only channel for the custom `## task` section.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    import backend.agents.career_agent.agents  # noqa: F401  # import applies the override
    import deepagents.middleware.subagents as sub
    from backend.agents.career_agent import prompts

    kwdefaults = sub.SubAgentMiddleware.__init__.__kwdefaults__ or {}
    assert kwdefaults["system_prompt"] == prompts.TASK


def test_todos_channel_registered(monkeypatch) -> None:
    """TodoListMiddleware is opt-in since 0.7; the frontend Plan panel needs `todos`."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from backend.agents.career_agent.agents import career_agent

    assert "todos" in career_agent.channels


class _RecordingFakeModel(BaseChatModel):
    """Fake chat model that records each request and replies with plain text."""

    captured_messages: list = Field(default_factory=list)
    captured_tools: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def bind_tools(self, tools, **kwargs):
        # Carry the tools through bind kwargs so `_generate` sees them per request.
        return self.bind(tools=list(tools), **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.captured_messages.append(list(messages))
        self.captured_tools.append(kwargs.get("tools") or [])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def _tool_names(tools) -> set[str]:
    """Extract tool names whether bound as BaseTool objects or OpenAI-style dicts."""
    names = set()
    for t in tools:
        if isinstance(t, dict):
            names.add((t.get("function") or {}).get("name") or t.get("name"))
        else:
            names.add(getattr(t, "name", None))
    return {n for n in names if n}


def _tool_description(tools, name: str) -> str:
    for t in tools:
        if isinstance(t, dict):
            fn = t.get("function") or {}
            if fn.get("name") == name or t.get("name") == name:
                return fn.get("description") or t.get("description") or ""
        elif getattr(t, "name", None) == name:
            return getattr(t, "description", "")
    return ""


def test_assembled_prompt_and_tools_snapshot(monkeypatch) -> None:
    """Build the real graph offline and pin the assembled system prompt + tools.

    This is the tripwire for the 0.7 override-by-name wiring: if an upstream
    rename ever demotes the Filesystem/Memory replacement instances to the
    custom slot (or drops a section), the assertions here fail before any
    LangSmith trace has to reveal it.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from backend.agents.career_agent.agents import build_career_agent
    from langgraph.store.memory import InMemoryStore

    fake = _RecordingFakeModel()
    agent = build_career_agent(model=fake, store=InMemoryStore())
    agent.invoke({"messages": [HumanMessage(content="hi")]})

    assert fake.captured_messages, "fake model was never called"
    system_text = fake.captured_messages[0][0].text

    # Authored front (Voice restored post-0.7-upgrade), then middleware sections.
    expected_sections = (
        "You are a career agent",
        "## Voice",
        "## How you work",
        "## `write_todos`",
        "## Skills System",
        "## File tools",
        "## Shell paths vs. virtual paths",
        "## `task` (subagent spawner)",
        "Available subagent types:",
        "<memory_guidelines>",
        "Current UTC date:",
    )
    for expected in expected_sections:
        assert expected in system_text, f"missing section: {expected}"

    # Lean on purpose — prose that 0.7 moved into tool schemas must not come back.
    removed_sections = ("## Following Conventions", "## Large Tool Results", "## Execute Tool")
    for gone in removed_sections:
        assert gone not in system_text, f"unexpectedly restored: {gone}"

    names = _tool_names(fake.captured_tools[0])
    expected_tools = {
        "write_todos",
        "task",
        "execute",
        "delete",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "list_files",
        "parse_document",
        "extract_jd",
        "render_battlecard_pdf",
    }
    assert expected_tools <= names, f"missing tools: {expected_tools - names}"
    # Retired: 0.7's write_file already has write-or-replace semantics.
    assert "overwrite_file" not in names

    execute_description = _tool_description(fake.captured_tools[0], "execute")
    assert "Do NOT use `execute` to create or edit files" in execute_description
