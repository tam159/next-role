"""Tests for the career-agent UTC datetime middleware."""

import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage


@pytest.fixture
def middleware():
    from backend.app.career_agent.middleware import UtcDatetimeMiddleware

    return UtcDatetimeMiddleware()


def _fake_request(text: str | None):
    """A minimal stand-in for `ModelRequest` — just the bits the middleware reads."""
    captured = {}

    def _override(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(captured=captured)

    sm = SystemMessage(content=text) if text is not None else None
    return SimpleNamespace(system_message=sm, override=_override), captured


_DATETIME_RE = re.compile(
    r"Current UTC datetime: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?",
)


def _content(msg: SystemMessage) -> str:
    return str(msg.content)


def test_middleware_appends_datetime_to_existing_system_prompt(middleware):
    request, captured = _fake_request("You are a career agent.")

    middleware.wrap_model_call(request, lambda r: r)

    content = _content(captured["system_message"])
    assert content.startswith("You are a career agent.")
    assert _DATETIME_RE.search(content)


def test_middleware_creates_message_when_system_prompt_is_none(middleware):
    request, captured = _fake_request(None)

    middleware.wrap_model_call(request, lambda r: r)

    assert _DATETIME_RE.search(_content(captured["system_message"]))


@pytest.mark.asyncio
async def test_middleware_async_path_also_injects(middleware):
    request, captured = _fake_request("hi")

    async def _passthrough(r):
        return r

    await middleware.awrap_model_call(request, _passthrough)

    content = _content(captured["system_message"])
    assert "hi" in content
    assert _DATETIME_RE.search(content)


def test_middleware_produces_fresh_value_per_call(middleware):
    """Two consecutive calls should yield different timestamps (or at least not be cached)."""
    import time

    request1, captured1 = _fake_request("x")
    middleware.wrap_model_call(request1, lambda r: r)
    time.sleep(1.1)  # iso seconds precision — sleep over 1s to guarantee a different stamp
    request2, captured2 = _fake_request("x")
    middleware.wrap_model_call(request2, lambda r: r)

    assert _content(captured1["system_message"]) != _content(captured2["system_message"])
