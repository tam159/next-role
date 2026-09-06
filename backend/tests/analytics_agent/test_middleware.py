"""The access gate as the agent actually applies it.

Refusal has to happen before the model runs — that is what keeps an
unauthorized caller from reaching a tool that reads other users' activity.
"""

from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from backend.agents.analytics_agent import middleware as middleware_mod
from backend.agents.analytics_agent.access import REFUSAL
from backend.agents.analytics_agent.middleware import AnalyticsAccessMiddleware

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

# The gate reads the run config, not the runtime, so the hook never touches it.
_NO_RUNTIME = cast("Runtime", None)


def _run_gate(*, identity, email=None, allowed):
    """Drive `before_agent` with a patched identity and allowlist verdict."""
    with (
        patch.object(middleware_mod, "current_identity", return_value=identity),
        patch.object(
            middleware_mod,
            "current_user",
            return_value=type("U", (), {"email": email})() if email else None,
        ),
        patch.object(middleware_mod, "is_allowed", return_value=allowed) as gate,
    ):
        result = AnalyticsAccessMiddleware().before_agent({}, _NO_RUNTIME)
    return result, gate


def test_an_allowed_caller_proceeds():
    result, _ = _run_gate(identity="user-1", allowed=True)

    assert result is None


def test_a_denied_caller_ends_the_run_with_a_refusal():
    result, _ = _run_gate(identity="user-9", allowed=False)

    assert result is not None
    assert result["jump_to"] == "end"
    assert result["messages"][0].content == REFUSAL


def test_the_email_claim_reaches_the_allowlist():
    _, gate = _run_gate(identity="user-9", email="tam@example.com", allowed=True)

    gate.assert_called_once_with("user-9", "tam@example.com")


def test_a_caller_without_an_email_claim_passes_none():
    _, gate = _run_gate(identity="user-1", allowed=True)

    gate.assert_called_once_with("user-1", None)


async def test_the_async_hook_matches_the_sync_one():
    with (
        patch.object(middleware_mod, "current_identity", return_value="user-9"),
        patch.object(middleware_mod, "current_user", return_value=None),
        patch.object(middleware_mod, "is_allowed", return_value=False),
    ):
        result = await AnalyticsAccessMiddleware().abefore_agent({}, _NO_RUNTIME)

    assert result is not None
    assert result["jump_to"] == "end"


def test_the_hook_can_jump_to_end():
    """The jump is only honoured when the hook declares it."""
    for hook in (AnalyticsAccessMiddleware.before_agent, AnalyticsAccessMiddleware.abefore_agent):
        assert "end" in getattr(hook, "__can_jump_to__", [])
