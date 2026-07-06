"""Tests for the SUBAGENT_OUTPUT_MODE="full_history" middleware pair.

Covers the transcript recorder (subagent side), the tool-call index update
(parent side), and the transcript splicer — especially its message-protocol
guarantee: transcripts land only after a turn's complete tool-response block,
never between sibling tool responses of parallel `task` calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from backend.agents.career_agent.middleware import (
    SubagentFullHistoryMiddleware,
    SubagentTranscriptRecorder,
    splice_subagent_transcripts,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command


def _task_call(call_id: str, subagent: str) -> dict[str, Any]:
    return {"name": "task", "args": {"subagent_type": subagent}, "id": call_id, "type": "tool_call"}


def _transcript(text: str) -> list[dict[str, Any]]:
    return [
        HumanMessage(content="the task brief").model_dump(),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "/x"},
                    "id": f"sub-{text}",
                    "type": "tool_call",
                },
            ],
        ).model_dump(),
        ToolMessage(content="file body", tool_call_id=f"sub-{text}").model_dump(),
        AIMessage(content=text).model_dump(),
    ]


class TestSplice:
    """splice_subagent_transcripts."""

    def test_noop_without_recorded_state(self):
        """Without transcript state the message list passes through untouched."""
        messages = [HumanMessage(content="hi"), AIMessage(content="yo")]
        assert splice_subagent_transcripts(messages, {}) == messages

    def test_single_task_turn_inserts_after_tool_response(self):
        """A lone task call gets its transcript right after its ToolMessage."""
        messages = [
            AIMessage(content="", tool_calls=[_task_call("c1", "hiring-recon")]),
            ToolMessage(content="done", tool_call_id="c1"),
            AIMessage(content="final"),
        ]
        state = {
            "subagent_transcripts": {"tools:t1": _transcript("report written")},
            "subagent_transcript_index": {"c1": {"ns": "tools:t1", "name": "hiring-recon"}},
        }

        out = splice_subagent_transcripts(messages, state)

        kinds = [(m.type, getattr(m, "name", None)) for m in out]
        assert kinds == [
            ("ai", None),
            ("tool", None),
            ("ai", "hiring-recon"),
            ("tool", None),
            ("ai", "hiring-recon"),
            ("ai", None),
        ]
        assert out[2].tool_calls[0]["name"] == "read_file"

    def test_parallel_tasks_keep_tool_block_contiguous(self):
        """Transcripts must not appear between sibling tool responses."""
        messages = [
            AIMessage(
                content="",
                tool_calls=[_task_call("c1", "resume-tailor"), _task_call("c2", "interview-coach")],
            ),
            ToolMessage(content="done-1", tool_call_id="c1"),
            ToolMessage(content="done-2", tool_call_id="c2"),
            AIMessage(content="final"),
        ]
        state = {
            "subagent_transcripts": {
                "tools:t1": _transcript("tailored"),
                "tools:t2": _transcript("prepped"),
            },
            "subagent_transcript_index": {
                "c1": {"ns": "tools:t1", "name": "resume-tailor"},
                "c2": {"ns": "tools:t2", "name": "interview-coach"},
            },
        }

        out = splice_subagent_transcripts(messages, state)

        # Both original ToolMessages stay adjacent, directly after the AI turn.
        assert [m.type for m in out[:3]] == ["ai", "tool", "tool"]
        # Then both transcripts (3 messages each after dropping the brief), then the final AI.
        assert [m.type for m in out[3:]] == ["ai", "tool", "ai", "ai", "tool", "ai", "ai"]
        assert out[3].name == "resume-tailor"
        assert out[6].name == "interview-coach"

    def test_transcript_drops_leading_task_brief(self):
        """The stored task-brief HumanMessage never reaches the model context."""
        state = {
            "subagent_transcripts": {"tools:t1": _transcript("x")},
            "subagent_transcript_index": {"c1": {"ns": "tools:t1", "name": "s"}},
        }
        messages = [
            AIMessage(content="", tool_calls=[_task_call("c1", "s")]),
            ToolMessage(content="done", tool_call_id="c1"),
        ]

        out = splice_subagent_transcripts(messages, state)

        assert all(m.type != "human" for m in out)


class TestRecorder:
    """SubagentTranscriptRecorder._export."""

    def test_exports_messages_keyed_by_namespace(self):
        """A finished subagent exports its messages under its trimmed namespace."""
        state = {"messages": [AIMessage(content="did the thing")]}
        with patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value={"configurable": {"checkpoint_ns": "tools:abc"}},
        ):
            update = SubagentTranscriptRecorder._export(state)  # noqa: SLF001

        assert update is not None
        assert list(update["subagent_transcripts"].keys()) == ["tools:abc"]
        assert update["subagent_transcripts"]["tools:abc"][0]["content"] == "did the thing"

    def test_no_namespace_means_no_export(self):
        """Outside a namespaced run the recorder stays silent."""
        state = {"messages": [AIMessage(content="x")]}
        with patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value={"configurable": {"checkpoint_ns": ""}},
        ):
            assert SubagentTranscriptRecorder._export(state) is None  # noqa: SLF001


class _FakeRequest:
    def __init__(self, tool_call: dict[str, Any]) -> None:
        self.tool_call = tool_call


class TestIndexUpdate:
    """SubagentFullHistoryMiddleware._index_update."""

    def test_tool_message_result_becomes_command_with_index(self):
        """A plain ToolMessage result is wrapped into a Command carrying the index."""
        request = _FakeRequest(_task_call("c1", "hiring-recon"))
        result = ToolMessage(content="done", tool_call_id="c1")
        with patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value={"configurable": {"__pregel_task_id": "t1"}},
        ):
            out = SubagentFullHistoryMiddleware._index_update(request, result)  # noqa: SLF001

        assert isinstance(out, Command)
        assert out.update["messages"] == [result]
        assert out.update["subagent_transcript_index"]["c1"] == {
            "ns": "tools:t1",
            "name": "hiring-recon",
        }

    def test_command_result_keeps_existing_update(self):
        """Existing Command update keys survive the index merge."""
        request = _FakeRequest(_task_call("c1", "s"))
        result = Command(update={"messages": ["m"], "todos": ["x"]})
        with patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value={"configurable": {"__pregel_task_id": "t9"}},
        ):
            out = SubagentFullHistoryMiddleware._index_update(request, result)  # noqa: SLF001

        assert out.update["todos"] == ["x"]
        assert out.update["subagent_transcript_index"]["c1"]["ns"] == "tools:t9"

    def test_non_task_tools_pass_through(self):
        """Non-task tools are returned unchanged."""
        request = _FakeRequest({"name": "read_file", "args": {}, "id": "c9"})
        result = ToolMessage(content="x", tool_call_id="c9")

        assert SubagentFullHistoryMiddleware._index_update(request, result) is result  # noqa: SLF001
