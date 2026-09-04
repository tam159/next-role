"""Tests for the career-agent model-override middleware."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import SecretStr


class _FakeModel:
    """Minimal stand-in for a chat model that supports `model_copy`.

    Records `disable_streaming` so tests can assert the middleware disabled
    streaming via a copy — and never mutated the shared/cached instance.
    """

    def __init__(self, name: str = "base", *, disable_streaming: bool = False) -> None:
        self.name = name
        self.disable_streaming = disable_streaming

    def model_copy(self, *, update: dict) -> "_FakeModel":
        copy = _FakeModel(self.name, disable_streaming=self.disable_streaming)
        for key, value in update.items():
            setattr(copy, key, value)
        return copy


@pytest.fixture
def middleware():
    from backend.agents.career_agent.middleware import ModelOverrideMiddleware

    return ModelOverrideMiddleware()


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """Reset the module-level model cache between tests."""
    from backend.agents.career_agent import middleware as mw

    mw._MODEL_CACHE.clear()  # noqa: SLF001
    yield
    mw._MODEL_CACHE.clear()  # noqa: SLF001


def _fake_request(model=None, tools=None):
    """Minimal `ModelRequest` stand-in that records `override()` kwargs."""
    captured = {}

    def _override(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(captured=captured)

    return (
        SimpleNamespace(model=model or _FakeModel(), tools=tools or [], override=_override),
        captured,
    )


def _fake_handler(received: dict):
    def _h(request):
        received["request"] = request
        return "OK"

    return _h


def test_main_agent_override_invokes_init_chat_model(middleware):
    request, captured = _fake_request()
    received: dict = {}
    fake_model = _FakeModel(name="main-override")

    config = {
        "configurable": {"main_agent_model": "anthropic:claude-sonnet-5"},
        "metadata": {},
    }
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        result = middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("anthropic:claude-sonnet-5")
    # Main agent: plain swap, streaming left intact (no model_copy).
    assert captured["model"] is fake_model
    assert fake_model.disable_streaming is False
    assert result == "OK"


def test_subagent_override_disables_streaming_when_default_on(middleware, monkeypatch):
    """With the rollback default flipped on, a subagent override gets a no-stream copy."""
    monkeypatch.setattr(
        "backend.agents.career_agent.middleware.DISABLE_SUBAGENT_STREAMING",
        True,
    )
    request, captured = _fake_request()
    received: dict = {}
    fake_model = _FakeModel(name="subagent-override")

    config = {
        "configurable": {
            "main_agent_model": "anthropic:claude-sonnet-5",
            "subagent_model": "openai:gpt-5.6-luna",
        },
        "metadata": {"lc_agent_name": "hiring-recon"},
    }
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("openai:gpt-5.6-luna")
    overridden = captured["model"]
    assert overridden is not fake_model  # a model_copy, not the cached instance
    assert overridden.disable_streaming is True
    assert fake_model.disable_streaming is False  # shared/cached instance untouched


def test_subagent_streams_by_default(middleware):
    """Module default is `False` since the `@langchain/react` migration: pass-through."""
    req_model = _FakeModel(name="subagent-default")
    request, captured = _fake_request(model=req_model)
    received: dict = {}

    config = {"configurable": {}, "metadata": {"lc_agent_name": "resume-tailor"}}
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
        ) as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_not_called()  # no override name → keep the request's own model
    assert captured == {}  # no override, streaming left intact
    assert received["request"] is request
    assert req_model.disable_streaming is False


def test_subagent_streaming_can_be_reenabled_via_config(middleware):
    """`configurable.disable_subagent_streaming=False` keeps subagent streaming on."""
    req_model = _FakeModel(name="subagent-default")
    request, captured = _fake_request(model=req_model)
    received: dict = {}

    config = {
        "configurable": {"disable_subagent_streaming": False},
        "metadata": {"lc_agent_name": "resume-tailor"},
    }
    with (
        patch("backend.agents.career_agent.middleware.get_config", return_value=config),
        patch("backend.agents.career_agent.middleware.init_chat_model") as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_not_called()
    assert captured == {}  # no override, streaming left intact
    assert received["request"] is request
    assert req_model.disable_streaming is False


def test_reenabled_subagent_still_gets_model_override(middleware):
    """With streaming re-enabled, a `subagent_model` override still applies (no copy)."""
    request, captured = _fake_request()
    received: dict = {}
    fake_model = _FakeModel(name="subagent-override")

    config = {
        "configurable": {
            "subagent_model": "openai:gpt-5.6-luna",
            "disable_subagent_streaming": False,
        },
        "metadata": {"lc_agent_name": "hiring-recon"},
    }
    with (
        patch("backend.agents.career_agent.middleware.get_config", return_value=config),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("openai:gpt-5.6-luna")
    assert captured["model"] is fake_model  # plain override, not a streaming-disabled copy
    assert fake_model.disable_streaming is False


def test_module_default_on_disables_subagent_streaming(middleware, monkeypatch):
    """Flipping the `DISABLE_SUBAGENT_STREAMING` module default back on is the rollback."""
    monkeypatch.setattr(
        "backend.agents.career_agent.middleware.DISABLE_SUBAGENT_STREAMING",
        True,
    )
    req_model = _FakeModel(name="subagent-default")
    request, captured = _fake_request(model=req_model)
    received: dict = {}

    config = {"configurable": {}, "metadata": {"lc_agent_name": "resume-tailor"}}
    with (
        patch("backend.agents.career_agent.middleware.get_config", return_value=config),
        patch("backend.agents.career_agent.middleware.init_chat_model") as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_not_called()  # no override name → keep the request's own model
    overridden = captured["model"]
    assert overridden is not req_model
    assert overridden.disable_streaming is True
    assert req_model.disable_streaming is False  # copied, not mutated


def test_per_run_rollback_disables_streaming(middleware):
    """`configurable.disable_subagent_streaming=True` overrides the `False` default."""
    req_model = _FakeModel(name="subagent-default")
    request, captured = _fake_request(model=req_model)
    received: dict = {}

    config = {
        "configurable": {"disable_subagent_streaming": True},
        "metadata": {"lc_agent_name": "interview-coach"},
    }
    with (
        patch("backend.agents.career_agent.middleware.get_config", return_value=config),
        patch("backend.agents.career_agent.middleware.init_chat_model") as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_not_called()
    overridden = captured["model"]
    assert overridden is not req_model
    assert overridden.disable_streaming is True
    assert req_model.disable_streaming is False  # copied, not mutated


def test_no_configurable_passes_request_through(middleware):
    request, captured = _fake_request()
    received: dict = {}

    config: dict = {"configurable": {}, "metadata": {}}
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
        ) as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_not_called()
    assert captured == {}
    assert received["request"] is request


def test_empty_string_override_passes_request_through(middleware):
    request, captured = _fake_request()
    received: dict = {}

    config = {"configurable": {"main_agent_model": ""}, "metadata": {}}
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
        ) as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_not_called()
    assert captured == {}
    assert received["request"] is request


def test_invalid_model_string_falls_back_gracefully(middleware, caplog):
    request, captured = _fake_request()
    received: dict = {}

    config = {
        "configurable": {"main_agent_model": "not-a-real-provider:nope"},
        "metadata": {},
    }
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            side_effect=ValueError("unsupported provider"),
        ),
        caplog.at_level("WARNING"),
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    assert captured == {}
    assert received["request"] is request
    assert any("not-a-real-provider:nope" in r.message for r in caplog.records)


def test_resolved_model_is_cached(middleware):
    received: dict = {}
    fake_model = _FakeModel(name="cached")
    config = {
        "configurable": {"main_agent_model": "openai:gpt-5.6-terra"},
        "metadata": {},
    }
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        for _ in range(3):
            request, _captured = _fake_request()
            middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("openai:gpt-5.6-terra")


def test_get_config_outside_runnable_context_is_safe(middleware):
    request, captured = _fake_request()
    received: dict = {}

    with patch(
        "backend.agents.career_agent.middleware.get_config",
        side_effect=RuntimeError("outside runnable"),
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    assert captured == {}
    assert received["request"] is request


@pytest.mark.asyncio
async def test_async_path_also_overrides(middleware):
    request, captured = _fake_request()
    received: dict = {}
    fake_model = _FakeModel(name="async-main")

    async def _async_handler(r):
        received["request"] = r
        return "OK"

    config = {
        "configurable": {"main_agent_model": "openai:gpt-5.6-terra"},
        "metadata": {},
    }
    with (
        patch(
            "backend.agents.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ),
    ):
        await middleware.awrap_model_call(request, _async_handler)

    assert captured["model"] is fake_model
    assert fake_model.disable_streaming is False


# --- Anthropic fine-grained tool streaming ---------------------------------------------------


def _anthropic_model():
    from langchain_anthropic import ChatAnthropic

    # Offline: constructing the client never talks to the API.
    return ChatAnthropic(model="claude-sonnet-5", api_key="test-key")


def _write_file_tool():
    from langchain_core.tools import tool

    @tool
    def write_file(file_path: str, content: str) -> str:
        """Write `content` to `file_path`."""
        return "ok"

    return write_file


def _buffered_tool():
    """A tool that explicitly opted out of fine-grained streaming."""
    from langchain_core.tools import tool

    @tool(extras={"eager_input_streaming": False})
    def edit_file(file_path: str, old_string: str, new_string: str) -> str:
        """Edit a file."""
        return "ok"

    return edit_file


_BUILTIN_TOOL = {"type": "web_search_20260209", "name": "web_search"}


def test_anthropic_default_model_opts_tools_into_eager_streaming(middleware):
    """Tools bound to a `ChatAnthropic` request get `eager_input_streaming` copies."""
    write_file, buffered = _write_file_tool(), _buffered_tool()
    request, captured = _fake_request(
        model=_anthropic_model(),
        tools=[write_file, _BUILTIN_TOOL, buffered],
    )
    received: dict = {}

    config: dict = {"configurable": {}, "metadata": {}}
    with (
        patch("backend.agents.career_agent.middleware.get_config", return_value=config),
        patch("backend.agents.career_agent.middleware.init_chat_model") as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_not_called()
    assert set(captured) == {"tools"}  # model untouched, only the tools were swapped
    flagged, builtin, kept = captured["tools"]
    assert flagged is not write_file  # a copy…
    assert flagged.name == "write_file"  # …that the tool node still resolves by name
    assert flagged.extras == {"eager_input_streaming": True}
    assert write_file.extras is None  # shared instance stays provider-neutral
    assert builtin is _BUILTIN_TOOL  # provider built-ins pass through
    assert kept is buffered  # an explicit opt-out is respected
    assert kept.extras == {"eager_input_streaming": False}


def test_eager_flag_reaches_anthropic_tool_definition(middleware):
    """Contract tripwire: `langchain_anthropic` must lift the extra onto the wire tool def."""
    write_file, buffered = _write_file_tool(), _buffered_tool()
    model = _anthropic_model()
    request, captured = _fake_request(model=model, tools=[write_file, buffered])

    config: dict = {"configurable": {}, "metadata": {}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler({}))

    wire_tools = model.bind_tools(captured["tools"]).kwargs["tools"]
    assert wire_tools[0]["name"] == "write_file"
    assert wire_tools[0]["eager_input_streaming"] is True
    assert wire_tools[1]["name"] == "edit_file"
    assert wire_tools[1]["eager_input_streaming"] is False
    # Without the middleware the flag is absent → the API buffers whole parameters.
    assert "eager_input_streaming" not in model.bind_tools([write_file]).kwargs["tools"][0]


def test_anthropic_override_model_opts_tools_into_eager_streaming(middleware):
    """The flag keys off the *effective* model — here an Anthropic `main_agent_model` override."""
    write_file = _write_file_tool()
    request, captured = _fake_request(tools=[write_file])  # default model is not Anthropic
    anthropic_model = _anthropic_model()

    config = {"configurable": {"main_agent_model": "anthropic:claude-sonnet-5"}, "metadata": {}}
    with (
        patch("backend.agents.career_agent.middleware.get_config", return_value=config),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            return_value=anthropic_model,
        ),
    ):
        middleware.wrap_model_call(request, _fake_handler({}))

    assert captured["model"] is anthropic_model
    assert captured["tools"][0].extras == {"eager_input_streaming": True}
    assert write_file.extras is None


def test_anthropic_subagent_no_stream_copy_still_gets_eager_flag(middleware, monkeypatch):
    """With the rollback lever on, the no-stream copy is still recognised as Anthropic."""
    monkeypatch.setattr(
        "backend.agents.career_agent.middleware.DISABLE_SUBAGENT_STREAMING",
        True,
    )
    write_file = _write_file_tool()
    request, captured = _fake_request(model=_anthropic_model(), tools=[write_file])

    config = {"configurable": {}, "metadata": {"lc_agent_name": "resume-tailor"}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler({}))

    assert captured["model"].disable_streaming is True
    assert captured["tools"][0].extras == {"eager_input_streaming": True}


def test_non_anthropic_model_leaves_tools_alone(middleware):
    """OpenAI/Gemini requests keep the shared tool instances — no copies, no override."""
    write_file = _write_file_tool()
    request, captured = _fake_request(tools=[write_file])
    received: dict = {}

    config: dict = {"configurable": {}, "metadata": {}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler(received))

    assert captured == {}
    assert received["request"] is request
    assert write_file.extras is None


def test_anthropic_tools_already_flagged_skip_the_override(middleware):
    """Nothing to change (every tool already chose) → no `tools` override at all."""
    request, captured = _fake_request(model=_anthropic_model(), tools=[_buffered_tool()])
    received: dict = {}

    config: dict = {"configurable": {}, "metadata": {}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler(received))

    assert captured == {}
    assert received["request"] is request


# --- Claude on Bedrock: beta flag on the request body -----------------------------------------

_BETA = "fine-grained-tool-streaming-2025-05-14"


def _converse_model(model_id="global.anthropic.claude-sonnet-5", **kwargs):
    from langchain_aws import ChatBedrockConverse

    # Static creds → boto3 builds a client without touching the network.
    return ChatBedrockConverse(
        model=model_id,
        region_name="us-east-1",
        aws_access_key_id=SecretStr("test"),
        aws_secret_access_key=SecretStr("test"),
        **kwargs,
    )


def _legacy_bedrock_model(model_id="global.anthropic.claude-sonnet-5", **kwargs):
    from langchain_aws import ChatBedrock

    return ChatBedrock(
        model=model_id,
        region_name="us-east-1",
        aws_access_key_id=SecretStr("test"),
        aws_secret_access_key=SecretStr("test"),
        **kwargs,
    )


def test_bedrock_converse_claude_gets_fine_grained_beta(middleware):
    """Converse has no per-tool field: the beta flag rides `additional_model_request_fields`."""
    write_file = _write_file_tool()
    model = _converse_model(additional_model_request_fields={"reasoningConfig": {"type": "x"}})
    request, captured = _fake_request(model=model, tools=[write_file])

    config: dict = {"configurable": {}, "metadata": {}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler({}))

    assert set(captured) == {"model"}  # model swapped, tools untouched
    flagged = captured["model"]
    assert flagged is not model  # a copy…
    assert flagged.additional_model_request_fields == {
        "reasoningConfig": {"type": "x"},  # …that keeps the user's other request fields
        "anthropic_beta": [_BETA],
    }
    assert model.additional_model_request_fields == {"reasoningConfig": {"type": "x"}}
    assert write_file.extras is None  # the extras path is a direct-API-only mechanism


def test_bedrock_converse_appends_to_existing_betas_once(middleware):
    model = _converse_model(additional_model_request_fields={"anthropic_beta": ["other-beta"]})
    request, captured = _fake_request(model=model, tools=[_write_file_tool()])

    config: dict = {"configurable": {}, "metadata": {}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler({}))

    assert captured["model"].additional_model_request_fields["anthropic_beta"] == [
        "other-beta",
        _BETA,
    ]

    # Already flagged → nothing to change, no override at all.
    flagged = captured["model"]
    request2, captured2 = _fake_request(model=flagged, tools=[_write_file_tool()])
    received: dict = {}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request2, _fake_handler(received))
    assert captured2 == {}
    assert received["request"] is request2


def test_bedrock_converse_non_claude_model_is_left_alone(middleware):
    """The flag is Anthropic-specific — a Nova/Llama request must not carry it."""
    model = _converse_model("amazon.nova-pro-v1:0")
    request, captured = _fake_request(model=model, tools=[_write_file_tool()])
    received: dict = {}

    config: dict = {"configurable": {}, "metadata": {}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler(received))

    assert captured == {}
    assert received["request"] is request


def test_legacy_chat_bedrock_claude_gets_fine_grained_beta_in_model_kwargs(middleware):
    """Legacy InvokeModel path: `model_kwargs` is merged into the Anthropic request body."""
    model = _legacy_bedrock_model(model_kwargs={"max_tokens": 4096})
    request, captured = _fake_request(model=model, tools=[_write_file_tool()])

    config: dict = {"configurable": {}, "metadata": {}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler({}))

    flagged = captured["model"]
    assert flagged is not model
    # ChatBedrock hoists `max_tokens`/`temperature` out of `model_kwargs` at init, so only
    # the beta key is added; whatever else was there is preserved.
    assert flagged.model_kwargs == {**(model.model_kwargs or {}), "anthropic_beta": [_BETA]}
    assert "anthropic_beta" not in (model.model_kwargs or {})  # shared instance untouched


def test_bedrock_override_string_gets_fine_grained_beta(middleware):
    """`bedrock_converse:…` typed into Settings → the resolved override model is flagged."""
    request, captured = _fake_request(tools=[_write_file_tool()])
    bedrock_model = _converse_model()

    config = {
        "configurable": {"main_agent_model": "bedrock_converse:global.anthropic.claude-sonnet-5"},
        "metadata": {},
    }
    with (
        patch("backend.agents.career_agent.middleware.get_config", return_value=config),
        patch(
            "backend.agents.career_agent.middleware.init_chat_model",
            return_value=bedrock_model,
        ),
    ):
        middleware.wrap_model_call(request, _fake_handler({}))

    flagged = captured["model"]
    assert flagged is not bedrock_model
    assert flagged.additional_model_request_fields["anthropic_beta"] == [_BETA]
    assert not bedrock_model.additional_model_request_fields  # cached instance untouched


def test_bedrock_subagent_no_stream_copy_keeps_both_tweaks(middleware, monkeypatch):
    """Rollback lever on + Bedrock Claude subagent: one copy carries both changes."""
    monkeypatch.setattr(
        "backend.agents.career_agent.middleware.DISABLE_SUBAGENT_STREAMING",
        True,
    )
    model = _converse_model()
    request, captured = _fake_request(model=model, tools=[_write_file_tool()])

    config = {"configurable": {}, "metadata": {"lc_agent_name": "resume-tailor"}}
    with patch("backend.agents.career_agent.middleware.get_config", return_value=config):
        middleware.wrap_model_call(request, _fake_handler({}))

    flagged = captured["model"]
    assert flagged.disable_streaming is True
    assert flagged.additional_model_request_fields["anthropic_beta"] == [_BETA]
    assert model.disable_streaming is False
