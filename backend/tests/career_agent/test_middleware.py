"""Tests for the career-agent custom middleware (UTC date, preferences seeding)."""

import re
from datetime import UTC, datetime, tzinfo
from types import SimpleNamespace
from typing import Self

import pytest
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langchain_core.messages import SystemMessage
from langgraph.store.memory import InMemoryStore


@pytest.fixture
def middleware():
    from backend.agents.career_agent.middleware import UtcDatetimeMiddleware

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
    from backend.agents.career_agent import middleware as middleware_module

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


class _ExplodingBackend:
    """Stand-in backend where every call raises, mimicking a store outage."""

    def read(self, path: str):
        raise _STORE_DOWN

    async def aread(self, path: str):
        raise _STORE_DOWN

    def write(self, path: str, content: str):
        raise _STORE_DOWN

    async def awrite(self, path: str, content: str):
        raise _STORE_DOWN


def _memory_backend() -> CompositeBackend:
    """A real composite stack for `/memory/`, like production but in-memory.

    Exercises the same code path the agent uses (prefix routing into a
    StoreBackend), so it inherits deepagents' real write semantics — under
    0.7 `write()` overwrites, which is exactly what the regression tests below
    must observe.
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/memory/": StoreBackend(
                namespace=lambda _rt: ("test", "memory"),
                store=InMemoryStore(),
            ),
        },
    )


# A preferences file with real user content and deliberately WITHOUT the
# scaffold's other section headings, so a scaffold overwrite is detectable.
_SAVED_PREFERENCES = """# Saved preferences

## Research

- Always include the company's typical salary range.
"""


def test_ensure_preferences_seeds_scaffold_when_missing():
    from backend.agents.career_agent.middleware import (
        PREFERENCES_PATH,
        EnsurePreferencesFileMiddleware,
    )

    backend = _memory_backend()
    EnsurePreferencesFileMiddleware(backend).before_agent(state={}, runtime=None)

    result = backend.read(PREFERENCES_PATH)
    assert result.error is None
    assert result.file_data is not None
    content = result.file_data["content"]
    assert content.startswith("# Saved preferences")
    assert "## Battlecard" in content  # section headings the model appends under


def test_ensure_preferences_preserves_existing_content():
    """Regression: deepagents 0.7 `write()` overwrites, so seeding must probe first.

    Under 0.6 the backend refused to overwrite and the unconditional seed write
    was a harmless no-op; under 0.7 it would wipe saved preferences on every
    turn unless the middleware checks for the file before writing.
    """
    from backend.agents.career_agent.middleware import (
        PREFERENCES_PATH,
        EnsurePreferencesFileMiddleware,
    )

    backend = _memory_backend()
    assert backend.write(PREFERENCES_PATH, _SAVED_PREFERENCES).error is None

    middleware = EnsurePreferencesFileMiddleware(backend)
    middleware.before_agent(state={}, runtime=None)
    middleware.before_agent(state={}, runtime=None)  # every turn re-runs this hook

    result = backend.read(PREFERENCES_PATH)
    assert result.file_data is not None
    content = result.file_data["content"]
    assert "Always include the company's typical salary range." in content
    assert "## Battlecard" not in content  # scaffold did not replace user content


def test_ensure_preferences_swallows_backend_errors():
    from backend.agents.career_agent.middleware import EnsurePreferencesFileMiddleware

    # A failing store probe/write must never crash the agent run.
    EnsurePreferencesFileMiddleware(_ExplodingBackend()).before_agent(
        state={},
        runtime=None,
    )


@pytest.mark.asyncio
async def test_ensure_preferences_async_path_seeds():
    from backend.agents.career_agent.middleware import (
        PREFERENCES_PATH,
        EnsurePreferencesFileMiddleware,
    )

    backend = _memory_backend()
    await EnsurePreferencesFileMiddleware(backend).abefore_agent(state={}, runtime=None)

    result = await backend.aread(PREFERENCES_PATH)
    assert result.error is None
    assert result.file_data is not None
    assert result.file_data["content"].startswith("# Saved preferences")


@pytest.mark.asyncio
async def test_ensure_preferences_async_path_preserves_existing_content():
    """Async twin of the overwrite regression test."""
    from backend.agents.career_agent.middleware import (
        PREFERENCES_PATH,
        EnsurePreferencesFileMiddleware,
    )

    backend = _memory_backend()
    assert (await backend.awrite(PREFERENCES_PATH, _SAVED_PREFERENCES)).error is None

    middleware = EnsurePreferencesFileMiddleware(backend)
    await middleware.abefore_agent(state={}, runtime=None)
    await middleware.abefore_agent(state={}, runtime=None)

    result = await backend.aread(PREFERENCES_PATH)
    assert result.file_data is not None
    content = result.file_data["content"]
    assert "Always include the company's typical salary range." in content
    assert "## Battlecard" not in content


@pytest.mark.asyncio
async def test_ensure_preferences_async_swallows_backend_errors():
    from backend.agents.career_agent.middleware import EnsurePreferencesFileMiddleware

    await EnsurePreferencesFileMiddleware(_ExplodingBackend()).abefore_agent(
        state={},
        runtime=None,
    )
