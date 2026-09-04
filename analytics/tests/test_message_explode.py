"""Unit tests for the message explosion — token metrics out, content never."""

from nextrole_analytics.dlt_sources.transforms import explode_messages, shape_message

AI_CONTENT = "Here is your tailored resume draft… SECRET PROSE"

AI_MESSAGE = {
    "content": AI_CONTENT,
    "additional_kwargs": {},
    "response_metadata": {
        "model_name": "claude-sonnet-5",
        "model_provider": "anthropic",
        "finish_reason": "stop",
    },
    "type": "ai",
    "name": None,
    "id": "run-abc-123",
    "tool_calls": [
        {"name": "write_file", "args": {"path": "/x", "content": "SECRET ARGS"}, "id": "tc1"},
        {"name": "task", "args": {"prompt": "SECRET PROMPT"}, "id": "tc2"},
    ],
    "invalid_tool_calls": [],
    "usage_metadata": {
        "input_tokens": 350,
        "output_tokens": 240,
        "total_tokens": 590,
        "input_token_details": {"cache_creation": 200, "cache_read": 100},
        "output_token_details": {"reasoning": 64},
    },
}

TOOL_MESSAGE = {
    "content": "wrote 2 files: SECRET OUTPUT",
    "type": "tool",
    "name": "write_file",
    "id": "msg-1",
    "tool_call_id": "tc1",
    "status": "success",
}

HUMAN_MESSAGE = {
    "content": [{"type": "text", "text": "please tailor my CV — SECRET"}],
    "type": "human",
    "id": "msg-0",
}


def test_shape_message_extracts_usage_and_model():
    row = shape_message("t1", 2, AI_MESSAGE)
    assert row["type"] == "ai"
    assert row["model_name"] == "claude-sonnet-5"
    assert row["model_provider"] == "anthropic"
    assert row["input_tokens"] == 350
    assert row["output_tokens"] == 240
    assert row["total_tokens"] == 590
    assert row["cache_creation_tokens"] == 200
    assert row["cache_read_tokens"] == 100
    assert row["reasoning_tokens"] == 64
    assert row["tool_call_count"] == 2
    assert row["tool_call_names"] == "task,write_file"
    assert row["message_index"] == 2
    assert row["message_id"] == "run-abc-123"


def test_shape_message_never_ships_content_or_args():
    for message in (AI_MESSAGE, TOOL_MESSAGE, HUMAN_MESSAGE):
        row = shape_message("t1", 0, message)
        assert "content" not in row
        assert "SECRET" not in str(row)
    ai_row = shape_message("t1", 0, AI_MESSAGE)
    assert ai_row["content_length"] == len(AI_CONTENT)


def test_shape_message_content_length_for_part_lists():
    row = shape_message("t1", 0, HUMAN_MESSAGE)
    assert row["content_length"] == len("please tailor my CV — SECRET")
    assert row["input_tokens"] is None


def test_shape_message_tool_fields():
    row = shape_message("t1", 1, TOOL_MESSAGE)
    assert row["tool_call_id"] == "tc1"
    assert row["tool_status"] == "success"
    assert row["name"] == "write_file"


def test_explode_messages_indexes_and_skips_non_dicts():
    rows = list(explode_messages("t1", [HUMAN_MESSAGE, "corrupt", AI_MESSAGE]))
    assert [r["message_index"] for r in rows] == [0, 2]
    assert all(r["thread_id"] == "t1" for r in rows)


def test_explode_messages_tolerates_missing_list():
    assert list(explode_messages("t1", None)) == []
    assert list(explode_messages("t1", {"not": "a list"})) == []
