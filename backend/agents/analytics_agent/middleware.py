"""Middleware specific to the analytics agent.

Only one, and it exists because a refusal must happen before the model runs:
the point of the gate is that a caller without access never reaches a tool that
reads other people's data, and never spends a token trying.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.agents.analytics_agent.access import REFUSAL, is_allowed
from backend.agents.career_agent.scope import current_identity, current_user
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage

if TYPE_CHECKING:
    from langgraph.runtime import Runtime


class AnalyticsAccessMiddleware(AgentMiddleware):
    """End the run with a refusal when the caller is not on the allowlist.

    `before_agent` runs once per invocation, ahead of the model, and jumps
    straight to the end — so an unauthorized caller gets one clear sentence
    instead of a tool error, and the warehouse is never queried on their behalf.
    """

    name = "AnalyticsAccessMiddleware"

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: Any, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Allow the run, or replace it with a refusal message."""
        return _gate()

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: Any, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Async twin of `before_agent` (the server runs the async path)."""
        return _gate()


def _gate() -> dict[str, Any] | None:
    """Return `None` to proceed, or the terminal refusal state."""
    user = current_user()
    email = getattr(user, "email", None) if user is not None else None
    if is_allowed(current_identity(), email):
        return None
    return {"messages": [AIMessage(content=REFUSAL)], "jump_to": "end"}
