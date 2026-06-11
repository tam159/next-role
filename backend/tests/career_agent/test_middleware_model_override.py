"""Tests for the career-agent model-override middleware."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest


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
    from backend.app.career_agent.middleware import ModelOverrideMiddleware

    return ModelOverrideMiddleware()


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """Reset the module-level model cache between tests."""
    from backend.app.career_agent import middleware as mw

    mw._MODEL_CACHE.clear()  # noqa: SLF001
    yield
    mw._MODEL_CACHE.clear()  # noqa: SLF001


def _fake_request(model=None):
    """Minimal `ModelRequest` stand-in that records `override()` kwargs."""
    captured = {}

    def _override(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(captured=captured)

    return SimpleNamespace(model=model or _FakeModel(), override=_override), captured


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
        "configurable": {"main_agent_model": "anthropic:claude-sonnet-4.6"},
        "metadata": {},
    }
    with (
        patch(
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        result = middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("anthropic:claude-sonnet-4.6")
    # Main agent: plain swap, streaming left intact (no model_copy).
    assert captured["model"] is fake_model
    assert fake_model.disable_streaming is False
    assert result == "OK"


def test_subagent_override_disables_streaming_when_default_on(middleware, monkeypatch):
    """With the rollback default flipped on, a subagent override gets a no-stream copy."""
    monkeypatch.setattr(
        "backend.app.career_agent.middleware.DISABLE_SUBAGENT_STREAMING",
        True,
    )
    request, captured = _fake_request()
    received: dict = {}
    fake_model = _FakeModel(name="subagent-override")

    config = {
        "configurable": {
            "main_agent_model": "anthropic:claude-sonnet-4.6",
            "subagent_model": "openai:gpt-5.4-mini",
        },
        "metadata": {"lc_agent_name": "hiring-recon"},
    }
    with (
        patch(
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("openai:gpt-5.4-mini")
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
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
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
        patch("backend.app.career_agent.middleware.get_config", return_value=config),
        patch("backend.app.career_agent.middleware.init_chat_model") as mocked_init,
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
            "subagent_model": "openai:gpt-5.4-mini",
            "disable_subagent_streaming": False,
        },
        "metadata": {"lc_agent_name": "hiring-recon"},
    }
    with (
        patch("backend.app.career_agent.middleware.get_config", return_value=config),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("openai:gpt-5.4-mini")
    assert captured["model"] is fake_model  # plain override, not a streaming-disabled copy
    assert fake_model.disable_streaming is False


def test_module_default_on_disables_subagent_streaming(middleware, monkeypatch):
    """Flipping the `DISABLE_SUBAGENT_STREAMING` module default back on is the rollback."""
    monkeypatch.setattr(
        "backend.app.career_agent.middleware.DISABLE_SUBAGENT_STREAMING",
        True,
    )
    req_model = _FakeModel(name="subagent-default")
    request, captured = _fake_request(model=req_model)
    received: dict = {}

    config = {"configurable": {}, "metadata": {"lc_agent_name": "resume-tailor"}}
    with (
        patch("backend.app.career_agent.middleware.get_config", return_value=config),
        patch("backend.app.career_agent.middleware.init_chat_model") as mocked_init,
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
        patch("backend.app.career_agent.middleware.get_config", return_value=config),
        patch("backend.app.career_agent.middleware.init_chat_model") as mocked_init,
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
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
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
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
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
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
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
        "configurable": {"main_agent_model": "openai:gpt-5.4"},
        "metadata": {},
    }
    with (
        patch(
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ) as mocked_init,
    ):
        for _ in range(3):
            request, _captured = _fake_request()
            middleware.wrap_model_call(request, _fake_handler(received))

    mocked_init.assert_called_once_with("openai:gpt-5.4")


def test_get_config_outside_runnable_context_is_safe(middleware):
    request, captured = _fake_request()
    received: dict = {}

    with patch(
        "backend.app.career_agent.middleware.get_config",
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
        "configurable": {"main_agent_model": "openai:gpt-5.4"},
        "metadata": {},
    }
    with (
        patch(
            "backend.app.career_agent.middleware.get_config",
            return_value=config,
        ),
        patch(
            "backend.app.career_agent.middleware.init_chat_model",
            return_value=fake_model,
        ),
    ):
        await middleware.awrap_model_call(request, _async_handler)

    assert captured["model"] is fake_model
    assert fake_model.disable_streaming is False
