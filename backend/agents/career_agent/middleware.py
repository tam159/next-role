"""Custom middleware for the career agent."""

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, convert_to_messages
from langgraph.config import get_config
from langgraph.types import Command

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


# Feature toggle (see PRD 16). Default `False`: subagents stream tokens live.
# The freeze that forced the original `True` default was an O(n^2) per-token
# chunk concat in the legacy `@langchain/langgraph-sdk` useStream path; the
# frontend now runs on `@langchain/react`'s v2 stream runtime (fragment
# accumulation + per-tick batched flushes), verified in-browser with
# `resume-tailor` + `interview-coach` streaming large tool-call args in
# parallel. Kept as a rollback lever: set `True` here — or per run via
# `configurable.disable_subagent_streaming` — to drop subagents back to
# per-step (whole-message) updates if a client-side freeze ever resurfaces.
DISABLE_SUBAGENT_STREAMING: bool = False


def _without_streaming(model: BaseChatModel) -> BaseChatModel:
    """Return a copy of `model` with token streaming disabled.

    Rollback path for subagent streaming (off by default since the
    `@langchain/react` migration — see `DISABLE_SUBAGENT_STREAMING`). When
    enabled, `disable_streaming` makes the model defer to `(a)invoke`, so
    LangGraph emits one complete message per step instead of token deltas —
    the historical mitigation for the legacy SDK's O(n^2) per-token concat
    under parallel large tool-call args.

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

    2. **Disable streaming for subagents (rollback lever).** When
       `DISABLE_SUBAGENT_STREAMING` is on (module default `False` since the
       `@langchain/react` migration; overridable per run via
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


# ---------------------------------------------------------------------------
# Subagent output mode: "full_history" support
# ---------------------------------------------------------------------------

# State keys shared by the recorder (runs inside each subagent) and the parent
# splicer. deepagents forwards any non-excluded state key from a finished
# subagent into the parent's state, which is the only sanctioned channel out
# of a subagent — its `messages` are deliberately dropped from the task
# result (deepagents' `_EXCLUDED_STATE_KEYS`), and the ToolMessage builder is
# a closure with no hook.


def _merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Reducer for the transcript state keys — parallel subagents each merge in."""
    return {**(left or {}), **(right or {})}


class SubagentTranscriptState(AgentState):
    """Agent state extension carrying subagent transcripts + their index.

    `subagent_transcripts` maps a task checkpoint namespace ("tools:<task-id>")
    to the subagent's serialized message list; `subagent_transcript_index` maps
    the parent's task `tool_call_id` to that namespace plus the subagent name.
    Both live in the parent's checkpointed state, so flipping the output mode
    later also applies to already-recorded threads.
    """

    subagent_transcripts: Annotated[NotRequired[dict[str, list[dict]]], _merge_dicts]
    subagent_transcript_index: Annotated[NotRequired[dict[str, dict]], _merge_dicts]


class SubagentTranscriptRecorder(AgentMiddleware):
    """Runs inside each subagent: exports its transcript into parent state.

    `after_agent` reads the finished subagent's own messages and returns them
    under `subagent_transcripts`, keyed by the subagent run's checkpoint
    namespace. deepagents' task tool forwards the key to the parent because it
    is not in its excluded-state set.
    """

    state_schema = SubagentTranscriptState

    @staticmethod
    def _export(state: Any) -> dict[str, Any] | None:  # noqa: ANN401  # AgentState is generic
        try:
            ns = get_config()["configurable"].get("checkpoint_ns") or ""
        except Exception:
            ns = ""
        # Inside a middleware hook the namespace carries the hook node's own
        # task segment ("tools:<task-id>|<hook-node>:<id>"); trim to the
        # subagent root so it matches the parent wrapper's index entry.
        ns = ns.split("|", 1)[0]
        if not ns:
            return None
        messages = [m.model_dump() for m in state.get("messages", [])]
        if not messages:
            return None
        return {"subagent_transcripts": {ns: messages}}

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Sync entry point."""
        return self._export(state)

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ANN401, ARG002
        """Async entry point."""
        return self._export(state)


def _transcript_messages(raw: list[dict], subagent_name: str | None) -> list[Any]:
    """Materialize a stored transcript for model context.

    Drops the leading task-brief HumanMessage (already present verbatim in the
    parent's `task` tool-call args) and stamps the subagent's name on its AI
    messages for attribution, mirroring supervisor-style history forwarding.
    """
    body = raw[1:] if raw and raw[0].get("type") == "human" else raw
    materialized = convert_to_messages(body)
    if subagent_name:
        for message in materialized:
            if message.type == "ai" and not message.name:
                message.name = subagent_name
    return materialized


def splice_subagent_transcripts(messages: list[Any], state: Any) -> list[Any]:  # noqa: ANN401
    """Insert recorded subagent transcripts after their turn's tool-response block.

    Placement matters for protocol validity: an assistant message that issued
    several parallel `task` calls must be followed by ALL of its ToolMessages
    before any other assistant message appears, so transcripts are inserted
    only after the contiguous ToolMessage block that answers the turn — never
    between sibling tool responses.

    Pure function over the request's message list; the parent's stored history
    is never mutated, so the mode can be toggled at any time.
    """
    transcripts = state.get("subagent_transcripts") or {}
    index = state.get("subagent_transcript_index") or {}
    if not transcripts or not index:
        return messages

    spliced: list[Any] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        spliced.append(message)
        i += 1
        task_call_ids = [
            call["id"]
            for call in (getattr(message, "tool_calls", None) or [])
            if call.get("name") == "task" and call.get("id") in index
        ]
        if not task_call_ids:
            continue
        # Copy the turn's full contiguous tool-response block first.
        while i < len(messages) and getattr(messages[i], "type", None) == "tool":
            spliced.append(messages[i])
            i += 1
        for call_id in task_call_ids:
            entry = index.get(call_id) or {}
            ns = entry.get("ns") or ""
            # Prefix-tolerant lookup: transcripts recorded before the
            # namespace-trim fix are keyed with a trailing hook segment.
            raw = transcripts.get(ns) or next(
                (v for k, v in transcripts.items() if ns and k.startswith(f"{ns}|")),
                None,
            )
            if raw:
                spliced.extend(_transcript_messages(raw, entry.get("name")))
    return spliced


class SubagentFullHistoryMiddleware(AgentMiddleware):
    """Parent-side half of `SUBAGENT_OUTPUT_MODE = "full_history"`.

    Mirrors `langgraph-supervisor`'s `output_mode="full_history"`: follow-up
    model calls see each subagent's real internal messages (AI tool_calls and
    tool results as structured message objects — never prose describing them,
    which could teach the model to imitate tool syntax instead of calling
    tools). `wrap_tool_call` records which task tool_call maps to which
    subagent namespace; `wrap_model_call` splices the recorded transcripts
    into the request ephemerally.
    """

    state_schema = SubagentTranscriptState

    @staticmethod
    def _index_update(request: Any, result: Any) -> Any:  # noqa: ANN401
        if request.tool_call.get("name") != "task":
            return result
        try:
            task_id = get_config()["configurable"].get("__pregel_task_id")
        except Exception:
            task_id = None
        if not task_id:
            return result
        entry = {
            "ns": f"tools:{task_id}",
            "name": (request.tool_call.get("args") or {}).get("subagent_type"),
        }
        index = {"subagent_transcript_index": {request.tool_call["id"]: entry}}
        if isinstance(result, Command):
            return dataclasses.replace(result, update={**(result.update or {}), **index})
        return Command(update={"messages": [result], **index})

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return self._index_update(request, handler(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return self._index_update(request, await handler(request))

    @staticmethod
    def _spliced(request: Any) -> Any:  # noqa: ANN401
        return request.override(
            messages=splice_subagent_transcripts(request.messages, request.state),
        )

    def wrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Sync entry point."""
        return handler(self._spliced(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:  # noqa: ANN401
        """Async entry point."""
        return await handler(self._spliced(request))
