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
    """Append a `Current UTC date: ...` line to the system message.

    Without this the agent has no clock and can't interpret `modified_at`
    timestamps from `list_files(...)` (e.g. tell "uploaded yesterday" apart
    from "uploaded last month"). Injecting per call rather than at module
    import keeps the value accurate across long-lived deployments, while date
    precision avoids invalidating prompt caches on every turn.
    """

    @staticmethod
    def _inject(request: Any) -> Any:  # noqa: ANN401  # ModelRequest is generic
        existing = request.system_message.text if request.system_message else ""
        today = datetime.now(UTC).date().isoformat()
        new_content = f"{existing}\n\nCurrent UTC date: {today}".strip()
        return request.override(system_message=SystemMessage(content=new_content))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return handler(self._inject(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return await handler(self._inject(request))


# Path of the always-loaded preferences file (a StoreBackend route; the same
# string is wired as a `memory=` source in agents.py).
PREFERENCES_PATH = "/memory/preferences.md"

# Scaffold written when the preferences file is absent. The section headings
# give the model an obvious place to append each preference, and let it pull the
# right ones per stage when delegating to subagents.
_PREFERENCES_SCAFFOLD = """# Saved preferences

Standing preferences for how to prepare this user's materials. Apply the relevant
ones on every run, and fold them into subagent task descriptions.

## Research

## Tailored resume

## Interview prep

## Battlecard

## General
"""


class EnsurePreferencesFileMiddleware(AgentMiddleware):
    """Guarantee the always-loaded preferences file exists before the model runs.

    gpt-5.4 reliably *appends* a preference to `/memory/preferences.md` when the
    file already exists, but won't reliably *create* it on a clean slate — it
    fumbles toward AGENTS.md or starts the intake workflow instead. So we seed
    the scaffold here in `before_agent`: a single cheap store write — no model
    round trip, so no added response latency — and idempotent, because the
    backend's `write` refuses to overwrite an existing file. This keeps the
    preferences memory source present on every deployment without relying on the
    LLM to bootstrap it.

    Main-agent only — subagents have no memory and never touch this file.
    """

    def __init__(self, backend: Any) -> None:  # noqa: ANN401  # CompositeBackend
        """Capture the backend used to seed the preferences scaffold."""
        self._backend = backend

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Seed the scaffold if missing (sync invocation path)."""
        try:
            self._backend.write(PREFERENCES_PATH, _PREFERENCES_SCAFFOLD)
        except Exception:  # never break a run over preference seeding
            logger.debug("ensure preferences file (sync) skipped", exc_info=True)

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Seed the scaffold if missing (async invocation path)."""
        try:
            await self._backend.awrite(PREFERENCES_PATH, _PREFERENCES_SCAFFOLD)
        except Exception:  # never break a run over preference seeding
            logger.debug("ensure preferences file (async) skipped", exc_info=True)


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


# Feature toggle (see PRD 16). Subagents don't stream tokens by default, because
# parallel subagents streaming large tool-call args trigger an O(n^2) chunk
# concat in the LangGraph SDK and hang the client. Flip to `False` — here for a
# global default, or per run via `configurable.disable_subagent_streaming` — to
# restore live subagent token streaming once the frontend/SDK is fixed for good.
DISABLE_SUBAGENT_STREAMING: bool = True


def _without_streaming(model: BaseChatModel) -> BaseChatModel:
    """Return a copy of `model` with token streaming disabled.

    Subagents must not stream tokens: when two run in parallel and both emit a
    large tool-call argument (e.g. `overwrite_file`'s `new_content`), the
    LangGraph SDK's per-token `MessageTupleManager.concat` is O(n^2) and hangs
    the client. `disable_streaming` makes the model defer to `(a)invoke`, so
    LangGraph emits one complete message per step instead of token deltas.

    `model_copy` leaves the shared/cached instance untouched — the main agent
    may use the same `provider:model` string (`openai:gpt-5.4` is shared with
    `resume-tailor`) and must keep streaming.
    """
    try:
        return model.model_copy(update={"disable_streaming": True})
    except Exception:  # never break a real run over a streaming tweak
        logger.warning("could not disable streaming on %r; leaving as-is", model)
        return model


class ModelOverrideMiddleware(AgentMiddleware):
    """Shape the request model per main-agent-vs-subagent context.

    Two responsibilities, both keyed off whether the call belongs to a subagent:

    1. **Model override.** Reads two `RunnableConfig.configurable` keys:
         - `main_agent_model` — applies to the top-level career_agent call.
         - `subagent_model`   — applies to every declarative subagent call.
       When the matching key is missing/empty or `init_chat_model` fails, the
       bake-time default (`_MODEL` in `agents.py`; `model:` in `subagents.yaml`)
       still wins.

    2. **Disable streaming for subagents.** When `DISABLE_SUBAGENT_STREAMING`
       is on (module default; overridable per run via
       `configurable.disable_subagent_streaming`), every subagent call (override
       or default model) gets `disable_streaming=True` so parallel subagents
       emitting large tool-call args don't flood the client (see
       `_without_streaming`). The main agent always keeps streaming.

    Subagent vs. main is differentiated by `metadata.lc_agent_name`, which
    deepagents stamps onto each subagent's runnable (see
    `deepagents/middleware/subagents.py` → `with_config({"metadata":
    {"lc_agent_name": ...}})`). Absent → main agent.
    """

    @staticmethod
    def _read_config() -> tuple[bool, str | None, bool]:
        """Return `(is_subagent, model_name_override, disable_subagent_streaming)`."""
        try:
            config = get_config()
        except RuntimeError:
            # Called outside a runnable context (e.g. a unit test that invokes
            # the middleware directly). Treat as main agent, no override, no-op.
            return False, None, False
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        disable_streaming = configurable.get("disable_subagent_streaming")
        if disable_streaming is None:  # not set per run → fall back to the module default
            disable_streaming = DISABLE_SUBAGENT_STREAMING
        if metadata.get("lc_agent_name"):
            return True, configurable.get("subagent_model") or None, bool(disable_streaming)
        return False, configurable.get("main_agent_model") or None, bool(disable_streaming)

    @classmethod
    def _maybe_override(cls, request: Any) -> Any:  # noqa: ANN401
        is_subagent, name, disable_streaming = cls._read_config()
        model = _resolve_model(name) if name else None  # None → keep request's default
        if is_subagent and disable_streaming:
            # Disable streaming on whichever model the subagent ends up using.
            base = model if model is not None else request.model
            return request.override(model=_without_streaming(base))
        if model is not None:
            return request.override(model=model)
        return request

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return handler(self._maybe_override(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return await handler(self._maybe_override(request))
