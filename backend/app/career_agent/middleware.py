"""Custom middleware for the career agent."""

import logging
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

logger = logging.getLogger(__name__)


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


# Module-level cache: `init_chat_model` builds a client each call (allocates
# network plumbing, reads env vars). The same one or two strings are reused
# across every node call in a run, so cache by the input string.
_MODEL_CACHE: dict[str, BaseChatModel] = {}


def _resolve_model(name: str) -> BaseChatModel | None:
    """Build (and memoize) a chat model from a `provider:model` string.

    Returns `None` on any failure — bad provider, unknown model, missing
    credentials — so the caller can fall back to the bake-time default
    instead of crashing the whole run on a user typo.
    """
    if cached := _MODEL_CACHE.get(name):
        return cached
    try:
        model = init_chat_model(name)
    except Exception:  # init_chat_model raises a mix
        logger.warning("ModelOverrideMiddleware: cannot init '%s'; falling back", name)
        return None
    _MODEL_CACHE[name] = model
    return model


class ModelOverrideMiddleware(AgentMiddleware):
    """Swap the request model based on runtime `configurable` values.

    Reads two keys from `RunnableConfig.configurable`:
      - `main_agent_model` — applies to the top-level career_agent call.
      - `subagent_model`   — applies to every declarative subagent call.

    The two are differentiated by `metadata.lc_agent_name`, which
    deepagents stamps onto each subagent's runnable (see
    `deepagents/middleware/subagents.py` → `with_config({"metadata":
    {"lc_agent_name": ...}})`). Absent → main agent.

    When the matching key is missing/empty or `init_chat_model` fails, the
    request passes through unchanged so the agent's bake-time default
    (`_MODEL` in `agents.py` for the main agent, `model:` in
    `subagents.yaml` for each subagent) still wins.
    """

    @staticmethod
    def _pick_override() -> str | None:
        try:
            config = get_config()
        except RuntimeError:
            # Called outside a runnable context (e.g. a unit test that
            # invokes the middleware directly). Nothing to override.
            return None
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        if metadata.get("lc_agent_name"):
            return configurable.get("subagent_model") or None
        return configurable.get("main_agent_model") or None

    @classmethod
    def _maybe_override(cls, request: Any) -> Any:  # noqa: ANN401
        name = cls._pick_override()
        if not name:
            return request
        model = _resolve_model(name)
        if model is None:
            return request
        return request.override(model=model)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return handler(self._maybe_override(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return await handler(self._maybe_override(request))
