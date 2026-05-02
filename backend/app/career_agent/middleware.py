"""Custom middleware for the career agent."""

from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage


class UtcDatetimeMiddleware(AgentMiddleware):
    """Append a fresh `Current UTC datetime: ...` line to the system message.

    Without this the agent has no clock and can't interpret `modified_at`
    timestamps from `list_files(...)` (e.g. tell "uploaded seconds ago" apart
    from "uploaded yesterday"). Injecting per call rather than at module
    import keeps the value accurate across long-lived deployments.
    """

    @staticmethod
    def _inject(request: Any) -> Any:  # noqa: ANN401  # ModelRequest is generic
        existing = request.system_message.text if request.system_message else ""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        new_content = f"{existing}\n\nCurrent UTC datetime: {now}".strip()
        return request.override(system_message=SystemMessage(content=new_content))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return handler(self._inject(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return await handler(self._inject(request))
