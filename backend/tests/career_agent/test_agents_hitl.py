"""Interrupt/resume flow tests for execute-tool human-in-the-loop.

Builds the real career-agent graph offline (scripted fake model, in-memory
store + checkpointer — no network) and drives the langgraph interrupt
lifecycle end to end: gated `execute` calls pause with the
HumanInTheLoopMiddleware payload the frontend renders, and
`Command(resume={"decisions": [...]})` approve/edit/reject paths behave as
the UI contract expects. Subagent propagation rides the same mechanism.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command
from pydantic import Field


class _ScriptedToolCallModel(BaseChatModel):
    """Fake chat model that replays a scripted queue of AIMessages in order."""

    script: list = Field(default_factory=list)
    captured: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):
        """Carry bound tools through so the graph wiring stays realistic."""
        return self.bind(tools=list(tools), **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.captured.append(list(messages))
        message = self.script.pop(0) if self.script else AIMessage(content="script exhausted")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _execute_call(command: str, call_id: str) -> dict:
    return {
        "name": "execute",
        "args": {"command": command},
        "id": call_id,
        "type": "tool_call",
    }


def _build_agent(monkeypatch, script: list):
    """Build the real graph with a scripted model and test-local persistence."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from backend.agents.career_agent.agents import build_career_agent
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    fake = _ScriptedToolCallModel(script=script)
    agent = build_career_agent(model=fake, store=InMemoryStore())
    # The server injects a checkpointer per run (never passed to
    # create_deep_agent); tests mirror that by attaching one post-compile.
    agent.checkpointer = InMemorySaver()
    return agent, fake


def _tool_message(result: dict, call_id: str) -> ToolMessage:
    matches = [
        m for m in result["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == call_id
    ]
    assert matches, f"no ToolMessage for {call_id}"
    return matches[-1]


def test_gated_execute_pauses_with_expected_payload(monkeypatch) -> None:
    """A non-allowlisted command raises ONE interrupt with the HITL payload."""
    agent, _ = _build_agent(
        monkeypatch,
        [AIMessage(content="", tool_calls=[_execute_call("id", "c1")])],
    )
    config = {"configurable": {"thread_id": "t-pause"}}

    result = agent.invoke({"messages": [HumanMessage(content="run id")]}, config)

    interrupts = result.get("__interrupt__")
    assert interrupts, "gated execute call did not interrupt"
    assert len(interrupts) == 1
    value = interrupts[0].value
    assert value["action_requests"][0]["name"] == "execute"
    assert value["action_requests"][0]["args"] == {"command": "id"}
    assert "Review it before it executes" in value["action_requests"][0]["description"]
    review = value["review_configs"][0]
    assert review["action_name"] == "execute"
    assert review["allowed_decisions"] == ["approve", "edit", "reject"]


def test_allowlisted_execute_runs_without_interrupt(monkeypatch) -> None:
    """The `when` predicate auto-approves read-only commands end to end."""
    agent, fake = _build_agent(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[_execute_call("echo auto-approved-ok", "c1")]),
            AIMessage(content="done"),
        ],
    )
    config = {"configurable": {"thread_id": "t-allow"}}

    result = agent.invoke({"messages": [HumanMessage(content="echo it")]}, config)

    assert not result.get("__interrupt__")
    assert "auto-approved-ok" in _tool_message(result, "c1").text
    assert len(fake.captured) == 2


def test_approve_runs_the_command(monkeypatch) -> None:
    """Resume with approve executes the original command."""
    agent, _ = _build_agent(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[_execute_call("id", "c1")]),
            AIMessage(content="done after approve"),
        ],
    )
    config = {"configurable": {"thread_id": "t-approve"}}

    result = agent.invoke({"messages": [HumanMessage(content="run id")]}, config)
    assert result.get("__interrupt__")

    resumed = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)

    assert not resumed.get("__interrupt__")
    assert "uid=" in _tool_message(resumed, "c1").text
    assert resumed["messages"][-1].text == "done after approve"


def test_reject_skips_execution_with_feedback(monkeypatch) -> None:
    """Resume with reject never runs the command and surfaces the message."""
    agent, _ = _build_agent(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[_execute_call("sleep 999", "c1")]),
            AIMessage(content="ok, skipped"),
        ],
    )
    config = {"configurable": {"thread_id": "t-reject"}}

    result = agent.invoke({"messages": [HumanMessage(content="sleep")]}, config)
    assert result.get("__interrupt__")

    resumed = agent.invoke(
        Command(
            resume={
                "decisions": [
                    {"type": "reject", "message": "User rejected — do not retry."},
                ],
            },
        ),
        config,
    )

    tool_message = _tool_message(resumed, "c1")
    assert tool_message.status == "error"
    assert "User rejected" in tool_message.text
    assert resumed["messages"][-1].text == "ok, skipped"


def test_edit_runs_the_edited_command(monkeypatch) -> None:
    """Resume with edit executes the human-edited args, not the original."""
    agent, _ = _build_agent(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[_execute_call("sleep 999", "c1")]),
            AIMessage(content="after edit"),
        ],
    )
    config = {"configurable": {"thread_id": "t-edit"}}

    result = agent.invoke({"messages": [HumanMessage(content="sleep")]}, config)
    assert result.get("__interrupt__")

    resumed = agent.invoke(
        Command(
            resume={
                "decisions": [
                    {
                        "type": "edit",
                        "edited_action": {
                            "name": "execute",
                            "args": {"command": "printf edited-ok"},
                        },
                    },
                ],
            },
        ),
        config,
    )

    assert "edited-ok" in _tool_message(resumed, "c1").text


def test_parallel_gated_calls_batch_into_one_interrupt(monkeypatch) -> None:
    """Two gated calls raise ONE interrupt needing two ordered decisions."""
    agent, _ = _build_agent(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[_execute_call("id", "c1"), _execute_call("sleep 999", "c2")],
            ),
            AIMessage(content="after parallel"),
        ],
    )
    config = {"configurable": {"thread_id": "t-parallel"}}

    result = agent.invoke({"messages": [HumanMessage(content="two calls")]}, config)

    interrupts = result.get("__interrupt__")
    assert interrupts
    assert len(interrupts) == 1
    assert len(interrupts[0].value["action_requests"]) == 2

    resumed = agent.invoke(
        Command(
            resume={
                "decisions": [
                    {"type": "approve"},
                    {"type": "reject", "message": "Second command rejected."},
                ],
            },
        ),
        config,
    )

    assert "uid=" in _tool_message(resumed, "c1").text
    assert _tool_message(resumed, "c2").status == "error"


def test_mixed_turn_interrupts_only_for_gated_calls(monkeypatch) -> None:
    """Allowlisted calls stay out of the batch; decisions count only gated ones."""
    agent, _ = _build_agent(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[
                    _execute_call("echo mixed-safe-ok", "c1"),
                    _execute_call("id", "c2"),
                ],
            ),
            AIMessage(content="after mixed"),
        ],
    )
    config = {"configurable": {"thread_id": "t-mixed"}}

    result = agent.invoke({"messages": [HumanMessage(content="mixed")]}, config)

    interrupts = result.get("__interrupt__")
    assert interrupts
    assert len(interrupts) == 1
    action_requests = interrupts[0].value["action_requests"]
    assert len(action_requests) == 1
    assert action_requests[0]["args"] == {"command": "id"}

    resumed = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)

    assert "mixed-safe-ok" in _tool_message(resumed, "c1").text
    assert "uid=" in _tool_message(resumed, "c2").text
    assert resumed["messages"][-1].text == "after mixed"


def test_subagent_execute_interrupt_propagates_to_root(monkeypatch) -> None:
    """A gated execute inside the general-purpose subagent pauses the root run."""
    agent, fake = _build_agent(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "Run `id` with the execute tool, then reply done.",
                            "subagent_type": "general-purpose",
                        },
                        "id": "task_1",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="", tool_calls=[_execute_call("id", "sub_c1")]),
            AIMessage(content="subagent done"),
            AIMessage(content="main done"),
        ],
    )
    config = {"configurable": {"thread_id": "t-subagent"}}

    result = agent.invoke({"messages": [HumanMessage(content="delegate")]}, config)

    interrupts = result.get("__interrupt__")
    assert interrupts, "subagent execute interrupt did not propagate to the root run"
    value = interrupts[0].value
    assert value["action_requests"][0]["name"] == "execute"
    assert value["action_requests"][0]["args"] == {"command": "id"}

    resumed = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)

    assert not resumed.get("__interrupt__")
    assert "subagent done" in _tool_message(resumed, "task_1").text
    assert resumed["messages"][-1].text == "main done"
    assert len(fake.captured) == 4


def test_subagent_allowlisted_execute_runs_without_interrupt(monkeypatch) -> None:
    """The `when` allowlist applies identically inside subagents (policy parity).

    Same read-only command, same verdict, main agent or subagent — e.g.
    `date -u '+%F'` auto-approves everywhere, while a variant with shell
    metacharacters (`date '+(%z)'`) gates everywhere.
    """
    agent, fake = _build_agent(
        monkeypatch,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "Echo a marker with the execute tool.",
                            "subagent_type": "general-purpose",
                        },
                        "id": "task_1",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="", tool_calls=[_execute_call("echo sub-allowlist-ok", "sub_c1")]),
            AIMessage(content="subagent done"),
            AIMessage(content="main done"),
        ],
    )
    config = {"configurable": {"thread_id": "t-subagent-allow"}}

    result = agent.invoke({"messages": [HumanMessage(content="delegate")]}, config)

    assert not result.get("__interrupt__")
    assert "subagent done" in _tool_message(result, "task_1").text
    assert result["messages"][-1].text == "main done"
    assert len(fake.captured) == 4


def test_kill_switch_disables_the_gate(monkeypatch) -> None:
    """CAREER_AGENT_EXECUTE_APPROVAL=false runs gated commands uninterrupted."""
    monkeypatch.setenv("CAREER_AGENT_EXECUTE_APPROVAL", "false")
    agent, _ = _build_agent(
        monkeypatch,
        [
            AIMessage(content="", tool_calls=[_execute_call("id", "c1")]),
            AIMessage(content="done"),
        ],
    )
    config = {"configurable": {"thread_id": "t-off"}}

    result = agent.invoke({"messages": [HumanMessage(content="run id")]}, config)

    assert not result.get("__interrupt__")
    assert "uid=" in _tool_message(result, "c1").text
