"""Prompt wiring and the assembled system prompt for the analytics agent.

The snapshot test builds the real graph with a recording fake chat model — no
network, no API calls — and pins the exact system message and tool set the model
would receive. It is the tripwire for deepagents' override-by-name wiring: an
upstream rename that demoted the Filesystem or Memory replacement instances to
the "custom" slot would silently drop this agent's file and memory prompts.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class _RecordingFakeModel(BaseChatModel):
    """Fake chat model that records each request and replies with plain text."""

    captured_messages: list = Field(default_factory=list)
    captured_tools: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def bind_tools(self, tools, **kwargs):
        """Carry tools through bind kwargs so `_generate` sees them per request."""
        return self.bind(tools=list(tools), **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """Record the request and reply with a fixed message."""
        self.captured_messages.append(list(messages))
        self.captured_tools.append(kwargs.get("tools") or [])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def _tool_names(tools) -> set[str]:
    """Extract tool names whether bound as BaseTool objects or OpenAI-style dicts."""
    names = set()
    for tool in tools:
        if isinstance(tool, dict):
            names.add((tool.get("function") or {}).get("name") or tool.get("name"))
        else:
            names.add(getattr(tool, "name", None))
    return {name for name in names if name}


def _tool_description(tools, name: str) -> str:
    """Return one tool's description from a bound tool list."""
    for tool in tools:
        if isinstance(tool, dict):
            function = tool.get("function") or {}
            if function.get("name") == name or tool.get("name") == name:
                return function.get("description") or tool.get("description") or ""
        elif getattr(tool, "name", None) == name:
            return getattr(tool, "description", "")
    return ""


def _build(monkeypatch):
    """Build the real graph offline with a recording model."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from backend.agents.analytics_agent.agents import build_analytics_agent
    from langgraph.store.memory import InMemoryStore

    fake = _RecordingFakeModel()
    agent = build_analytics_agent(model=fake, store=InMemoryStore())
    agent.invoke({"messages": [HumanMessage(content="hi")]})
    assert fake.captured_messages, "fake model was never called"
    return fake


# ---------------------------------------------------------------------------
# prompt constants
# ---------------------------------------------------------------------------


def test_memory_prompt_formats_with_only_agent_memory_placeholder() -> None:
    """`MEMORY` must format cleanly with just `agent_memory` — no stray braces."""
    from backend.agents.analytics_agent import prompts

    rendered = prompts.MEMORY.format(agent_memory="SENTINEL")

    assert "SENTINEL" in rendered
    assert "<agent_memory>" in rendered


def test_system_prompt_states_the_privacy_boundary() -> None:
    """The agent must know document bodies never reached the warehouse."""
    from backend.agents.analytics_agent import prompts

    assert "never document bodies" in prompts.SYSTEM_PROMPT


def test_system_prompt_tells_the_agent_to_work_alone() -> None:
    """Deepagents adds a general-purpose subagent; the prompt keeps it unused."""
    from backend.agents.analytics_agent import prompts

    assert "You work alone" in prompts.SYSTEM_PROMPT


def test_execute_guardrail_points_at_the_no_approval_path() -> None:
    """The Python fallback must read as a fallback, not the default route."""
    from backend.agents.analytics_agent import prompts

    assert "Prefer `create_chart`" in prompts.EXECUTE_GUARDRAIL
    assert "without database credentials" in prompts.EXECUTE_GUARDRAIL


# ---------------------------------------------------------------------------
# assembled graph
# ---------------------------------------------------------------------------


def test_todos_channel_registered(monkeypatch) -> None:
    """TodoListMiddleware is opt-in since 0.7; the frontend Plan panel needs `todos`."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from backend.agents.analytics_agent.agents import analytics_agent

    assert "todos" in analytics_agent.channels


def test_assembled_prompt_snapshot(monkeypatch) -> None:
    """Pin the sections the model receives, from every middleware in the stack."""
    fake = _build(monkeypatch)
    system_text = fake.captured_messages[0][0].text

    expected_sections = (
        "You are NextRole's analytics agent",
        "## What the warehouse holds",
        "## How to work",
        "## How to answer",
        "## `write_todos`",
        "## Skills System",
        "## File tools",
        "<agent_memory>",
        "Current UTC date:",
    )
    for expected in expected_sections:
        assert expected in system_text, f"missing section: {expected}"


def test_the_memory_file_reaches_the_model(monkeypatch) -> None:
    """`ANALYTICS_AGENT.md` is loaded every turn, not read on demand."""
    fake = _build(monkeypatch)
    system_text = fake.captured_messages[0][0].text

    assert "owner = 'default'" in system_text
    assert "Never `FINAL`" in system_text


def test_the_sql_rules_that_cause_errors_are_always_in_context(monkeypatch) -> None:
    """The dialect traps live in memory, not behind skill discovery.

    A model that never opens the skill still has to get these right, and both
    have been observed failing in practice — so they load every turn rather
    than waiting to be discovered.
    """
    fake = _build(monkeypatch)
    # The memory file is hard-wrapped, so a phrase can span a line break.
    system_text = " ".join(fake.captured_messages[0][0].text.split())

    # Aliasing over a column name nests aggregates (ClickHouse code 184).
    assert "Never alias an expression to a name that is already a column" in system_text
    # Stated generically: a column-specific example got read as a fact about
    # that column, and models then broke the same rule on a different one.
    assert "`sum(x) AS x`" in system_text
    assert "Check **every** aliased aggregate in the select list" in system_text
    # The same error code surfaces as "found in WHERE" when the shadowed name
    # is used there — where moving to HAVING would silently change the answer.
    assert '"found in WHERE" is usually the same alias problem' in system_text
    assert "quietly answer a different question" in system_text
    # Guessed function names: `toDayOfWeek` exists, so `toDayName` looks plausible.
    assert "Do not invent a `toXxx` function" in system_text
    # ClickHouse will not widen integer types across a join, and `ON` needs a
    # real key — both reached by generating a scaffold table and joining to it.
    assert "Joins are strict" in system_text
    assert "Prefer grouping the real rows to building a scaffold" in system_text


def test_the_prompt_gates_the_first_query_on_reading_the_skill(monkeypatch) -> None:
    """Reading the workflow is a precondition, not a suggestion."""
    fake = _build(monkeypatch)
    system_text = fake.captured_messages[0][0].text

    assert "Read the `warehouse-analysis` skill before your first query" in system_text
    assert "Confidence is not evidence here" in system_text


def test_both_skills_are_discoverable(monkeypatch) -> None:
    """Skill names and descriptions load into the prompt at startup."""
    fake = _build(monkeypatch)
    system_text = fake.captured_messages[0][0].text

    assert "warehouse-analysis" in system_text
    assert "charts" in system_text


def test_tool_surface_snapshot(monkeypatch) -> None:
    """The exact tools bound to the model, including the deepagents built-ins."""
    fake = _build(monkeypatch)

    names = _tool_names(fake.captured_tools[0])

    assert {"describe_data", "run_sql", "create_chart"} <= names
    assert {"ls", "read_file", "write_file", "edit_file", "glob", "grep"} <= names
    assert {"execute", "write_todos"} <= names
    # The career agent's document tools must not leak into this agent.
    assert not names & {"parse_document", "extract_jd", "render_battlecard_pdf", "web_search"}


def test_no_declarative_subagents_are_registered(monkeypatch) -> None:
    """Delegation is off the table for this agent.

    deepagents still adds its general-purpose subagent, which is left in place
    deliberately: the only off switch is a process-global profile registry keyed
    by model spec, and flipping it would strip the career agent's too. The
    system prompt keeps it unused instead.
    """
    fake = _build(monkeypatch)

    task_description = _tool_description(fake.captured_tools[0], "task")
    assert "general-purpose" in task_description
    for career_subagent in ("hiring-recon", "resume-tailor", "interview-coach"):
        assert career_subagent not in task_description


def test_execute_carries_the_analytics_guardrail(monkeypatch) -> None:
    """The stock execute description is extended, not replaced."""
    fake = _build(monkeypatch)

    description = _tool_description(fake.captured_tools[0], "execute")

    assert "Prefer `create_chart`" in description
    # The stock deepagents description is still there, ahead of the guardrail.
    assert description.startswith("Executes a shell command")


def test_the_model_default_matches_the_career_agent(monkeypatch) -> None:
    """Both agents bake in the same model, so the UI's picker behaves the same."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from backend.agents.analytics_agent import agents as analytics
    from backend.agents.career_agent import agents as career

    assert analytics._MODEL == career._MODEL  # noqa: SLF001 — the constant under test
