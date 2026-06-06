"""Tests for the career-agent UTC date middleware."""

import re
from datetime import UTC, datetime, tzinfo
from types import SimpleNamespace
from typing import Self

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


_DATE_RE = re.compile(r"Current UTC date: \d{4}-\d{2}-\d{2}$")


def _content(msg: SystemMessage) -> str:
    return str(msg.content)


def test_middleware_appends_date_to_existing_system_prompt(middleware):
    request, captured = _fake_request("You are a career agent.")

    middleware.wrap_model_call(request, lambda r: r)

    content = _content(captured["system_message"])
    assert content.startswith("You are a career agent.")
    assert _DATE_RE.search(content)
    assert "Current UTC datetime:" not in content


def test_middleware_creates_message_when_system_prompt_is_none(middleware):
    request, captured = _fake_request(None)

    middleware.wrap_model_call(request, lambda r: r)

    assert _DATE_RE.search(_content(captured["system_message"]))


@pytest.mark.asyncio
async def test_middleware_async_path_also_injects(middleware):
    request, captured = _fake_request("hi")

    async def _passthrough(r):
        return r

    await middleware.awrap_model_call(request, _passthrough)

    content = _content(captured["system_message"])
    assert "hi" in content
    assert _DATE_RE.search(content)


def test_middleware_uses_same_date_for_different_times_on_same_day(middleware, monkeypatch):
    """Two same-day calls should keep the injected prompt line cacheable."""
    from backend.app.career_agent import middleware as middleware_module

    class _Datetime(datetime):
        calls = 0

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            cls.calls += 1
            hour = 3 if cls.calls == 1 else 21
            return cls(2026, 6, 6, hour, 38, 8, tzinfo=tz or UTC)

    monkeypatch.setattr(middleware_module, "datetime", _Datetime)

    request1, captured1 = _fake_request("x")
    middleware.wrap_model_call(request1, lambda r: r)
    request2, captured2 = _fake_request("x")
    middleware.wrap_model_call(request2, lambda r: r)

    assert _content(captured1["system_message"]) == _content(captured2["system_message"])
    assert _content(captured1["system_message"]).endswith("Current UTC date: 2026-06-06")


# Pre-built so the `raise` sites below carry no inline string literal (EM101/TRY003).
_STORE_DOWN = RuntimeError("store unavailable")


class _RecordingBackend:
    """Stand-in backend that records write/awrite calls.

    `fail=True` raises to mimic a store outage, so we can assert the middleware
    swallows it rather than crashing the run.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.writes: list[tuple[str, str]] = []

    def write(self, path: str, content: str):
        if self.fail:
            raise _STORE_DOWN
        self.writes.append((path, content))
        return SimpleNamespace(error=None, path=path)

    async def awrite(self, path: str, content: str):
        if self.fail:
            raise _STORE_DOWN
        self.writes.append((path, content))
        return SimpleNamespace(error=None, path=path)


def test_ensure_preferences_seeds_scaffold_when_missing():
    from backend.app.career_agent.middleware import (
        PREFERENCES_PATH,
        EnsurePreferencesFileMiddleware,
    )

    backend = _RecordingBackend()
    EnsurePreferencesFileMiddleware(backend).before_agent(state={}, runtime=None)

    assert len(backend.writes) == 1
    path, content = backend.writes[0]
    assert path == PREFERENCES_PATH
    assert content.startswith("# Saved preferences")
    assert "## Battlecard" in content  # section headings the model appends under


def test_ensure_preferences_swallows_backend_errors():
    from backend.app.career_agent.middleware import EnsurePreferencesFileMiddleware

    # A failing store write must never crash the agent run.
    EnsurePreferencesFileMiddleware(_RecordingBackend(fail=True)).before_agent(
        state={},
        runtime=None,
    )


@pytest.mark.asyncio
async def test_ensure_preferences_async_path_seeds():
    from backend.app.career_agent.middleware import (
        PREFERENCES_PATH,
        EnsurePreferencesFileMiddleware,
    )

    backend = _RecordingBackend()
    await EnsurePreferencesFileMiddleware(backend).abefore_agent(state={}, runtime=None)

    assert len(backend.writes) == 1
    assert backend.writes[0][0] == PREFERENCES_PATH
    assert backend.writes[0][1].startswith("# Saved preferences")
