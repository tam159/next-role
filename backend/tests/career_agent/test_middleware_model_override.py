"""Tests for the career-agent model-override middleware."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest


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


def _fake_request():
    """Minimal `ModelRequest` stand-in that records `override()` kwargs."""
    captured = {}

    def _override(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(captured=captured)

    return SimpleNamespace(override=_override), captured


def _fake_handler(received: dict):
    def _h(request):
        received["request"] = request
        return "OK"

    return _h


def test_main_agent_override_invokes_init_chat_model(middleware):
    request, captured = _fake_request()
    received: dict = {}
    fake_model = object()

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
    assert captured == {"model": fake_model}
    assert result == "OK"


def test_subagent_override_used_when_lc_agent_name_present(middleware):
    request, captured = _fake_request()
    received: dict = {}
    fake_model = object()

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
    assert captured == {"model": fake_model}


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
    fake_model = object()
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
    fake_model = object()

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

    assert captured == {"model": fake_model}
